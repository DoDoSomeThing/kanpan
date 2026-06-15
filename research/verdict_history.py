#!/usr/bin/env python3
"""
verdict_history — 判讀燈號歷史驗證（檢查 verdict() 的燈號到底有沒有分離度）

問題：core.verdict() 一堆手調門檻（net≥6、score≥78、RSI78、bias22…）從沒驗過。
  rank.py 已誠實承認「90-100 分桶未來20日勝率僅 46%、沒 edge」。
  燈號同理——若「強多頭」未來表現沒比「偏空轉弱」好，燈號就是假確定性，該砍或重調。

做法：對 kline_deep.json 每股每日，直接呼叫**產線** compute_panel()+verdict()
  （驗真實程式碼，不重寫邏輯），按燈號 tone 分桶，配未來 5/10/20 日報酬：
    樣本數 / 5日勝率 / 10日勝率 / 20日勝率 / 平均20日 / 最差20日

判讀：看「強多頭→多頭有利→方向待定→偏弱→偏空」勝率是否單調遞減。
  有遞減=燈號有分離度（留）；打平或反向=沒 edge（砍/重調）。
  這是描述歷史，不是預測未來；台股長多樣本偏多，看桶間相對差距。

用法：python verdict_history.py [--deep 路徑] [--step 10] [--max 檔數上限]
"""
import argparse
import json
import os
import sys
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core import compute_panel, verdict

DEEP_CANDIDATES = [
    os.path.join(HERE, "..", "data", "kline_deep.json"),
    os.path.join(HERE, "..", "..", "Github專案", "tw-stock-bot", "cache", "kline_deep.json"),
]

# 燈號由強到弱排序（驗分離度時看勝率是否照這順序遞減）
TONES = ["強多頭訊號", "多頭有利", "方向待定", "偏弱待觀察", "偏空轉弱"]


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
    ap.add_argument("--step", type=int, default=10, help="每 N 日取樣（省時，預設10）")
    ap.add_argument("--max", type=int, default=0, help="最多掃幾檔（0=全部）")
    a = ap.parse_args()

    path = find_deep(a.deep)
    print(f"載入 {path} ...")
    data = json.load(open(path, encoding="utf-8"))
    print(f"{len(data)} 檔  step={a.step}" + (f"  max={a.max}" if a.max else ""))

    rows = {t: {"n": 0, "r5": [], "r10": [], "r20": []} for t in TONES}
    other = {"n": 0, "r5": [], "r10": [], "r20": []}   # 不在五分類內的兜底

    skipped = scanned = 0
    for n_done, (sid, raw) in enumerate(data.items(), 1):
        if a.max and scanned >= a.max:
            break
        if not isinstance(raw, list) or len(raw) < 150:
            skipped += 1
            continue
        bars = sorted(raw, key=lambda x: x["date"])
        bars = [{"date": b["date"], "open": b.get("open"),
                 "high": b.get("high", b.get("max")), "low": b.get("low", b.get("min")),
                 "close": b["close"], "volume": b.get("volume", 0) or 0} for b in bars]
        closes = [b["close"] for b in bars]
        scanned += 1

        for i in range(120, len(bars) - 20, a.step):
            c = closes[i]
            if not c or c <= 0:
                continue
            try:
                p = compute_panel(bars, i)
                v = verdict(p)            # win20_rate 不影響 tone 分類，省略
            except Exception:
                continue
            tone = v["tone"]
            bucket = rows.get(tone, other)
            bucket["n"] += 1
            f5  = closes[i + 5]  if i + 5  < len(closes) else None
            f10 = closes[i + 10] if i + 10 < len(closes) else None
            f20 = closes[i + 20]
            if f5:  bucket["r5"].append((f5 / c - 1) * 100)
            if f10: bucket["r10"].append((f10 / c - 1) * 100)
            if f20: bucket["r20"].append((f20 / c - 1) * 100)
        if n_done % 100 == 0:
            print(f"  {n_done}/{len(data)} ...")

    print(f"\n掃 {scanned} 檔，跳過(資料不足) {skipped} 檔\n")

    def wr(xs):
        return round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1) if xs else None

    out = {"period": "2021-06~2024-12 (kline_deep)", "note": "verdict 燈號分離度驗證",
           "tones": []}
    hdr = f"{'燈號':<10}{'樣本':>9}{'5日勝':>7}{'10日勝':>8}{'20日勝':>8}{'平均20日':>9}{'最差20日':>9}"
    print(hdr)
    print("-" * len(hdr))
    for t in TONES + ["(其他)"]:
        r = rows[t] if t in rows else other
        n = r["n"]
        b = {"tone": t, "n": n, "win5": wr(r["r5"]), "win10": wr(r["r10"]),
             "win20": wr(r["r20"]),
             "avg20": round(statistics.mean(r["r20"]), 2) if r["r20"] else None,
             "mdd": round(min(r["r20"]), 1) if r["r20"] else None}
        out["tones"].append(b)
        print(f"{t:<10}{n:>9,}{b['win5'] or '—':>7}{b['win10'] or '—':>8}"
              f"{b['win20'] or '—':>8}"
              f"{b['avg20'] if b['avg20'] is not None else '—':>9}"
              f"{b['mdd'] if b['mdd'] is not None else '—':>9}")

    sp = os.path.join(HERE, "verdict_stats.json")
    json.dump(out, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n→ 存 {sp}")
    print("※ 判讀：勝率/平均報酬若沒照『強多頭>多頭有利>...>偏空』遞減，燈號無分離度，該重調或砍。")


if __name__ == "__main__":
    main()
