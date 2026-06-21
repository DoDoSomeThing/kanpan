#!/usr/bin/env python3
"""
kanpan portfolio — 組合層視圖（V2 / ROADMAP P1.5 + P2）

不預測。只把「單股面板看不到」的組合真相攤開：
  - P1.5 累計超額 α：你所有平倉 vs 同期 0050，總帳上贏過躺著買大盤嗎?
  - P2 相關係數：常看的幾檔是否高度相關 = 假分散（以為分散、其實 all-in 一個賭注）。
  - P2 曝險權重：各檔佔組合多少 + （選填）現金水位對照。

純函式為主（給 test 釘），I/O（載 K、現價）由呼叫端（api/CLI）注入。
"""
from math import sqrt


# ---------- P1.5：累計超額 α ----------
def cumulative_alpha(closed_records):
    """彙總平倉歷史（已含 bench_pct/alpha_pct，來自 position.closed_with_alpha）。
    回:
      n              納入計算的筆數（有 alpha 的才算）
      sum_return     已實現報酬 % 加總（你）
      sum_bench      同期 0050 報酬 % 加總（躺著買）
      sum_alpha      累計超額 α %（sum_return − sum_bench）
      mean_alpha     平均每筆超額 α %
      win_vs_bench   贏過 0050 的筆數
      beat_rate      贏過 0050 的比例 %（None if n==0）
    說明：簡單加總（非複利連乘），逐筆「你 vs 大盤」誠實對帳，非帳戶淨值曲線。"""
    rs = [r for r in closed_records if r.get("alpha_pct") is not None]
    n = len(rs)
    if n == 0:
        return {"n": 0, "sum_return": 0.0, "sum_bench": 0.0, "sum_alpha": 0.0,
                "mean_alpha": None, "win_vs_bench": 0, "beat_rate": None}
    sum_return = round(sum(r["return_pct"] for r in rs), 1)
    sum_bench = round(sum(r["bench_pct"] for r in rs), 1)
    sum_alpha = round(sum_return - sum_bench, 1)
    win = sum(1 for r in rs if r["alpha_pct"] > 0)
    return {
        "n": n,
        "sum_return": sum_return,
        "sum_bench": sum_bench,
        "sum_alpha": sum_alpha,
        "mean_alpha": round(sum_alpha / n, 1),
        "win_vs_bench": win,
        "beat_rate": round(win / n * 100, 1),
    }


# ---------- P2：相關係數 ----------
def _closes_by_date(bars):
    """{date10: close}，date 取前 10 字。"""
    return {(b["date"] or "")[:10]: b["close"]
            for b in bars if b.get("close") is not None}


def aligned_returns(bars_a, bars_b):
    """兩檔在共同交易日上的『日報酬序列』（對齊後相鄰日 pct）。
    回 (ra, rb) 等長 list；共同日 < 3 → ([], [])。"""
    ca, cb = _closes_by_date(bars_a), _closes_by_date(bars_b)
    common = sorted(set(ca) & set(cb))
    if len(common) < 3:
        return [], []
    ra, rb = [], []
    for i in range(1, len(common)):
        p0a, p1a = ca[common[i - 1]], ca[common[i]]
        p0b, p1b = cb[common[i - 1]], cb[common[i]]
        if p0a and p0b:
            ra.append(p1a / p0a - 1)
            rb.append(p1b / p0b - 1)
    return ra, rb


def pearson(xs, ys):
    """Pearson 相關係數。長度不符/不足/零變異 → None。"""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / sqrt(sxx * syy), 2)


def correlation_matrix(bars_map):
    """bars_map: {sid: bars}。回 [{a, b, corr, n}] 兩兩相關（上三角）。
    n = 共同日報酬點數。corr None 表資料不足/零變異。"""
    sids = list(bars_map.keys())
    out = []
    for i in range(len(sids)):
        for j in range(i + 1, len(sids)):
            ra, rb = aligned_returns(bars_map[sids[i]], bars_map[sids[j]])
            out.append({"a": sids[i], "b": sids[j],
                        "corr": pearson(ra, rb), "n": len(ra)})
    return out


