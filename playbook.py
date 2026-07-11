#!/usr/bin/env python3
"""
playbook — L2 規則化劇本引擎（V2 Phase 3）核心。

三個**固定**模板（禁任意組合，擋資料探勘）＋觸發判定＋防呆顯示。
模板條件在 `fires()` 一處定義，回測(research/playbook_history.py)與即時面板共用，
保證「歷史驗的」與「今天判的」是同一套規則。

四大原則對齊：不預測、顯示歷史、無回測=無意見、風控優先。
引擎價值＝誠實證明多數型態沒 edge、勸退亂進場（非找神劇本）。
"""
import json
import os

from core import sma

HERE = os.path.dirname(os.path.abspath(__file__))
STATS_PATH = os.path.join(HERE, "research", "playbook_stats.json")

# 模板名稱 → 人話條件（顯示用；實際判定在 fires()）
TEMPLATES = {
    "突破型": "收盤 > 近20日高 且 量 > 1.2×MV20",
    "跌破型": "收盤 < 近20日低",
    "回測型": "前根<MA20、本根≥MA20（站回）且 量 ≥ MV20",
}

BREAKOUT_VOL_MULT = 1.2     # 突破型量能門檻：量 > 此倍數 × MV20
LOOKBACK = 20               # 近 N 日高/低（不含當日）

# 防呆門檻（spec Phase 3，三條缺一不可）
MIN_SAMPLE = 30             # 樣本 < 此 → 樣本不足，不給勝率
OVERFIT_GAP = 10.0          # 訓練勝率 − 驗證勝率 > 此(pp) → 過擬合警告
EDGE_WR = 50.0             # 驗證勝率 < 此 → 視為「型態無 edge」


def fires(bars, closes, vols, m20, v20, i):
    """回傳 index i 這根觸發的模板名稱 list。
    bars 需含 high/low（容忍 max/min）；m20/v20 為已算好的 sma 序列。
    i 須 >= LOOKBACK 且各 MA 不為 None，否則回 []。"""
    if i < LOOKBACK or i < 1:
        return []
    c = closes[i]
    if not c or c <= 0:
        return []
    out = []

    def hi(b):
        return b.get("high", b.get("max"))

    def lo(b):
        return b.get("low", b.get("min"))

    win = bars[i - LOOKBACK:i]                      # 近20根（不含當日）
    highs = [hi(b) for b in win if hi(b) is not None]
    lows = [lo(b) for b in win if lo(b) is not None]
    vr_ok_break = (v20[i] and vols[i] > BREAKOUT_VOL_MULT * v20[i])
    vr_ok_pull = (v20[i] and vols[i] >= v20[i])

    # 突破型：收盤創近20高 且 帶量
    if highs and c > max(highs) and vr_ok_break:
        out.append("突破型")
    # 跌破型：收盤破近20低（不綁量，下跌常縮量）
    if lows and c < min(lows):
        out.append("跌破型")
    # 回測型：站回 MA20（前根在下、本根在上）且量不萎縮
    if (m20[i] is not None and m20[i - 1] is not None
            and closes[i - 1] < m20[i - 1] and c >= m20[i] and vr_ok_pull):
        out.append("回測型")
    return out


def detect_playbook(bars, idx=-1):
    """對 bars 的第 idx 根（預設最新已收盤）判觸發哪些模板。純函式、即時用。"""
    n = len(bars)
    i = idx if idx >= 0 else n + idx
    if i < LOOKBACK:
        return []
    closes = [b["close"] for b in bars]
    vols = [b.get("volume", 0) or 0 for b in bars]
    m20 = sma(closes, 20)
    v20 = sma(vols, 20)
    return fires(bars, closes, vols, m20, v20, i)


def load_stats(path=STATS_PATH):
    """讀回測結果；沒有回 None（面板顯示『未回測』）。"""
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def _guard(t):
    """對單一模板的回測 dict 套三道防呆，回 (status, msg)。
    status: ok / low_sample / unvalidated / overfit / no_edge"""
    val = t.get("val") or {}
    tr = t.get("train") or {}
    nv = val.get("n", 0)
    if t.get("n", 0) < MIN_SAMPLE:
        return "low_sample", "樣本不足，不給勝率"
    if nv == 0:
        return "unvalidated", "無樣本外，未驗證"
    if tr.get("win20") is not None and val.get("win20") is not None \
            and (tr["win20"] - val["win20"]) > OVERFIT_GAP:
        return "overfit", f"過擬合警告（訓練{tr['win20']}% vs 驗證{val['win20']}%）"
    if val.get("win20") is not None and val["win20"] < EDGE_WR:
        return "no_edge", f"型態無 edge（驗證勝率{val['win20']}%<50%）"
    return "ok", ""


def playbook_view(fired, stats):
    """合成面板用 dict：今日觸發的模板 + 回測數字 + 防呆 + L2.5 不交易原因。
    fired = detect_playbook() 結果；stats = load_stats()。
    回 {fired, cards, no_trade}。cards 一模板一張。"""
    cards = []
    reasons = []        # L2.5 不交易原因（勾選清單）
    tmpls = (stats or {}).get("templates", {})

    if not fired:
        reasons.append({"label": "條件未成立（無模板觸發）", "on": True})

    for name in fired:
        t = tmpls.get(name)
        if not t:
            cards.append({"name": name, "cond": TEMPLATES.get(name, ""),
                          "status": "unvalidated", "msg": "未回測", "stat": None})
            reasons.append({"label": f"{name}：未回測", "on": True})
            continue
        status, msg = _guard(t)
        val = t.get("val") or {}
        cards.append({
            "name": name, "cond": t.get("cond", TEMPLATES.get(name, "")),
            "status": status, "msg": msg,
            "stat": {
                "n_train": (t.get("train") or {}).get("n", 0),
                "win_train": (t.get("train") or {}).get("win20"),
                "n_val": val.get("n", 0),
                "win_val": val.get("win20"),
                "avg20": t.get("avg20"),
                "mdd": t.get("mdd"),
            },
        })
        if status == "low_sample":
            reasons.append({"label": f"{name}：樣本不足", "on": True})
        elif status == "overfit":
            reasons.append({"label": f"{name}：過擬合", "on": True})
        elif status == "no_edge":
            reasons.append({"label": f"{name}：型態無 edge（勝率<50%）", "on": True})
        elif status == "unvalidated":
            reasons.append({"label": f"{name}：未驗證", "on": True})

    return {"fired": fired, "cards": cards, "no_trade": reasons}
