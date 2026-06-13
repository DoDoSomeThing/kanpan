#!/usr/bin/env python3
"""
理由卡 — 買進前寫下「為什麼買 + 什麼情況算理由死了」，每天 check 自動提醒。

理念（來自操盤手紀律）：判斷留給人、執行紀律交給程式。
  進攻（為什麼買）＝人的功課，程式不插手；
  出場（理由死了）＝寫成可檢查條件，程式每天盯，死了就叫你執行。
個股別用機械停損線（回測證明被洗爆），用「當初買的理由還在不在」。

可程式檢查的失效條件：
  --price-below X     收盤跌破 X 元
  --below-ma maN      收盤跌破均線（ma20 月線 / ma60 季線 / ma120 半年線）
  --structure-bear    結構轉成空頭
  --rsi-below X       RSI 跌破 X
另存自由文字 note（人工判斷項，如「族群轉弱」），check 時一併提醒你看一眼。

用法：
  python reason.py add 2330 --why "AI訂單成長,CoWoS滿載" --below-ma ma60 --price-below 900 --note "族群轉弱也走"
  python reason.py list
  python reason.py check        # 每天跑：列每張卡理由活著/⚠️死了
  python reason.py rm 2330
"""
import argparse
import json
import os

from core import load_bars, compute_panel

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache", "kline_cache.json.gz")
CARDS = os.path.join(HERE, "cards.json")


def _load():
    return json.load(open(CARDS, encoding="utf-8")) if os.path.exists(CARDS) else {}


def _save(cards):
    json.dump(cards, open(CARDS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def cmd_add(a):
    cards = _load()
    cards[a.sid] = {
        "why": a.why or "",
        "rules": {"price_below": a.price_below, "below_ma": a.below_ma,
                  "structure_bear": a.structure_bear, "rsi_below": a.rsi_below},
        "note": a.note or "",
        "created": __import__("datetime").date.today().isoformat(),
    }
    _save(cards)
    print(f"已建理由卡 {a.sid}：{a.why}")


def cmd_rm(a):
    cards = _load()
    if cards.pop(a.sid, None):
        _save(cards); print(f"已刪 {a.sid}")
    else:
        print(f"沒有 {a.sid} 的卡")


def cmd_list(a):
    cards = _load()
    if not cards:
        print("（還沒有理由卡，用 reason.py add 建立）"); return
    for sid, c in cards.items():
        print(f"\n[{sid}] 建於 {c['created']}")
        print(f"  為什麼買：{c['why']}")
        print(f"  失效條件：{_rules_str(c['rules'])}")
        if c["note"]:
            print(f"  人工注意：{c['note']}")


def _rules_str(r):
    parts = []
    if r.get("price_below") is not None:
        parts.append(f"跌破 {r['price_below']} 元")
    if r.get("below_ma"):
        parts.append(f"跌破{ {'ma20':'月線','ma60':'季線','ma120':'半年線'}.get(r['below_ma'], r['below_ma']) }")
    if r.get("structure_bear"):
        parts.append("結構轉空頭")
    if r.get("rsi_below") is not None:
        parts.append(f"RSI<{r['rsi_below']}")
    return "、".join(parts) or "（無自動條件，只靠人工注意）"


def _check_one(sid, rules):
    """回 (dead:bool, hits:list, panel)。抓不到資料回 (None, [理由], None)。"""
    try:
        bars = load_bars(sid, CACHE)
        p = compute_panel(bars)
    except Exception as e:
        return None, [f"查無資料：{e}"], None
    hits = []
    c = p.get("close")
    if rules.get("price_below") is not None and c is not None and c < rules["price_below"]:
        hits.append(f"跌破 {rules['price_below']}（現 {c}）")
    ma = rules.get("below_ma")
    if ma and p.get(ma) is not None and c is not None and c < p[ma]:
        nm = {"ma20": "月線", "ma60": "季線", "ma120": "半年線"}.get(ma, ma)
        hits.append(f"跌破{nm}（現 {c} < {round(p[ma],1)}）")
    if rules.get("structure_bear") and "空頭" in (p.get("structure") or ""):
        hits.append(f"結構轉空頭（{p['structure']}）")
    if rules.get("rsi_below") is not None and p.get("rsi") is not None and p["rsi"] < rules["rsi_below"]:
        hits.append(f"RSI {p['rsi']} < {rules['rsi_below']}")
    return (len(hits) > 0), hits, p


def cmd_check(a):
    cards = _load()
    if not cards:
        print("（還沒有理由卡）"); return
    print("===== 理由卡每日檢查 =====")
    for sid, c in cards.items():
        dead, hits, p = _check_one(sid, c["rules"])
        if dead is None:
            print(f"\n[{sid}] ⚠️ {hits[0]}"); continue
        if dead:
            print(f"\n[{sid}] 🔴 理由可能死了 — 該檢視出場")
            for h in hits:
                print(f"    觸發：{h}")
        else:
            print(f"\n[{sid}] 🟢 理由還在（現 {p.get('close')}，結構 {p.get('structure')}）")
        if c["note"]:
            print(f"    ⚠️ 人工再確認：{c['note']}")
    print("\n（自動條件只查價格/均線/結構/RSI；族群、基本面等請人工判斷）")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add"); pa.add_argument("sid")
    pa.add_argument("--why", required=True)
    pa.add_argument("--price-below", type=float)
    pa.add_argument("--below-ma", choices=["ma20", "ma60", "ma120"])
    pa.add_argument("--structure-bear", action="store_true")
    pa.add_argument("--rsi-below", type=float)
    pa.add_argument("--note")
    pa.set_defaults(func=cmd_add)

    pr = sub.add_parser("rm"); pr.add_argument("sid"); pr.set_defaults(func=cmd_rm)
    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("check").set_defaults(func=cmd_check)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
