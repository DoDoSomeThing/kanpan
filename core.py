#!/usr/bin/env python3
"""
kanpan core — 台股看盤面板核心（2026-06-12）

理念：不預測漲跌，只回答——
  現在趨勢如何？什麼結構？動能強不強？量能如何？歷史上類似狀況表現如何？
輸出是「風險評估 / 趨勢評估 / 歷史勝率參考」，絕不輸出買進/賣出。

四區塊 + 綜合：
  Trend Score  0~100（均線結構，5 條件各 20 分）
  Structure    底部/起漲/主升段/多頭修正/空頭/突破（規則優先序判定）
  Momentum     RSI14 分級（強/普通/偏弱/弱）
  Volume       今量 / 20日均量 倍數（放量/正常/量縮）
  VP Score     Trend 40% + Momentum 20% + Volume 20% + Position 20%
"""
import gzip
import json
import os

# ---------- 資料載入 ----------

def load_bars(sid: str, cache_path: str) -> list:
    """讀 K 線 cache（.json 或 .json.gz），統一欄位，按日期排序。
    cache 格式：{sid: [{date,open,high/max,low/min,close,volume}, ...]}"""
    if cache_path.endswith(".gz"):
        d = json.load(gzip.open(cache_path))
    else:
        d = json.load(open(cache_path, encoding="utf-8"))
    rows = d.get(sid)
    if not rows:
        raise KeyError(f"cache 沒有 {sid}")
    out = []
    for r in rows:
        out.append({
            "date":   r["date"],
            "open":   r.get("open"),
            "high":   r.get("high", r.get("max")),
            "low":    r.get("low", r.get("min")),
            "close":  r["close"],
            "volume": r.get("volume", 0) or 0,
        })
    return sorted(out, key=lambda x: x["date"])


# ---------- 指標 ----------

def sma(vals: list, n: int):
    """簡單均線序列；前 n-1 根為 None。"""
    out = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def rsi14(closes: list, n: int = 14):
    """Wilder RSI 序列；前 n 根為 None。"""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


# ---------- 區塊 1：Trend Score ----------

def trend_score(c, ma5, ma10, ma20, ma60, ma120) -> int:
    """0~100，5 條件各 20 分。任一均線缺(資料不足)該條件 0 分。"""
    s = 0
    if ma20 and c > ma20:
        s += 20
    if ma20 and ma60 and ma20 > ma60:
        s += 20
    if ma60 and ma120 and ma60 > ma120:
        s += 20
    if ma10 and c > ma10:
        s += 20
    if ma5 and c > ma5:
        s += 20
    return s


# ---------- 區塊 2：Structure ----------

def structure(c, ma5, ma10, ma20, ma60, ma120, high60, low60) -> str:
    """市場結構（優先序由強訊號往下判）：
    突破 > 主升段 > 起漲 > 多頭修正 > 多頭 > 底部 > 空頭 > 盤整
    """
    if not (ma20 and ma60):
        return "資料不足"
    bull_stack  = ma20 > ma60
    above_ma20  = c > ma20

    # 突破：創 60 日新高（含今天）
    if high60 is not None and c >= high60:
        return "突破"
    # 主升段：完整多排 + 價在所有均線上
    if (ma120 and ma60 > ma120 and bull_stack and above_ma20
            and ma10 and c > ma10):
        return "主升段"
    # 起漲：站回 ma20、ma20 仍低於 ma60（剛轉強，均線還沒翻）
    if above_ma20 and not bull_stack:
        return "起漲"
    # 多頭修正：多頭結構在，但短線跌破 ma10
    if above_ma20 and bull_stack and ma10 and c < ma10:
        return "多頭修正"
    # 多頭：站上 ma20 + 多排
    if above_ma20 and bull_stack:
        return "多頭"
    # 底部：價低於 ma20 但已接近 60 日低點止穩（離低點 <5%）
    if (not above_ma20) and low60 and low60 > 0 and (c / low60 - 1) < 0.05:
        return "底部"
    # 空頭：價破 ma20 + 空排
    if (not above_ma20) and ma20 < ma60:
        return "空頭"
    return "盤整"


# ---------- 區塊 3：Momentum ----------

def momentum(rsi):
    """回 (rsi, 分級文字)。"""
    if rsi is None:
        return None, "資料不足"
    if rsi > 60:
        return rsi, "強"
    if rsi >= 50:
        return rsi, "普通"
    if rsi >= 40:
        return rsi, "偏弱"
    return rsi, "弱"


def momentum_score(rsi) -> float:
    """RSI → 0~100（VP Score 用）。50 為中性映射。"""
    if rsi is None:
        return 50.0
    return max(0.0, min(100.0, rsi))


# ---------- 區塊 4：Volume ----------

def volume_block(vol, vol20):
    """回 (倍數, 分級文字)。"""
    if not vol20:
        return None, "資料不足"
    ratio = vol / vol20
    if ratio >= 1.5:
        tag = "放量"
    elif ratio <= 0.7:
        tag = "量縮"
    else:
        tag = "正常"
    return round(ratio, 2), tag


