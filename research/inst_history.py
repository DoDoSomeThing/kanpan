#!/usr/bin/env python3
"""
法人買賣超 → 未來報酬 回測（誠實驗證：法人「有沒有意思」到底準不準）。

測兩個指標，看「進場當天/近期法人買超強度」分桶後，未來 5/10/20 日的勝率與平均報酬：
  A. 外資當日買超佔成交比重 = 外資淨買超(股) / 當日成交量(股)
  B. 近 5 日外資累積買超佔比 = 5日外資淨買超合計 / 5日成交量合計

資料：kline_deep(2021-06~2024-12 上市日K) + t86_cache(同期三大法人買賣超)。
判讀重點：高買超桶的勝率/平均報酬有沒有「明顯」高於低買超/賣超桶。
  沒明顯差 → 法人買超對未來報酬無 edge（與前作 6 訊號結論一致）。
用法：python3 research/inst_history.py
"""
import json
import os
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
KLINE = HERE.parent / "data" / "kline_deep.json"
# T86 歷史在 tw-stock-bot（kanpan 自身只存近日 live cache）；研究用，指過去即可
T86_DIR = Path(os.getenv("T86_DIR",
               Path.home() / "Desktop" / "Justin" / "Github專案" / "tw-stock-bot" / "t86_cache"))
HORIZON = int(os.getenv("HORIZON", "20"))   # 1=隔天反應、5、20…


def load_kline():
    d = json.load(open(KLINE))
    out = {}
    for sid, rows in d.items():
        rows = sorted(rows, key=lambda r: r["date"])
        idx = {r["date"]: i for i, r in enumerate(rows)}
        out[sid] = {"rows": rows, "idx": idx}
    return out


def load_t86():
    """{日期(YYYY-MM-DD): {sid: 外資淨買超(股)}}。"""
    out = {}
    for f in sorted(T86_DIR.glob("*.json")):
        if len(f.stem) != 8 or not f.stem.isdigit():
            continue
        dt = f"{f.stem[:4]}-{f.stem[4:6]}-{f.stem[6:]}"
        try:
            raw = json.load(open(f))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        out[dt] = {sid: v[0] for sid, v in raw.items() if isinstance(v, list) and v}
    return out


def bucketize(ratio):
    if ratio < 0:      return "賣超(<0)"
    if ratio < 0.02:   return "0~2%"
    if ratio < 0.05:   return "2~5%"
    if ratio < 0.10:   return "5~10%"
    return ">10%(大買)"


ORDER = ["賣超(<0)", "0~2%", "2~5%", "5~10%", ">10%(大買)"]


def fwd_return(k, sid, dt):
    """sid 在 dt 進場、HORIZON 日後報酬%；不足回 None。"""
    info = k.get(sid)
    if not info or dt not in info["idx"]:
        return None
    i = info["idx"][dt]
    rows = info["rows"]
    if i + HORIZON >= len(rows):
        return None
    c0, c1 = rows[i]["close"], rows[i + HORIZON]["close"]
    if c0 <= 0:
        return None
    return (c1 / c0 - 1) * 100


def run(label, ratio_fn, k, t86):
    """ratio_fn(sid, dt, i) → 佔比 or None。分桶統計未來報酬。"""
    buckets = {b: [] for b in ORDER}
    dates = sorted(t86)
    for di, dt in enumerate(dates):
        for sid, fnet in t86[dt].items():
            info = k.get(sid)
            if not info or dt not in info["idx"]:
                continue
            i = info["idx"][dt]
            r = ratio_fn(sid, dt, i, info, di, dates)
            if r is None:
                continue
            fr = fwd_return(k, sid, dt)
            if fr is None:
                continue
            buckets[bucketize(r)].append(fr)

    print(f"\n===== {label} → 未來 {HORIZON} 日（上市,2021-2024）=====")
    print(f"  {'外資買超佔比':<14} {'樣本':>8} {'勝率%':>7} {'報酬中位%':>9} {'平均報酬%':>9}")
    for b in ORDER:
        rs = buckets[b]
        if not rs:
            continue
        win = sum(1 for x in rs if x > 0) / len(rs) * 100
        print(f"  {b:<14} {len(rs):>8,} {win:>6.0f} {statistics.median(rs):>+8.1f} "
              f"{statistics.mean(rs):>+8.1f}")


def ratio_today(sid, dt, i, info, di, dates):
    fnet = T86[dt].get(sid)
    vol = info["rows"][i]["volume"]
    if fnet is None or not vol:
        return None
    return fnet / vol


def ratio_5d(sid, dt, i, info, di, dates):
    """近5日(含當日)外資累積淨買超 / 近5日成交量合計。"""
    if di < 4 or i < 4:
        return None
    fsum = 0.0
    for dd in dates[di - 4:di + 1]:
        v = T86.get(dd, {}).get(sid)
        if v is None:
            return None
        fsum += v
    vsum = sum(info["rows"][j]["volume"] for j in range(i - 4, i + 1))
    if not vsum:
        return None
    return fsum / vsum


def main():
    global T86
    print("載入 kline_deep + t86...")
    k = load_kline()
    T86 = load_t86()
    print(f"kline {len(k)} 檔；t86 {len(T86)} 天")
    run("A. 外資當日買超佔成交比重", ratio_today, k, T86)
    run("B. 近5日外資累積買超佔比", ratio_5d, k, T86)
    print("\n判讀：高買超桶若勝率/平均報酬沒明顯高於賣超桶 → 法人買超對未來報酬無 edge。")


if __name__ == "__main__":
    main()
