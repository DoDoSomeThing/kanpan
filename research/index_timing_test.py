#!/usr/bin/env python3
"""
指數短線擇時 vs 死抱 — 0050 / 正2(00631L) 回測。

問題：看均線進出(短線擇時)會贏「死抱不動」嗎？
做法：訊號用「前一日收盤 vs MAn」(避免 look-ahead)；站上=持有當日報酬、跌破=空手(0%)。
      每次進出收一次交易成本。比 總報酬/年化/最大回撤(MDD)/換手次數。
規則：buy-hold(死抱) vs 收盤>MA10 / >MA20 / >MA60 / >MA120。
誠實：擇時通常「降回撤、但也降報酬」(會錯過反彈日)。看數字自己判。
"""
import json
import ssl
import urllib.request
from datetime import date

START = "2015-01-01"
COST = 0.20   # 每次進出單邊成本 %(ETF 手續~0.14、賣再加稅0.1，取中性 0.2/次)


def fetch(sid):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core import _find_finmind_token
    tok = _find_finmind_token()
    url = ("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice"
           f"&data_id={sid}&start_date={START}&end_date={date.today().isoformat()}&token={tok}")
    r = json.load(urllib.request.urlopen(url, timeout=60, context=ssl.create_default_context()))
    d = sorted(r.get("data", []), key=lambda x: x["date"])
    s = [(x["date"], x["close"]) for x in d if x.get("close")]
    return _fix_splits(s)


def _fix_splits(s):
    """偵測分割/異常跳動(單日 >35%)→ 把前段價接回，去除假摔/假漲。"""
    px = [c for _, c in s]
    for i in range(len(px) - 1, 0, -1):
        if px[i - 1] <= 0:
            continue
        ratio = px[i] / px[i - 1]
        if ratio < 0.65 or ratio > 1.5:        # 單日 -35%↓/+50%↑ ≈ 分割，非真行情
            factor = px[i] / px[i - 1]
            for j in range(i):                  # 把分割點之前全部乘上 factor 接續
                px[j] *= factor
    return [(s[i][0], px[i]) for i in range(len(s))]


def sma(v, n, i):
    if i + 1 < n:
        return None
    return sum(v[i - n + 1:i + 1]) / n


def run(series, label):
    dates = [d for d, _ in series]
    px = [c for _, c in series]
    n = len(px)
    rets = [0.0] + [(px[i] / px[i - 1] - 1) for i in range(1, n)]

    def equity(rule):
        eq = 1.0
        peak = 1.0
        mdd = 0.0
        switches = 0
        prev_in = False
        for i in range(1, n):
            if rule is None:        # 死抱
                in_mkt = True
            else:
                ma = sma(px, rule, i - 1)        # 用前一日 MA
                in_mkt = ma is not None and px[i - 1] > ma
            if in_mkt != prev_in:               # 進出 → 收成本
                eq *= (1 - COST / 100)
                switches += 1
                prev_in = in_mkt
            if in_mkt:
                eq *= (1 + rets[i])
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1)
        years = n / 250
        cagr = (eq ** (1 / years) - 1) * 100 if eq > 0 else -100
        return eq, (eq - 1) * 100, cagr, mdd * 100, switches

    print(f"\n===== {label}（{dates[0]}~{dates[-1]}, {n}日, 成本{COST}%/次）=====")
    print(f"{'策略':<14}{'總報酬':>9}{'年化':>8}{'最大回撤':>9}{'換手次數':>8}")
    rules = [("死抱 buy-hold", None), ("收盤>MA10", 10), ("收盤>MA20", 20),
             ("收盤>MA60", 60), ("收盤>MA120", 120)]
    for name, r in rules:
        _, tot, cagr, mdd, sw = equity(r)
        print(f"{name:<14}{tot:>+8.0f}%{cagr:>+7.1f}%{mdd:>+8.1f}%{sw:>8}")


def main():
    for sid, label in [("0050", "0050 台灣50"), ("00631L", "00631L 正2(2倍)")]:
        try:
            s = fetch(sid)
            if len(s) > 130:
                run(s, f"{label}")
            else:
                print(f"{label}: 資料不足")
        except Exception as e:
            print(f"{label}: 抓取失敗 {e}")


if __name__ == "__main__":
    main()
