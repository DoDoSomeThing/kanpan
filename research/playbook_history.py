#!/usr/bin/env python3
"""
playbook_history — L2 規則化劇本引擎回測（V2 Phase 3）

對 kline_deep.json（2021-06~2024-12）跑三個**固定**模板，配未來 20 日報酬，
做**前後分段**驗證（前半段=訓練、後半段=驗證，依觸發日期切），各算：
  樣本數 / 20日勝率 / 平均報酬(20日) / 最大回撤(觸發後最差20日)

三模板（spec Phase 3 草案，禁任意組合，擋資料探勘）：
  突破型：收盤 > 近20日高(不含當日) 且 量 > 1.2×MV20
  跌破型：收盤 < 近20日低(不含當日)
  回測型：前一根 < MA20、本根 ≥ MA20（站回）且 量 ≥ MV20

產 research/playbook_stats.json 給 panel/api 引用。
心理準備：依既有驗證，多半跑出勝率 45~50% → 引擎價值＝誠實證明多數型態沒 edge、勸退亂進場。

用法：python playbook_history.py [--deep 路徑] [--step 1]
"""
import argparse
import json
import os
import sys
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core import sma                                   # noqa: E402
from playbook import TEMPLATES, fires                  # noqa: E402  共用模板定義

DEEP_CANDIDATES = [
    os.path.join(HERE, "..", "data", "kline_deep.json"),
    os.path.join(HERE, "..", "..", "Github專案", "tw-stock-bot", "cache", "kline_deep.json"),
]

# 前後分段切點：2021-06~2024-12 約中點。觸發日 < 此 → 訓練，否則 → 驗證。
SPLIT_DATE = "2023-03-01"
FWD = 20            # 未來 N 日報酬
# 可投資門檻：觸發日 close < 此 → 不納入回測。
# 目的＝對齊使用者實際會買的範圍(剔雞蛋水餃/將下市股)，讓 mdd 反映真會遇到的最壞，
# 非「藏 -100%」。**一次定死、只用客觀價格門檻**，不得為了調勝率再加條件(=資料探勘)。
MIN_PRICE = 10.0


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
    ap.add_argument("--step", type=int, default=1, help="每 N 日取樣（劇本觸發稀疏，預設 1）")
    a = ap.parse_args()

    path = find_deep(a.deep)
    print(f"載入 {path} ...")
    data = json.load(open(path, encoding="utf-8"))
    print(f"{len(data)} 檔　切點 {SPLIT_DATE}（前訓練/後驗證）")

    # 每模板 × {train,val} 收集 fwd20 報酬
    rec = {name: {"train": [], "val": []} for name in TEMPLATES}

    skipped = 0
    for n_done, (sid, raw) in enumerate(data.items(), 1):
        if not isinstance(raw, list) or len(raw) < 150:
            skipped += 1
            continue
        bars = sorted(raw, key=lambda x: x["date"])
        closes = [b["close"] for b in bars]
        vols   = [b.get("volume", 0) or 0 for b in bars]
        m20 = sma(closes, 20)
        v20 = sma(vols, 20)

        for i in range(20, len(bars) - FWD, a.step):
            c = closes[i]
            if not c or c < MIN_PRICE or i + FWD >= len(closes):
                continue   # 低價股(<MIN_PRICE)剔除：非使用者投資範圍，避免歸零雜訊汙染 mdd
            fired = fires(bars, closes, vols, m20, v20, i)
            if not fired:
                continue
            fc = closes[i + FWD]
            if not fc or fc <= 0:
                continue   # 未來價缺值(deep 資料破洞填 0)→ 跳過，避免假 -100% 汙染 mdd
            fwd = (fc / c - 1) * 100
            seg = "train" if bars[i]["date"][:10] < SPLIT_DATE else "val"
            for name in fired:
                rec[name][seg].append(fwd)
        if n_done % 200 == 0:
            print(f"  {n_done}/{len(data)} ...")

    print(f"跳過(資料不足) {skipped} 檔\n")

    def stat(xs):
        if not xs:
            return {"n": 0, "win20": None, "avg20": None, "mdd": None}
        return {
            "n": len(xs),
            "win20": round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1),
            "avg20": round(statistics.mean(xs), 2),
            "mdd": round(min(xs), 1),
        }

    out = {"period": "2021-06~2024-12 (kline_deep)", "split": SPLIT_DATE,
           "fwd": FWD, "min_price": MIN_PRICE, "templates": {}}
    print(f"{'模板':<10}{'訓練n':>7}{'訓練勝':>7}{'驗證n':>7}{'驗證勝':>7}{'平均20':>8}{'最差20':>8}")
    for name in TEMPLATES:
        tr, va = stat(rec[name]["train"]), stat(rec[name]["val"])
        allxs = rec[name]["train"] + rec[name]["val"]
        out["templates"][name] = {
            "cond": TEMPLATES[name],
            "train": tr, "val": va,
            "n": len(allxs),
            "avg20": round(statistics.mean(allxs), 2) if allxs else None,
            "mdd": round(min(allxs), 1) if allxs else None,
        }
        print(f"{name:<10}{tr['n']:>7,}{(tr['win20'] or '—'):>7}{va['n']:>7,}"
              f"{(va['win20'] or '—'):>7}{(va['avg20'] if va['avg20'] is not None else '—'):>8}"
              f"{(va['mdd'] if va['mdd'] is not None else '—'):>8}")

    sp = os.path.join(HERE, "playbook_stats.json")
    json.dump(out, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n→ 存 {sp}（panel/api 自動引用）")
    print("※ 描述歷史非預測；三模板大概率勝率 45~50%＝多數型態沒 edge，引擎用來勸退亂進場。")


if __name__ == "__main__":
    main()
