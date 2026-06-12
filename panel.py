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
    line = "=" * 30
    out = [line, f"  kanpan 看盤 — {sid}", line, ""]
    out.append(f"當前分數: {p['vp_score']} / 100")
    out.append("（趨勢40% + 動能20% + 量能20% + 位置20%）")
    out.append(f"資料日: {p['date']}　收盤 {p['close']}")
    out.append("")
    if stats:
        b = bucket_label(p["vp_score"], stats)
        if b and b["n"] > 0:
            out += [
                f"歷史統計（分數 {b['lo']}~{b['hi']} 的過去表現，{stats['period']}）:",
                f"  樣本數:   {b['n']:,}",
                f"  5日勝率:  {b['win5']}%　10日: {b['win10']}%　20日: {b['win20']}%",
                f"  平均報酬: {b['avg20']:+.1f}% (20日)",
                f"  最大回撤: {b['mdd']:.1f}%",
            ]
        else:
            out.append("歷史統計: 此分數區間樣本不足")
    else:
        out.append("歷史統計: 未產生（先跑 research/score_history.py）")
    out += ["", "-" * 30]
    out.append(f"趨勢分數: {p['trend_score']}/100（均線結構）")
    out.append(f"結構:     {p['structure']}")
    out.append(f"週線:     {p['weekly']}｜{p['resonance']}")
    out.append(f"動能:     RSI {p['rsi']}　{p['momentum']}")
    vr = f"{p['vol_ratio']}倍" if p["vol_ratio"] is not None else "—"
    out.append(f"量能:     {vr}　{p['vol_tag']}｜{p['skew_tag']}")
    if p["pos_pct"] is not None:
        hi_lo = "偏高" if p["pos_pct"] >= 70 else "偏低" if p["pos_pct"] <= 30 else "中段"
        out.append(f"位置:     60日區間 {p['pos_pct']}%（{hi_lo}）")
    if p.get("vah"):
        out.append(f"參考價位: 壓力 {p['vah']}｜中軸 {p['poc']}｜支撐 {p['val']}（非建議）")
    out += ["", "-" * 30, "評語:", comment(p), "", line]
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
