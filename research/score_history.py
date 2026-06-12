#!/usr/bin/env python3
"""
score_history — VP Score 歷史驗證（第三階段）

對 kline_deep.json（2021-06~2024-12，含多空整理多市況）每股每日算 VP Score，
配未來 5/10/20 日報酬，按分數分桶統計：
  樣本數 / 5日勝率 / 10日勝率 / 20日勝率 / 平均報酬(20日) / 最大回撤(桶內最差20日)

產 research/score_stats.json 給 panel.py 引用 → 面板顯示「歷史上類似分數的表現」。
這是描述歷史，不是預測未來。

用法：python score_history.py [--deep 路徑] [--step 2]
"""
import argparse
import json
import os
import sys
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core import sma, rsi14, trend_score, vp_score

DEEP_CANDIDATES = [
    os.path.join(HERE, "..", "data", "kline_deep.json"),
    os.path.join(HERE, "..", "..", "Github專案", "tw-stock-bot", "cache", "kline_deep.json"),
]

BUCKETS = [(90, 100), (80, 89), (70, 79), (60, 69), (50, 59), (40, 49), (0, 39)]


def find_deep(arg):
    if arg:
        return arg
    for p in DEEP_CANDIDATES:
        if os.path.exists(p):
            return p
    sys.exit("找不到 kline_deep.json。先跑 data/fetch_data.py 下載（GitHub Release）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", default=None)
    ap.add_argument("--step", type=int, default=2, help="每 N 日取樣（省時）")
    a = ap.parse_args()

    path = find_deep(a.deep)
    print(f"載入 {path} ...")
    data = json.load(open(path, encoding="utf-8"))
    print(f"{len(data)} 檔")

    rows = {f"{lo}-{hi}": {"scores": 0, "r5": [], "r10": [], "r20": []}
            for lo, hi in BUCKETS}

    skipped = 0
    for n_done, (sid, raw) in enumerate(data.items(), 1):
        if not isinstance(raw, list) or len(raw) < 150:
            skipped += 1
            continue
        bars = sorted(raw, key=lambda x: x["date"])
        closes = [b["close"] for b in bars]
        vols   = [b.get("volume", 0) or 0 for b in bars]
        highs  = [b.get("high", b.get("max")) for b in bars]
        lows   = [b.get("low", b.get("min")) for b in bars]
        m5, m10 = sma(closes, 5), sma(closes, 10)
        m20, m60, m120 = sma(closes, 20), sma(closes, 60), sma(closes, 120)
        rs = rsi14(closes)
        v20 = sma(vols, 20)

        for i in range(120, len(bars) - 20, a.step):
            c = closes[i]
            if not c or c <= 0:
                continue
            t = trend_score(c, m5[i], m10[i], m20[i], m60[i], m120[i])
            lo_i = max(0, i - 59)
            try:
                h60 = max(h for h in highs[lo_i:i + 1] if h)
                l60 = min(l for l in lows[lo_i:i + 1] if l)
            except ValueError:
                continue
            vr = (vols[i] / v20[i]) if v20[i] else None
            s = vp_score(t, rs[i], round(vr, 2) if vr else None, c, l60, h60)

            f5  = closes[i + 5]  if i + 5  < len(closes) else None
            f10 = closes[i + 10] if i + 10 < len(closes) else None
            f20 = closes[i + 20]
            for lo_b, hi_b in BUCKETS:
                if lo_b <= s <= hi_b:
                    key = f"{lo_b}-{hi_b}"
                    rows[key]["scores"] += 1
                    if f5:  rows[key]["r5"].append((f5 / c - 1) * 100)
                    if f10: rows[key]["r10"].append((f10 / c - 1) * 100)
                    if f20: rows[key]["r20"].append((f20 / c - 1) * 100)
                    break
        if n_done % 200 == 0:
            print(f"  {n_done}/{len(data)} ...")

    print(f"跳過(資料不足) {skipped} 檔\n")
    out = {"period": "2021-06~2024-12 (kline_deep)", "buckets": []}
    hdr = f"{'Score':<8}{'樣本':>9}{'5日勝':>7}{'10日勝':>8}{'20日勝':>8}{'平均20日':>9}{'最差20日':>9}"
    print(hdr)
    for lo_b, hi_b in BUCKETS:
        key = f"{lo_b}-{hi_b}"
        r = rows[key]
        n = r["scores"]
        def wr(xs): return round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1) if xs else None
        b = {
            "lo": lo_b, "hi": hi_b, "n": n,
            "win5":  wr(r["r5"]),
            "win10": wr(r["r10"]),
            "win20": wr(r["r20"]),
            "avg20": round(statistics.mean(r["r20"]), 2) if r["r20"] else None,
            "mdd":   round(min(r["r20"]), 1) if r["r20"] else None,
        }
        out["buckets"].append(b)
        print(f"{key:<8}{n:>9,}{b['win5'] or '—':>7}{b['win10'] or '—':>8}"
              f"{b['win20'] or '—':>8}{b['avg20'] if b['avg20'] is not None else '—':>9}"
              f"{b['mdd'] if b['mdd'] is not None else '—':>9}")

    sp = os.path.join(HERE, "score_stats.json")
    json.dump(out, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n→ 存 {sp}（panel.py 會自動引用）")
    print("※ 歷史統計描述過去，不是預測未來；台股長多期樣本偏多，看桶間相對差距。")


if __name__ == "__main__":
    main()
