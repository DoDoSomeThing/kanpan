#!/usr/bin/env python3
"""模擬 Pine 版長相（matplotlib 畫，數字用 core.py 真算）。
只為預覽版面，真正跑要貼 kanpan_vp.pine 進 TradingView。
用法：python mock_chart.py 1537
"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

plt.rcParams["font.sans-serif"] = ["PingFang TC", "Heiti TC", "Hei"]
plt.rcParams["axes.unicode_minus"] = False

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core import load_bars, compute_panel, sma, verdict  # noqa

CACHE = os.path.join(os.path.dirname(HERE), "cache", "kline_cache.json.gz")
STATS = os.path.join(os.path.dirname(HERE), "research", "score_stats.json")

UP, DN = "#e2483a", "#26a69a"   # 台股：漲紅跌綠
BG = "#0a0e1f"


def win20_for(score):
    if not os.path.exists(STATS):
        return None
    st = json.load(open(STATS, encoding="utf-8"))
    for b in st["buckets"]:
        if b["lo"] <= score <= b["hi"] and b["n"] > 0:
            return b["win20"]
    return None


def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else "1537"
    bars = load_bars(sid, CACHE)
    p = compute_panel(bars)
    v = verdict(p, win20_for(p["vp_score"]))
    evo = p["evo"]

    N = 120
    show = bars[-N:]
    closes = [b["close"] for b in bars]
    ma20 = sma(closes, 20)[-N:]
    ma60 = sma(closes, 60)[-N:]
    x = list(range(len(show)))

    fig, ax = plt.subplots(figsize=(13, 7), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    # K 棒
    for i, b in enumerate(show):
        col = UP if b["close"] >= b["open"] else DN
        ax.plot([i, i], [b["low"], b["high"]], color=col, linewidth=0.8, zorder=2)
        lo, hi = sorted([b["open"], b["close"]])
        ax.add_patch(Rectangle((i - 0.3, lo), 0.6, max(hi - lo, 0.01),
                               facecolor=col, edgecolor=col, zorder=3))

    # 均線
    ax.plot(x, ma20, color="#f4b400", lw=1.1, label="MA20", zorder=4)
    ax.plot(x, ma60, color="#2962ff", lw=1.1, label="MA60", zorder=4)

    # 價值區帶（VAH–VAL 填色）+ POC
    if p["vah"] and p["val"]:
        ax.axhspan(p["val"], p["vah"], color="#7aa2ff", alpha=0.07, zorder=1)
        ax.axhline(p["vah"], color="#ff7a7a", lw=1, ls="--", alpha=0.7, zorder=4)
        ax.axhline(p["val"], color="#5ff08c", lw=1, ls="--", alpha=0.7, zorder=4)
        ax.axhline(p["poc"], color="#e066ff", lw=1.2, ls="-", alpha=0.8, zorder=4)
        ax.text(len(show) - 1, p["vah"], f" VAH壓力 {p['vah']}", color="#ff7a7a", fontsize=8, va="center")
        ax.text(len(show) - 1, p["poc"], f" POC {p['poc']}", color="#e066ff", fontsize=8, va="center")
        ax.text(len(show) - 1, p["val"], f" VAL支撐 {p['val']}", color="#5ff08c", fontsize=8, va="center")

    # 整數關卡
    if p["round_level"]:
        ax.axhline(p["round_level"], color="#8893ad", lw=0.8, ls=":", alpha=0.6, zorder=3)
        ax.text(0, p["round_level"], f"整數 {p['round_level']} ", color="#8893ad",
                fontsize=7.5, va="bottom", ha="left")

    # 進場參考線（偏多時畫支撐＝失守減碼）
    if v["net"] >= 3 and p["val"]:
        ax.axhline(p["val"], color="#5ff08c", lw=1.6, alpha=0.9, zorder=5)

    # 判讀箭頭（示意：最後一根）
    last = show[-1]
    if v["net"] >= 3:
        ax.annotate("多", (len(show) - 1, last["low"]), color="#5ff08c", fontsize=11,
                    ha="center", va="top", weight="bold")
    elif v["net"] <= -3:
        ax.annotate("空", (len(show) - 1, last["high"]), color="#ff7a7a", fontsize=11,
                    ha="center", va="bottom", weight="bold")

    # ---- 右下面板（仿 Evolution Module）；用中文字、符號避開缺字 emoji ----
    tcol = "#4fe08a" if v["net"] >= 3 else "#ff7a7a" if v["net"] <= -3 else "#f4c000"
    lines = [f"● {v['tone']}",
             f"{v['conf']} | 分數 {p['vp_score']}/100 | {p['structure']}",
             "─" * 26]
    ico = {True: "√", False: "×", None: "—"}
    for k in ("A", "B", "C_top", "C_bot", "D", "E", "F", "G"):
        e = evo.get(k)
        if e:
            lines.append(f"{ico[e['ok']]} {e['k']}: {e['v']}")
    lines.append(f"操作: {v['action']}")
    txt = "\n".join(lines)
    ax.text(0.985, 0.02, txt, transform=ax.transAxes, ha="right", va="bottom",
            fontsize=8.2, color="#d7dbe6",
            bbox=dict(boxstyle="round,pad=0.7", facecolor="#0d1330",
                      edgecolor=tcol, linewidth=1.8, alpha=0.96))
    # 標題 + 燈號上色（左上）
    ax.text(0.012, 0.975, f"kanpan VP — {sid}", transform=ax.transAxes,
            ha="left", va="top", fontsize=13, color="#8fb0ff", weight="bold")
    ax.text(0.012, 0.925, f"● {v['tone']}", transform=ax.transAxes,
            ha="left", va="top", fontsize=14, color=tcol, weight="bold")

    ax.set_xlim(-1, len(show) + 8)
    ax.tick_params(colors="#6b7596", labelsize=8)
    for s in ax.spines.values():
        s.set_color("#2a3a66")
    ax.grid(color="#161c33", lw=0.5)
    ax.legend(loc="upper left", bbox_to_anchor=(0.012, 0.88), facecolor="#0d1330",
              edgecolor="#2a3a66", labelcolor="#9aa6c4", fontsize=8)
    ax.set_title(f"kanpan Pine 版預覽（模擬，真正在 TradingView 跑長這樣）  資料日 {p['date']}",
                 color="#9aa6c4", fontsize=9)

    out = os.path.join(HERE, "mockup.png")
    fig.tight_layout()
    fig.savefig(out, facecolor=BG, bbox_inches="tight")
    print("saved", out, "| verdict:", v["tone"], "score", p["vp_score"])


if __name__ == "__main__":
    main()
