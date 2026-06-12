#!/usr/bin/env python3
"""
kanpan panel — CLI 看盤面板

用法：
  python panel.py 2330                     # 用預設 cache（cache/kline_cache.json.gz）
  python panel.py 2330 --cache 路徑        # 指定 cache

輸出：VP PANEL（VP Score / 歷史統計 / 四區塊 / 評語）。
歷史統計來自 research/score_stats.json（先跑 research/score_history.py 產生）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import load_bars, compute_panel, comment

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHES = [
    os.path.join(HERE, "cache", "kline_cache.json.gz"),   # kanpan 自己的 cache(fetch_data.py 下載)
]
STATS_PATH = os.path.join(HERE, "research", "score_stats.json")


def find_cache(arg):
    if arg:
        return arg
    for p in DEFAULT_CACHES:
        if os.path.exists(p):
            return p
    sys.exit("找不到 K 線 cache。用 --cache 指定，或先跑 data/fetch_data.py")


def load_stats():
    if os.path.exists(STATS_PATH):
        return json.load(open(STATS_PATH, encoding="utf-8"))
    return None


def bucket_label(score, stats):
    for b in stats["buckets"]:
        if b["lo"] <= score <= b["hi"]:
            return b
    return None


def render(sid, p, stats):
    line = "=" * 28
    out = [line, "  VP PANEL — " + sid, line, ""]
    out.append(f"VP Score: {p['vp_score']}")
    out.append(f"資料日:   {p['date']}　收盤 {p['close']}")
    out.append("")
    if stats:
        b = bucket_label(p["vp_score"], stats)
        if b and b["n"] > 0:
            out += [
                f"歷史統計（Score {b['lo']}~{b['hi']}，{stats['period']}）:",
                f"  樣本數:   {b['n']:,}",
                f"  5日勝率:  {b['win5']}%",
                f"  10日勝率: {b['win10']}%",
                f"  20日勝率: {b['win20']}%",
                f"  平均報酬: {b['avg20']:+.1f}% (20日)",
                f"  最大回撤: {b['mdd']:.1f}%",
            ]
        else:
            out.append("歷史統計: 此分數區間樣本不足")
    else:
        out.append("歷史統計: 未產生（先跑 research/score_history.py）")
    out += ["", "-" * 28]
    out.append(f"Trend:     {p['trend_score']}/100")
    out.append(f"Structure: {p['structure']}")
    out.append(f"Momentum:  RSI {p['rsi']}　{p['momentum']}")
    vr = f"{p['vol_ratio']}倍" if p["vol_ratio"] is not None else "—"
    out.append(f"Volume:    {vr}　{p['vol_tag']}")
    if p["pos_pct"] is not None:
        out.append(f"Position:  60日區間 {p['pos_pct']}% 位置")
    out += ["", "-" * 28, "評語:", comment(p), "", line]
    out.append("※ 描述現況與歷史統計，非買賣建議。")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sid")
    ap.add_argument("--cache", default=None)
    a = ap.parse_args()
    cache = find_cache(a.cache)
    bars = load_bars(a.sid.upper(), cache)
    p = compute_panel(bars)
    print(render(a.sid.upper(), p, load_stats()))


if __name__ == "__main__":
    main()
