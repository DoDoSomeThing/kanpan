#!/usr/bin/env python3
"""
kanpan api — 本機後端（給 kanpan Chrome 擴充呼叫）

跑：python api.py  → http://127.0.0.1:8771
端點：
  GET /panel?sid=2330  → kanpan 面板 JSON（VP Score / 歷史統計 / 四區塊 / 評語）
  GET /health          → {"ok": true}

盤中(9:00-13:30)自動套 TWSE MIS 即時價；非盤中照日線收盤。
kanpan 自含：只用本 repo 的 core/live/research，cache 放 kanpan/cache。
"""
import os
import re
import sys
import json

from flask import Flask, jsonify, request
from flask_cors import CORS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from core import load_bars, compute_panel, comment, verdict, data_freshness, consistency_check
from live import market_open, live_quote, TW_TZ
from inst import get_inst, consensus
from position import (load_positions, position_risk, open_position,
                      close_position)
from datetime import datetime

CACHE = os.path.join(HERE, "cache", "kline_cache.json.gz")
STATS = os.path.join(HERE, "research", "score_stats.json")

app = Flask(__name__)
CORS(app)

_stats = json.load(open(STATS, encoding="utf-8")) if os.path.exists(STATS) else None


def bucket_of(score):
    if not _stats:
        return None
    for b in _stats["buckets"]:
        if b["lo"] <= score <= b["hi"]:
            return {**b, "period": _stats["period"]}
    return None


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/ohlc")
def ohlc_ep():
    """給 lightweight-charts 畫 K 線用（自畫，不靠 TradingView widget）。
    回 {candles:[{time,open,high,low,close}], volumes:[{time,value,color}]}。"""
    sid = (request.args.get("sid") or "").strip().upper()
    if not re.fullmatch(r"[0-9]{4,6}[A-Z]?", sid):
        return jsonify(error="sid 格式錯"), 400
    days = request.args.get("days", default=250, type=int)
    try:
        bars = load_bars(sid, CACHE)
    except KeyError as e:
        return jsonify(error=(e.args[0] if e.args else str(e))), 404
    bars = bars[-max(60, min(days, 600)):]
    candles, volumes = [], []
    for b in bars:
        t = b["date"]
        candles.append({"time": t, "open": b["open"], "high": b["high"],
                        "low": b["low"], "close": b["close"]})
        up = b["close"] >= b["open"]
        volumes.append({"time": t, "value": b["volume"],
                        "color": "rgba(226,72,58,.5)" if up else "rgba(38,166,154,.5)"})
    return jsonify(sid=sid, candles=candles, volumes=volumes)


