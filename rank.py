#!/usr/bin/env python3
"""
kanpan rank — 全市場分數排行（掃 cache 算每檔 VP Score，看分布 + 高分清單）。

提醒：高分 = 「現在結構/動能最強」的描述，不是買訊。
  歷史驗證（research/score_history.py）：90-100 分桶未來20日勝率 46%，並沒比低分桶高，
  只是平均報酬較高（強勢右偏）。當「現在誰最強勢」的清單用，別當明牌。

用法：
  python3 rank.py            # 分布 + 前 30 名
  python3 rank.py --min 80   # 只列 80 分以上
  python3 rank.py --top 50
"""
import argparse
import collections
import gzip
import json
import os
import ssl
import urllib.request

from core import compute_panel, _find_finmind_token

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache", "kline_cache.json.gz")
NAMES = os.path.join(HERE, "cache", "names.json")


def load_names() -> dict:
    """代號→股名。先讀本地 cache/names.json，沒有就抓 FinMind 全市場股名存起來。"""
    if os.path.exists(NAMES):
        try:
            return json.load(open(NAMES, encoding="utf-8"))
        except Exception:
            pass
    tok = _find_finmind_token()
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo&token={tok}"
    try:
        r = json.load(urllib.request.urlopen(url, timeout=40,
                                             context=ssl.create_default_context()))
        m = {x["stock_id"]: x["stock_name"] for x in r.get("data", [])}
    except Exception:
        return {}
    if m:
        os.makedirs(os.path.dirname(NAMES), exist_ok=True)
        json.dump(m, open(NAMES, "w", encoding="utf-8"), ensure_ascii=False)
    return m


def scan() -> list:
    d = json.load(gzip.open(CACHE)) if CACHE.endswith(".gz") else json.load(open(CACHE))
    out = []
    for sid, rows in d.items():
        if len(rows) < 130:                 # 不足算不出 ma120
            continue
        bars = sorted(({
            "date": r["date"], "open": r.get("open"),
            "high": r.get("high", r.get("max")), "low": r.get("low", r.get("min")),
            "close": r["close"], "volume": r.get("volume", 0) or 0,
        } for r in rows), key=lambda x: x["date"])
        try:
            p = compute_panel(bars)
        except Exception:
            continue
        s = p.get("vp_score")
        if s is not None:
            out.append((sid, s, p.get("structure", ""), p.get("rsi")))
    out.sort(key=lambda x: -x[1])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=0, help="只列 N 分以上")
    ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()

    rows = scan()
    names = load_names()
    vals = [s for _, s, _, _ in rows]
    print(f"\n掃描 {len(vals)} 檔（上市 cache）")

    buckets = collections.Counter((v // 10) * 10 for v in vals)
    print("\n分數分布：")
    for b in sorted(buckets, reverse=True):
        print(f"  {b:>2}~{b+9}: {buckets[b]:>4} 檔  {'#' * (buckets[b] // 8)}")
    print(f"\n80 以上：{sum(1 for v in vals if v >= 80)} 檔　"
          f"90 以上：{sum(1 for v in vals if v >= 90)} 檔")

    sel = [r for r in rows if r[1] >= a.min][:a.top] if a.min else rows[:a.top]
    title = f"{a.min} 分以上" if a.min else f"前 {a.top} 名"
    print(f"\n{title}：")
    print(f"  {'代號':<8}{'名稱':<10}{'分數':>4}  {'結構':<12}{'RSI':>5}")
    for sid, s, st, rsi in sel:
        nm = names.get(sid, "")
        print(f"  {sid:<8}{nm:<10}{s:>4}  {st:<12}{(f'{rsi:.0f}' if rsi else '—'):>5}")
    print("\n⚠️ 高分=現在最強勢的描述,非買訊（90分桶歷史勝率46%沒比低分高,見 README）。")


if __name__ == "__main__":
    main()
