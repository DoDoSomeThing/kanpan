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
from core import (load_bars, compute_panel, comment, verdict, data_freshness,
                  consistency_check, state_layer, wilson_ci)
from live import market_open, live_quote, TW_TZ
from inst import get_inst, consensus
from playbook import detect_playbook, playbook_view, load_stats

_PB_STATS = load_stats()    # L2 劇本回測結果，啟動時載一次
from position import (load_positions, position_risk, open_position,
                      close_position, attach_alpha, closed_with_alpha,
                      _load_bench)
import portfolio as PF
import behavior as BH
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
            # P4：勝率桶加 20 日勝率 Wilson 95% CI（N 已在 b["n"]）
            ci = wilson_ci(b.get("win20"), b.get("n"))
            return {**b, "period": _stats["period"],
                    "win20_ci": list(ci) if ci else None}
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
    p["state_layer"] = state_layer(p)   # L1 狀態層(需 inst_consensus 已併入)
    # L2 規則化劇本：判最後一根『已收盤』K(live 時取 -2)觸發哪些模板 + 回測 + 防呆
    pb_idx = -2 if live else -1
    p["playbook"] = playbook_view(detect_playbook(bars, pb_idx), _PB_STATS)
    p["comment"] = comment(p)
    # L3 持倉風控（V2 Phase 1）：該檔有持倉才回，順手累積 peak
    try:
        cur = bars[-1]["close"]
        hi = bars[-1].get("high")
        p["position"] = attach_alpha(position_risk(sid, cur, today_high=hi),
                                     _load_bench(CACHE))
    except Exception:
        p["position"] = None
    # P3 行為守門：追高（無倉，單股）/ 凹單（破生效未出，單股）/
    #   頻率（近 30 日過度交易，**全域跨檔**：餵全部 closed，非只本檔）
    try:
        _d = load_positions()
        p["behavior"] = BH.behavior_checks(
            p=p, risk=p.get("position"), closed_records=_d["closed"])
    except Exception:
        p["behavior"] = []
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
            r = attach_alpha(position_risk(sid, cur, today_high=hi),
                             _load_bench(CACHE))
            if not r:
                return jsonify(sid=sid, position=None)
            return jsonify(sid=sid, position=r)
        # 無 sid → 列全部
        d = load_positions()
        bench = _load_bench(CACHE)
        out = []
        for s in list(d["open"].keys()):
            cur, hi = _cur_price_high(s)
            if cur is None:
                continue
            out.append(attach_alpha(position_risk(s, cur, today_high=hi), bench))
        return jsonify(open=out, closed=closed_with_alpha(d["closed"], bench))

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


@app.get("/portfolio")
def portfolio_ep():
    """組合層視圖（P1.5 + P2）：
      cumulative  累計超額 α（所有平倉 vs 同期 0050）
      correlation 持倉兩兩相關係數（揭露假分散）
      high_corr   相關 >=0.7 的對
      exposure    各檔曝險權重（?cash=現金元 選填 → 加投資比）
    無持倉時 correlation/exposure 空，cumulative 仍回（看歷史對帳）。"""
    d = load_positions()
    bench = _load_bench(CACHE)
    cumulative = PF.cumulative_alpha(closed_with_alpha(d["closed"], bench))

    holdings, bars_map = [], {}
    for s in list(d["open"].keys()):
        cur, _hi = _cur_price_high(s)
        if cur is None:
            continue
        holdings.append({"sid": s, "shares": d["open"][s].get("shares"),
                         "price": cur})
        try:
            bars_map[s] = load_bars(s, CACHE)
        except KeyError:
            pass

    matrix = PF.correlation_matrix(bars_map) if len(bars_map) >= 2 else []
    cash = request.args.get("cash", type=float)
    return jsonify(
        cumulative=cumulative,
        correlation=matrix,
        high_corr=PF.high_corr_pairs(matrix),
        exposure=PF.exposure(holdings, cash=cash),
    )


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