def volume_score(ratio) -> float:
    """量能 → 0~100。1.0 倍=50；放量加分、極端爆量(>3x)打折(可能出貨)。"""
    if ratio is None:
        return 50.0
    if ratio > 3.0:
        return 60.0          # 爆量不給滿，歷史高檔爆量常是出貨
    return max(0.0, min(100.0, ratio * 50.0))


# ---------- Position（VP Score 第四成分）----------

def position_score(c, low60, high60) -> float:
    """價格在 60 日區間的位置 0~100（低=便宜側、高=貴側）。
    分數設計：中上段(40~80%)最健康給高分；貼頂(>95%)回落、破底(<10%)低分。"""
    if not (low60 and high60) or high60 <= low60:
        return 50.0
    pct = (c - low60) / (high60 - low60) * 100   # 0=60日低點 100=高點
    if pct >= 95:
        return 70.0          # 貼頂：強但追高險，打折
    if pct >= 40:
        return 80.0 + (pct - 40) / 55 * 15       # 40~95 → 80~95
    if pct >= 10:
        return 40.0 + (pct - 10) / 30 * 40       # 10~40 → 40~80
    return 20.0              # 破底側

def position_pct(c, low60, high60):
    if not (low60 and high60) or high60 <= low60:
        return None
    return round((c - low60) / (high60 - low60) * 100, 1)


# ---------- VP Score ----------

W_TREND, W_MOM, W_VOL, W_POS = 0.4, 0.2, 0.2, 0.2

def vp_score(t_score, rsi, vol_ratio, c, low60, high60) -> int:
    s = (t_score * W_TREND
         + momentum_score(rsi) * W_MOM
         + volume_score(vol_ratio) * W_VOL
         + position_score(c, low60, high60) * W_POS)
    return int(round(s))


# ---------- 整合：算一檔的面板 ----------

def compute_panel(bars: list, i: int = -1) -> dict:
    """對 bars 的第 i 根（預設最新）算完整面板 dict。
    需要至少 ~120 根才有 ma120；不足時部分欄位 None/資料不足。"""
    closes = [b["close"] for b in bars]
    vols   = [b["volume"] for b in bars]
    n = len(bars)
    idx = i if i >= 0 else n + i

    ma5   = sma(closes, 5)[idx]
    ma10  = sma(closes, 10)[idx]
    ma20  = sma(closes, 20)[idx]
    ma60  = sma(closes, 60)[idx]
    ma120 = sma(closes, 120)[idx]
    rsi   = rsi14(closes)[idx]
    vol20 = sma(vols, 20)[idx]

    lo = max(0, idx - 59)
    win = bars[lo:idx + 1]
    high60 = max(b["high"] for b in win if b["high"] is not None)
    low60  = min(b["low"] for b in win if b["low"] is not None)

    c = closes[idx]
    t = trend_score(c, ma5, ma10, ma20, ma60, ma120)
    st = structure(c, ma5, ma10, ma20, ma60, ma120, high60, low60)
    r, mtag = momentum(rsi)
    vr, vtag = volume_block(vols[idx], vol20)
    score = vp_score(t, rsi, vr, c, low60, high60)

    return {
        "date": bars[idx]["date"], "close": round(c, 2),
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma120": ma120,
        "trend_score": t,
        "structure": st,
        "rsi": round(r, 1) if r is not None else None, "momentum": mtag,
        "vol_ratio": vr, "vol_tag": vtag,
        "pos_pct": position_pct(c, low60, high60),
        "high60": high60, "low60": low60,
        "vp_score": score,
    }


# ---------- 評語（規則式，非 LLM、非建議）----------

def comment(p: dict) -> str:
    """規則式評語：描述狀態，不給買賣建議。"""
    lines = []
    st = p["structure"]
    t = p["trend_score"]
    if st == "主升段":
        lines.append("趨勢結構完整，處於主升段。")
    elif st == "多頭修正":
        lines.append("中期多頭未破壞，短線處於修正階段。")
    elif st == "突破":
        lines.append("價格創 60 日新高，突破階段（留意是否帶量）。")
    elif st == "起漲":
        lines.append("剛站回月線，均線尚未翻多，初步轉強待確認。")
    elif st == "空頭":
        lines.append("空頭結構，支撐已失，風險高。")
    elif st == "底部":
        lines.append("接近 60 日低點區，止穩與否待確認。")
    else:
        lines.append(f"目前結構：{st}。")
    if p["momentum"] in ("偏弱", "弱"):
        lines.append("動能偏弱，等待動能恢復。")
    elif p["rsi"] is not None and p["rsi"] > 70:
        lines.append("RSI 過熱，短線追高風險高。")
    if p["vol_tag"] == "量縮":
        lines.append("量能萎縮，觀望氣氛。")
    elif p["vol_tag"] == "放量":
        lines.append("量能放大，留意位置（低檔放量與高檔放量意義相反）。")
    return "\n".join(lines)
