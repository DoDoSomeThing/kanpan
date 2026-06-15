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

from core import compute_panel, _find_finmind_token, _recent_trading_day, _norm

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache", "kline_cache.json.gz")
EXTRA = os.path.join(HERE, "cache", "extra")
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


def _load_extra() -> dict:
    """讀 cache/extra/*.json（panel 看過的檔當日即時抓的快取，fetched==今日才算新）。
    回 {sid: bars}。讓全市場排行對「看過的檔」用新資料，免額外打 FinMind。"""
    from datetime import date
    today = date.today().isoformat()
    fresh = {}
    if not os.path.isdir(EXTRA):
        return fresh
    for fn in os.listdir(EXTRA):
        if not fn.endswith(".json"):
            continue
        try:
            obj = json.loads(open(os.path.join(EXTRA, fn), encoding="utf-8").read())
            if obj.get("fetched") == today and obj.get("bars"):
                fresh[fn[:-5]] = obj["bars"]
        except Exception:
            pass
    return fresh


def scan():
    """回 (rows, meta)。rows=[(sid,score,structure,rsi),...] 依分數降冪。
    meta=cache 新鮮度資訊（最後日期/過期交易日數/套用幾檔 extra），給 main 印橫幅。"""
    d = json.load(gzip.open(CACHE)) if CACHE.endswith(".gz") else json.load(open(CACHE))
    extra = _load_extra()
    out = []
    cache_max = ""
    extra_used = 0
    for sid, rows in d.items():
        if sid in extra:                     # 當日即時快取優先（新）
            bars = extra[sid]
            extra_used += 1
        else:
            if len(rows) < 130:              # 不足算不出 ma120
                continue
            bars = _norm(rows)
        if bars:
            cache_max = max(cache_max, bars[-1]["date"][:10])
        if len(bars) < 130:
            continue
        try:
            p = compute_panel(bars)
        except Exception:
            continue
        s = p.get("vp_score")
        if s is not None:
            out.append((sid, s, p.get("structure", ""), p.get("rsi")))
    out.sort(key=lambda x: -x[1])
    meta = {"cache_max": cache_max, "extra_used": extra_used,
            "recent": _recent_trading_day(), "stale": cache_max < _recent_trading_day()}
    return out, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=0, help="只列 N 分以上")
    ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()

    rows, meta = scan()
    names = load_names()
    vals = [s for _, s, _, _ in rows]
    print(f"\n掃描 {len(vals)} 檔（上市 cache）")

    # 新鮮度橫幅：cache 過期就講清楚，別把舊排行當現況
    if meta["stale"]:
        print(f"[過期] cache 最後日期 {meta['cache_max']}（最近交易日 {meta['recent']}）"
              f"｜排行多為 cache 收盤、僅供參考")
    else:
        print(f"cache 最後日期 {meta['cache_max']}（最新）")
    if meta["extra_used"]:
        print(f"   已套用 {meta['extra_used']} 檔當日即時快取（看過的檔為新）")

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


if __name__ == "__main__":
    main()
