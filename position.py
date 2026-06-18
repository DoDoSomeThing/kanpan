#!/usr/bin/env python3
"""
kanpan position — L3 持倉風控（V2 Phase 1）

只做風控，不預測。命中已驗證的真 edge：trail 出場（−4% 硬停損 / 高點回落 8%）。

資料：positions.json（本機、gitignore）。多檔（open key = sid）。
  - peak_price 跨日累積 max(歷史 peak, 今日 high)，每次刷新寫回。
  - 出場時把該檔從 open 搬進 closed 陣列（留歷史供自我檢討）。

CLI：
  python position.py list                          # 列所有持倉 + 風控
  python position.py show 2356                      # 單檔風控（用 cache 最新價）
  python position.py open 2356 68.6 0.5 [--date YYYY-MM-DD] [--note 文字]
  python position.py close 2356 67.3 [--reason trail] [--date YYYY-MM-DD]
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
POS_PATH = os.path.join(HERE, "positions.json")

HARD_STOP_PCT = 0.04   # 硬停損：entry × (1 − 4%)，固定從進場價算
TRAIL_PCT = 0.08       # Trail：peak × (1 − 8%)，高點回落保護
NEAR_PCT = 2.0         # 距觸發 0~2% = 🟡 接近


def _today():
    try:
        from live import TW_TZ
        return datetime.now(TW_TZ).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


# ---------- 讀寫 ----------
def load_positions():
    if not os.path.exists(POS_PATH):
        return {"open": {}, "closed": []}
    with open(POS_PATH, encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("open", {})
    d.setdefault("closed", [])
    return d


def save_positions(d):
    with open(POS_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


# ---------- 風控計算（純函數，給 test 釘） ----------
def compute_risk(entry_price, peak_price, cur_price):
    """回風控 dict。effective_exit = max(硬停損, Trail)，面板只秀這條。"""
    hard_stop = round(entry_price * (1 - HARD_STOP_PCT), 2)
    trail_stop = round(peak_price * (1 - TRAIL_PCT), 2)
    if trail_stop >= hard_stop:
        effective, by = trail_stop, "Trail"
    else:
        effective, by = hard_stop, "硬停損"
    unreal_pct = round((cur_price - entry_price) / entry_price * 100, 1)
    dist_pct = round((cur_price - effective) / cur_price * 100, 1)
    # 狀態燈：🔴 已觸發(現價 ≤ 生效)｜🟡 接近(0~2%)｜🟢 正常(>2%)
    if cur_price <= effective:
        light, state = "🔴", "已觸發"
    elif dist_pct <= NEAR_PCT:
        light, state = "🟡", "接近"
    else:
        light, state = "🟢", "正常持有"
    return {
        "entry_price": entry_price,
        "peak_price": peak_price,
        "cur_price": cur_price,
        "unreal_pct": unreal_pct,
        "hard_stop": hard_stop,
        "trail_stop": trail_stop,
        "effective_exit": effective,
        "effective_by": by,
        "dist_pct": dist_pct,
        "light": light,
        "state": state,
    }


def position_risk(sid, cur_price, today_high=None, d=None, persist=True):
    """查某檔持倉風控。有 today_high 則累積更新 peak 並寫回（跨日持久化）。
    無持倉回 None。"""
    own = d is None
    if d is None:
        d = load_positions()
    pos = d["open"].get(sid)
    if not pos:
        return None
    # peak 跨日累積：max(歷史 peak, 今日 high)
    peak = pos.get("peak_price") or pos["entry_price"]
    if today_high is not None and today_high > peak:
        peak = round(today_high, 2)
        pos["peak_price"] = peak
        if persist and own:
            save_positions(d)
    r = compute_risk(pos["entry_price"], peak, cur_price)
    r.update({
        "sid": sid,
        "shares": pos.get("shares"),
        "entry_date": pos.get("entry_date"),
        "note": pos.get("note", ""),
    })
    return r


# ---------- 開 / 平倉 ----------
def open_position(sid, entry_price, shares, entry_date=None, note=""):
    d = load_positions()
    if sid in d["open"]:
        raise ValueError(f"{sid} 已有持倉，先平倉再開（或手動改 positions.json）")
    d["open"][sid] = {
        "entry_price": round(float(entry_price), 2),
        "shares": float(shares),
        "entry_date": entry_date or _today(),
        "peak_price": round(float(entry_price), 2),   # 初始 peak = 進場價
        "note": note or "",
    }
    save_positions(d)
    return d["open"][sid]


def close_position(sid, exit_price, exit_date=None, exit_reason="manual"):
    d = load_positions()
    pos = d["open"].get(sid)
    if not pos:
        raise ValueError(f"{sid} 無持倉")
    exit_price = round(float(exit_price), 2)
    exit_date = exit_date or _today()
    entry = pos["entry_price"]
    return_pct = round((exit_price - entry) / entry * 100, 1)
    hold_days = _days_between(pos.get("entry_date"), exit_date)
    rec = {
        "sid": sid,
        "entry_price": entry,
        "exit_price": exit_price,
        "entry_date": pos.get("entry_date"),
        "exit_date": exit_date,
        "exit_reason": exit_reason,
        "return_pct": return_pct,
        "hold_days": hold_days,
    }
    d["closed"].append(rec)
    del d["open"][sid]
    save_positions(d)
    return rec


def _days_between(d1, d2):
    try:
        a = datetime.strptime(d1, "%Y-%m-%d")
        b = datetime.strptime(d2, "%Y-%m-%d")
        return (b - a).days
    except Exception:
        return None


# ---------- CLI ----------
def _cur_price_and_high(sid):
    """CLI 用：從 cache 最新棒取現價與今日 high（不打即時，CLI 求簡）。"""
    from core import load_bars
    cache = os.path.join(HERE, "cache", "kline_cache.json.gz")
    bars = load_bars(sid.upper(), cache)
    last = bars[-1]
    return last["close"], last["high"]


def _fmt_risk(r):
    sh = f" × {r['shares']}張" if r.get("shares") is not None else ""
    return (
        f"持倉 {r['sid']}\n"
        f"進場 {r['entry_price']}{sh}｜現價 {r['cur_price']}｜未實現 {r['unreal_pct']:+}%\n"
        f"生效出場：{r['effective_exit']}（{r['effective_by']}，"
        f"硬停損{r['hard_stop']} / Trail高點{r['peak_price']}−8%={r['trail_stop']}）\n"
        f"距觸發 {r['dist_pct']:+}%　{r['light']} {r['state']}"
    )


def main():
    ap = argparse.ArgumentParser(description="kanpan L3 持倉風控")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("list")

    p_show = sub.add_parser("show")
    p_show.add_argument("sid")

    p_open = sub.add_parser("open")
    p_open.add_argument("sid")
    p_open.add_argument("entry_price", type=float)
    p_open.add_argument("shares", type=float)
    p_open.add_argument("--date", default=None)
    p_open.add_argument("--note", default="")

    p_close = sub.add_parser("close")
    p_close.add_argument("sid")
    p_close.add_argument("exit_price", type=float)
    p_close.add_argument("--reason", default="manual")
    p_close.add_argument("--date", default=None)

    a = ap.parse_args()

    if a.cmd == "list":
        d = load_positions()
        if not d["open"]:
            print("無持倉。用 python position.py open <sid> <進場價> <張數> 建立。")
            return
        for sid in d["open"]:
            cur, high = _cur_price_and_high(sid)
            r = position_risk(sid, cur, today_high=high, d=d)
            print(_fmt_risk(r))
            print("-" * 30)
        save_positions(d)   # 寫回 peak 累積
    elif a.cmd == "show":
        sid = a.sid.upper()
        cur, high = _cur_price_and_high(sid)
        r = position_risk(sid, cur, today_high=high)
        if not r:
            print(f"{sid} 無持倉。")
            return
        print(_fmt_risk(r))
    elif a.cmd == "open":
        pos = open_position(a.sid.upper(), a.entry_price, a.shares, a.date, a.note)
        print(f"開倉 {a.sid.upper()}：{pos}")
    elif a.cmd == "close":
        rec = close_position(a.sid.upper(), a.exit_price, a.date, a.reason)
        print(f"平倉 {a.sid.upper()}：報酬 {rec['return_pct']:+}%"
              f"（持有 {rec['hold_days']} 日，{rec['exit_reason']}）")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