def high_corr_pairs(matrix, threshold=0.7):
    """挑出相關 >= threshold 的對（揭露假分散）。依 corr 由高到低。"""
    hi = [m for m in matrix if m.get("corr") is not None and m["corr"] >= threshold]
    return sorted(hi, key=lambda m: m["corr"], reverse=True)


# ---------- P2：曝險權重 ----------
def exposure(holdings, cash=None):
    """holdings: [{sid, shares, price}]（price=現價，shares=張）。
    市值 = shares × price（張×價，相對權重用，單位不影響比例）。
    回:
      total_value   持倉總市值
      cash          現金（None 表未提供）
      invested_pct  持倉佔（持倉+現金）比 %（cash None → None）
      positions     [{sid, value, weight}]（weight=佔持倉總額 %），依市值由大到小
    """
    rows = []
    for h in holdings:
        sh, px = h.get("shares"), h.get("price")
        val = round(sh * px, 2) if (sh is not None and px is not None) else 0.0
        rows.append({"sid": h["sid"], "value": val})
    total = round(sum(r["value"] for r in rows), 2)
    for r in rows:
        r["weight"] = round(r["value"] / total * 100, 1) if total > 0 else None
    rows.sort(key=lambda r: r["value"], reverse=True)
    invested_pct = None
    if cash is not None and (total + cash) > 0:
        invested_pct = round(total / (total + cash) * 100, 1)
    return {"total_value": total, "cash": cash,
            "invested_pct": invested_pct, "positions": rows}


# ---------- CLI ----------
def _main():
    import argparse
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from core import load_bars
    from position import (load_positions, closed_with_alpha, _load_bench)

    ap = argparse.ArgumentParser(description="kanpan 組合層視圖（累計α/相關/曝險）")
    ap.add_argument("--cash", type=float, default=None, help="現金（元），算投資比")
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    cache = os.path.join(here, "cache", "kline_cache.json.gz")

    d = load_positions()
    bench = _load_bench(cache)

    c = cumulative_alpha(closed_with_alpha(d["closed"], bench))
    print("=" * 40)
    print("累計超額 α（所有平倉 vs 同期 0050）")
    if c["n"] == 0:
        print("  尚無平倉紀錄。")
    else:
        print(f"  筆數 {c['n']}｜你累計 {c['sum_return']:+}%｜0050 {c['sum_bench']:+}%")
        print(f"  累計超額 α {c['sum_alpha']:+}%（平均每筆 {c['mean_alpha']:+}%）")
        print(f"  贏過大盤 {c['win_vs_bench']}/{c['n']} 筆（{c['beat_rate']}%）")

    holdings, bars_map = [], {}
    for s in list(d["open"].keys()):
        try:
            bars = load_bars(s, cache)
        except KeyError:
            continue
        bars_map[s] = bars
        holdings.append({"sid": s, "shares": d["open"][s].get("shares"),
                         "price": bars[-1]["close"]})

    print("-" * 40)
    print("曝險權重")
    ex = exposure(holdings, cash=a.cash)
    if not ex["positions"]:
        print("  無持倉。")
    else:
        for r in ex["positions"]:
            print(f"  {r['sid']}: 市值 {r['value']}｜權重 {r['weight']}%")
        print(f"  總市值 {ex['total_value']}"
              + (f"｜現金 {ex['cash']}｜投資比 {ex['invested_pct']}%"
                 if ex["invested_pct"] is not None else ""))

    if len(bars_map) >= 2:
        print("-" * 40)
        print("持倉相關係數（揭露假分散）")
        mx = correlation_matrix(bars_map)
        for m in mx:
            cv = "—" if m["corr"] is None else m["corr"]
            print(f"  {m['a']} ↔ {m['b']}: {cv}（{m['n']}日）")
        hi = high_corr_pairs(mx)
        if hi:
            print("  ⚠ 高相關（>=0.7，假分散）: "
                  + "、".join(f"{m['a']}/{m['b']}={m['corr']}" for m in hi))
    print("=" * 40)


if __name__ == "__main__":
    _main()
