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
from core import load_bars, compute_panel, comment
from live import market_open, live_price, TW_TZ
from inst import get_inst
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
        return jsonify(error=str(e)), 404
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
        return jsonify(error=str(e)), 404

    live = False
    live_time = None
    if market_open():
        price, chg = live_price(sid)
        if price:
            # 即時價當「今日臨時K」接在歷史後重算（量沿用昨日，標註非今日量）
            last = bars[-1]
            bars = bars + [{
                "date": "live", "open": price,
                "high": max(price, last["close"]), "low": min(price, last["close"]),
                "close": price, "volume": last["volume"],
            }]
            live = True
            live_time = datetime.now(TW_TZ).strftime("%H:%M")

    p = compute_panel(bars)
    if live:
        p["date"] = bars[-2]["date"] + " +即時"
        p["vol_note"] = "量能為昨日值（盤中累積量未計）"
    p["sid"] = sid
    p["live"] = live
    p["live_time"] = live_time
    p["hist_bucket"] = bucket_of(p["vp_score"])
    try:
        p["inst"] = get_inst(sid)   # 三大法人(上市 T86)；上櫃/未列 None
    except Exception:
        p["inst"] = None
    p["comment"] = comment(p)
    return jsonify(p)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8771, debug=False)
