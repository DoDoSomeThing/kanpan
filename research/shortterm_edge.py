#!/usr/bin/env python3
"""
短線 edge 驗證 — 高分選股的「短期超額報酬」是否真實、是否每年都在。

問題：kanpan vp_score 高的股，短線(1~3日)會贏「當天平均股票」嗎？(beta中性，測純選股本事)
做法：對 kline_deep(2021-06~2024-12) 每股每日算 vp_score；
      基準 = 當天所有股票的 h 日平均報酬(大盤代理)；超額 = 高分股報酬 − 當天平均。
      逐年 × 持有天數 報：樣本/超額勝率/平均超額。真 edge 要每年都正、且 > 成本。
誠實：扣來回成本 ~0.79%(短線每筆都付，基準買著放不付) → 看「扣成本後超額」才算數。

用法：python research/shortterm_edge.py [--step 2] [--th 80]
讀 kline_deep(只讀，不改封存專案)。
"""
import argparse
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core import sma, rsi14, trend_score, vp_score  # noqa

DEEP = [
    os.path.join(HERE, "..", "data", "kline_deep.json"),
    os.path.join(HERE, "..", "..", "Github專案", "tw-stock-bot", "cache", "kline_deep.json"),
]
HORIZONS = [1, 2, 3, 5]
COST = 0.79   # 來回手續+稅 %


def find_deep():
    for p in DEEP:
        if os.path.exists(p):
            return p
    sys.exit("找不到 kline_deep.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=2, help="每 N 日取樣")
    ap.add_argument("--th", type=int, default=80, help="高分門檻(>=)")
    a = ap.parse_args()

    data = json.load(open(find_deep(), encoding="utf-8"))
    print(f"{len(data)} 檔，門檻 score>={a.th}，step={a.step}")

    # market[h][date] = [報酬總和, 筆數]  → 當天平均股票 h 日報酬(大盤代理)
    market = {h: defaultdict(lambda: [0.0, 0]) for h in HORIZONS}
    # picks[h] = list of (date, 報酬)  只存高分股
    picks = {h: [] for h in HORIZONS}

    skipped = 0
    for n, (sid, raw) in enumerate(data.items(), 1):
        if not isinstance(raw, list) or len(raw) < 150:
            skipped += 1
            continue
        bars = sorted(raw, key=lambda x: x["date"])
        closes = [b["close"] for b in bars]
        vols = [b.get("volume", 0) or 0 for b in bars]
        highs = [b.get("high", b.get("max")) for b in bars]
        lows = [b.get("low", b.get("min")) for b in bars]
        m5, m10 = sma(closes, 5), sma(closes, 10)
        m20, m60, m120 = sma(closes, 20), sma(closes, 60), sma(closes, 120)
        rs = rsi14(closes)
        v20 = sma(vols, 20)

        for i in range(120, len(bars) - max(HORIZONS), a.step):
            c = closes[i]
            if not c or c <= 0:
                continue
            date = bars[i]["date"]
            t = trend_score(c, m5[i], m10[i], m20[i], m60[i], m120[i])
            lo_i = max(0, i - 59)
            try:
                h60 = max(h for h in highs[lo_i:i + 1] if h)
                l60 = min(l for l in lows[lo_i:i + 1] if l)
            except ValueError:
                continue
            vr = (vols[i] / v20[i]) if v20[i] else None
            s = vp_score(t, rs[i], round(vr, 2) if vr else None, c, l60, h60)

            for h in HORIZONS:
                fc = closes[i + h] if i + h < len(closes) else None
                if not fc:
                    continue
                ret = (fc / c - 1) * 100
                market[h][date][0] += ret      # 所有股票都進基準
                market[h][date][1] += 1
                if s >= a.th:
                    picks[h].append((date, ret))
        if n % 300 == 0:
            print(f"  {n}/{len(data)} ...")

    print(f"跳過(資料不足) {skipped} 檔\n")

    # 逐年 × 持有 統計超額
    print(f"{'年':<6}{'持有':>5}{'樣本':>8}{'超額勝率':>9}{'平均超額':>10}{'扣成本後':>10}")
    for year in ["2021", "2022", "2023", "2024", "全期"]:
        for h in HORIZONS:
            ex = []
            for date, ret in picks[h]:
                if year != "全期" and not date.startswith(year):
                    continue
                m = market[h][date]
                if m[1] == 0:
                    continue
                ex.append(ret - m[0] / m[1])     # 超額 = 高分股 − 當天平均股
            if not ex:
                continue
            win = sum(1 for x in ex if x > 0) / len(ex) * 100
            avg = sum(ex) / len(ex)
            print(f"{year:<6}{h:>4}天{len(ex):>8}{win:>8.0f}%{avg:>+9.2f}%{avg - COST:>+9.2f}%")
        if year != "全期":
            print()


if __name__ == "__main__":
    main()
