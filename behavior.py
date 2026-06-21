#!/usr/bin/env python3
"""
kanpan behavior — 行為守門（V2 / ROADMAP P3）

對應用戶弱點（有持倉、手癢想短線）。只警示，不下買賣指令。
全純函式（給 test 釘）：吃面板/風控/平倉歷史，回警示 list。

警示 dict：{level: "warn"|"info", code, msg}
原則：唯一驗過的真 edge = trail 出場，別違背；過度交易摩擦吃報酬；追高期望值差。
"""
from datetime import datetime

# 門檻（保守預設，之後可調）
CHASE_BIAS = 15.0     # 月線正乖離 >= 15% = 偏離過大
CHASE_POS = 85.0      # 60 日區間位置 >= 85% = 接近天花板
FREQ_WINDOW = 30      # 頻率視窗（日）
FREQ_MAX = 4          # 視窗內進出（平倉）> 此數 = 過度交易


def chase_warning(p, has_position):
    """追高警示：尚無持倉、想進場時，價已偏離過大 / 貼近區間頂 → 期望值差。
    已持倉者不發（避免對抱單者誤報）。p 為 compute_panel 結果。"""
    if has_position or not p:
        return None
    bias20 = p.get("bias20")
    pos_pct = p.get("pos_pct")
    reasons = []
    if bias20 is not None and bias20 >= CHASE_BIAS:
        reasons.append(f"月線正乖離 {bias20:+.1f}%（追高險）")
    if pos_pct is not None and pos_pct >= CHASE_POS:
        reasons.append(f"60日區間位置 {pos_pct:.0f}%（貼近天花板）")
    if not reasons:
        return None
    return {"level": "warn", "code": "chase",
            "msg": "追高警示：" + "、".join(reasons) + "，此處進場期望值差，離套牢區近。"}


def hold_loser_warning(risk):
    """凹單偵測：已跌破生效出場（🔴 已觸發）卻仍持有 → 提醒。
    risk 為 position.position_risk 結果（None 表無持倉）。"""
    if not risk:
        return None
    if risk.get("light") == "🔴" or risk.get("state") == "已觸發":
        return {"level": "warn", "code": "hold_loser",
                "msg": (f"凹單偵測：現價 {risk.get('cur_price')} 已跌破生效出場 "
                        f"{risk.get('effective_exit')}（{risk.get('effective_by')}）。"
                        f"唯一驗過的真 edge = trail 出場，別凹。")}
    return None


def _d10(s):
    return (s or "")[:10]


def frequency_warning(closed_records, ref_date=None,
                      window=FREQ_WINDOW, max_trades=FREQ_MAX):
    """頻率警示：近 window 日內平倉筆數 > max_trades → 過度交易，摩擦成本吃報酬。
    **全域**：應餵『全部』平倉紀錄（跨檔），非單一 sid，否則抓不到整體頻繁進出。
    ref_date None → 用 closed 中最晚 exit_date 當基準。"""
    dates = [_d10(r.get("exit_date")) for r in closed_records if r.get("exit_date")]
    dates = [d for d in dates if d]
    if not dates:
        return None
    ref = _d10(ref_date) if ref_date else max(dates)
    try:
        ref_dt = datetime.strptime(ref, "%Y-%m-%d")
    except Exception:
        return None
    cnt = 0
    for d in dates:
        try:
            if 0 <= (ref_dt - datetime.strptime(d, "%Y-%m-%d")).days <= window:
                cnt += 1
        except Exception:
            continue
    if cnt > max_trades:
        return {"level": "warn", "code": "frequency",
                "msg": (f"頻率警示：近 {window} 日內平倉 {cnt} 次（>{max_trades}），"
                        f"過度交易，摩擦成本（手續費+稅+滑價）會吃掉報酬。")}
    return None


def behavior_checks(p=None, risk=None, closed_records=None, ref_date=None):
    """彙整三道守門，回警示 list（空 list = 無警示）。
    p: 面板 dict；risk: 該檔持倉風控；closed_records: 平倉歷史。"""
    has_pos = risk is not None
    out = []
    for w in (chase_warning(p, has_pos) if p else None,
              hold_loser_warning(risk),
              frequency_warning(closed_records or [], ref_date)):
        if w:
            out.append(w)
    return out