@app.get("/panel")
def panel_ep():
    sid = (request.args.get("sid") or "").strip().upper()
    if not re.fullmatch(r"[0-9]{4,6}[A-Z]?", sid):
        return jsonify(error="sid 格式錯，例 2330 / 0050"), 400
    if not os.path.exists(CACHE):
        return jsonify(error="無 K 線 cache，先跑 data/fetch_data.py cache"), 500
    try:
        bars = load_bars(sid, CACHE)
    except KeyError as e:
        return jsonify(error=(e.args[0] if e.args else str(e))), 404

    live = False
    live_time = None
    vol_real = False
    if market_open():
        q = live_quote(sid)
        if q and q["price"]:
            # 即時報價當「今日臨時K」接在歷史後重算。有 MIS 真開高低量就用真的(量能/CCP/乖離即時)
            last = bars[-1]
            price = q["price"]
            vlots = q.get("vol_lots")
            vol_real = bool(vlots and vlots > 0)
            bars = bars + [{
                "date": "live",
                "open": q.get("open") or price,
                "high": q.get("high") or max(price, last["close"]),
                "low": q.get("low") or min(price, last["close"]),
                "close": price,
                "volume": (vlots * 1000) if vol_real else last["volume"],  # 張→股 對齊 cache
            }]
            live = True
            live_time = datetime.now(TW_TZ).strftime("%H:%M")

    p = compute_panel(bars)
    if live:
        p["date"] = bars[-2]["date"] + " +即時"
        p["vol_note"] = ("盤中累積量(即時)" if vol_real
                         else "量能為昨日值（盤中累積量未取得）")
    p["sid"] = sid
    p["live"] = live
    p["live_time"] = live_time
    b = bucket_of(p["vp_score"])
    p["hist_bucket"] = b
    try:
        p["inst"] = get_inst(sid)   # 三大法人(上市 T86)；上櫃/未列 None
    except Exception:
        p["inst"] = None
    # 功能2 法人共識(背離)：先算好再進 verdict，net 才計入
    tv = bars[-1]["volume"] / 1000 if bars and bars[-1].get("volume") else None
    p["inst_consensus"] = consensus(p["inst"], total_vol=tv) if p.get("inst") else None
    # 功能A：法人資料源新鮮度（盤中即時棒不影響法人，需各自比對）
    p["inst_fresh"] = data_freshness(p["inst"]["date"]) if p.get("inst") else None
    # 功能七：各資料源 vs 基準 bar 一致性
    p["consistency"] = consistency_check(p.get("ref_date"), p.get("inst"))
    p["verdict"] = verdict(p, b["win20"] if b and b.get("n", 0) > 0 else None)
    p["comment"] = comment(p)
    # L3 持倉風控（V2 Phase 1）：該檔有持倉才回，順手累積 peak
    try:
        cur = bars[-1]["close"]
        hi = bars[-1].get("high")
        p["position"] = position_risk(sid, cur, today_high=hi)
    except Exception:
        p["position"] = None
    return jsonify(p)


@app.route("/position", methods=["GET", "POST"])
def position_ep():
    """GET ?sid=2356 → 該檔風控；GET 無 sid → 列所有持倉風控。
    POST {action:open|close, ...} → 開/平倉。"""
    if request.method == "GET":
        sid = (request.args.get("sid") or "").strip().upper()
        if sid:
            if not re.fullmatch(r"[0-9]{4,6}[A-Z]?", sid):
                return jsonify(error="sid 格式錯"), 400
            cur, hi = _cur_price_high(sid)
            if cur is None:
                return jsonify(error="無 K 線 cache 或查無此檔"), 404
            r = position_risk(sid, cur, today_high=hi)
            if not r:
                return jsonify(sid=sid, position=None)
            return jsonify(sid=sid, position=r)
        # 無 sid → 列全部
        d = load_positions()
        out = []
        for s in list(d["open"].keys()):
            cur, hi = _cur_price_high(s)
            if cur is None:
                continue
            out.append(position_risk(s, cur, today_high=hi))
        return jsonify(open=out, closed=d["closed"])

    # POST：開 / 平倉
    body = request.get_json(silent=True) or {}
    action = (body.get("action") or "").lower()
    sid = (body.get("sid") or "").strip().upper()
    if not re.fullmatch(r"[0-9]{4,6}[A-Z]?", sid or ""):
        return jsonify(error="sid 格式錯"), 400
    try:
        if action == "open":
            pos = open_position(sid, body["entry_price"], body["shares"],
                                body.get("entry_date"), body.get("note", ""))
            return jsonify(ok=True, sid=sid, position=pos)
        elif action == "close":
            rec = close_position(sid, body["exit_price"],
                                 body.get("exit_date"),
                                 body.get("exit_reason", "manual"))
            return jsonify(ok=True, sid=sid, closed=rec)
        return jsonify(error="action 需為 open / close"), 400
    except (KeyError, ValueError) as e:
        return jsonify(error=str(e)), 400


def _cur_price_high(sid):
    """取某檔現價與今日 high：盤中用 MIS 即時，否則 cache 最新收盤。"""
    if not os.path.exists(CACHE):
        return None, None
    try:
        bars = load_bars(sid, CACHE)
    except KeyError:
        return None, None
    last = bars[-1]
    cur, hi = last["close"], last.get("high")
    if market_open():
        q = live_quote(sid)
        if q and q.get("price"):
            cur = q["price"]
            hi = max(q.get("high") or cur, hi or cur)
    return cur, hi


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8771, debug=False)
