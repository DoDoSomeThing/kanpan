#!/usr/bin/env python3
"""
kanpan review — 交易檢討（抽取自 anti-gambling-trader 方法論）

吃平倉紀錄（positions.json 的 closed），做三件「單股面板看不到」的誠實體檢：
  1. 樣本外驗證  out_of_sample — 前段賺不算數，沒看過的後段也賺才是真本事。
  2. 各策略體檢  per_tag       — 按出場原因拆開算期望值，揪出哪一招在送錢 → 砍掉。
  3. 賭博特徵掃  gambling_flags— 負期望 / 獲利集中 / 長連虧 / 賺小賠大（盈虧比<1）。

純函式（給 test 釘）。只描述、不預測。closed 紀錄欄位：
  {sid, entry_price, exit_price, entry_date, exit_date, exit_reason, return_pct, hold_days}
"""

# 門檻（保守預設）
CONCENTRATION = 0.40    # 最賺一筆 ≥ 總獲利 40% = 獲利集中
LOSE_STREAK = 5         # 連虧 ≥ 5 次 = 警示
DECAY_BAD = 0.5         # 樣本外期望值 < 樣本內 ×0.5（或翻負）= 衰退


def _by_date(closed):
    return sorted(closed, key=lambda r: r.get("exit_date") or "")


def _mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


# ---------- 1. 樣本外驗證 ----------
def out_of_sample(closed, split=0.7):
    """依出場日時序切前 split / 後段，比期望值（平均 return_pct）。
    回 verdict：持續 / 衰退 / 翻負 / 樣本不足。"""
    rs = [r for r in _by_date(closed) if r.get("return_pct") is not None]
    n = len(rs)
    if n < 6:
        return {"n": n, "verdict": "樣本不足", "in_exp": None, "out_exp": None}
    k = max(1, int(n * split))
    in_rs = [r["return_pct"] for r in rs[:k]]
    out_rs = [r["return_pct"] for r in rs[k:]]
    if not out_rs:
        return {"n": n, "verdict": "樣本不足", "in_exp": None, "out_exp": None}
    ie, oe = _mean(in_rs), _mean(out_rs)
    if oe is None or ie is None:
        verdict = "樣本不足"
    elif oe <= 0 < ie:
        verdict = "翻負（過擬合徵兆）"
    elif ie > 0 and oe < ie * DECAY_BAD:
        verdict = "大幅衰退"
    elif ie <= 0:
        verdict = "前段就不賺"
    else:
        verdict = "持續（相對穩）"
    return {"n": n, "in_n": len(in_rs), "out_n": len(out_rs),
            "in_exp": ie, "out_exp": oe, "verdict": verdict}


# ---------- 2. 各策略體檢 ----------
def per_tag(closed, key="exit_reason"):
    """按 key（預設出場原因）分組算期望值，由最差到最好。
    每組：{tag, n, expectancy, win_rate, verdict}。"""
    groups = {}
    for r in closed:
        if r.get("return_pct") is None:
            continue
        groups.setdefault(r.get(key) or "未標", []).append(r["return_pct"])
    out = []
    for tag, rs in groups.items():
        exp = _mean(rs)
        wins = sum(1 for x in rs if x > 0)
        out.append({
            "tag": tag, "n": len(rs), "expectancy": exp,
            "win_rate": round(wins / len(rs) * 100, 1),
            "verdict": ("🟥 賠錢招" if exp is not None and exp <= 0
                        else "🟩 賺錢招"),
        })
    out.sort(key=lambda g: (g["expectancy"] is not None, g["expectancy"]))
    return out


# ---------- 3. 賭博特徵掃 ----------
def _max_lose_streak(seq):
    best = cur = 0
    for x in seq:
        if x < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def gambling_flags(closed):
    """掃賭博/風險特徵，回警示 list（空=乾淨）。每筆 {level, code, msg}。"""
    rs = [r["return_pct"] for r in _by_date(closed) if r.get("return_pct") is not None]
    flags = []
    if len(rs) < 3:
        return flags
    exp = _mean(rs)
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    # 負期望
    if exp is not None and exp <= 0:
        flags.append({"level": "high", "code": "neg_exp",
                      "msg": f"每筆期望值為負（{exp:+.2f}%）— 長期數學上注定虧。"})
    # 獲利集中：最賺一筆佔總獲利比
    tot_win = sum(wins)
    if tot_win > 0:
        share = max(wins) / tot_win
        if share >= CONCENTRATION:
            flags.append({"level": "mid", "code": "concentration",
                          "msg": f"最賺一筆佔總獲利 {share*100:.0f}% — 獲利集中，"
                                 f"抽掉那筆可能就不賺。"})
    # 長連虧
    streak = _max_lose_streak(rs)
    if streak >= LOSE_STREAK:
        flags.append({"level": "mid", "code": "lose_streak",
                      "msg": f"曾連虧 {streak} 次 — 守得住紀律嗎?"})
    # 賺小賠大：盈虧比 < 1（平均賺 < 平均賠）
    if wins and losses:
        aw, al = _mean(wins), abs(_mean(losses))
        if al > 0 and aw / al < 1.0:
            flags.append({"level": "low", "code": "small_win_big_loss",
                          "msg": f"盈虧比 {aw/al:.2f}<1（平均賺{aw:.1f}% < 平均賠{al:.1f}%）"
                                 f"— 賺小賠大，靠勝率硬撐。"})
    return flags


def review(closed):
    """彙整三項，給 api/CLI 一次取。"""
    return {
        "out_of_sample": out_of_sample(closed),
        "per_tag": per_tag(closed),
        "gambling_flags": gambling_flags(closed),
    }
