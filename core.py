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
import ssl
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# ---------- 資料載入 ----------

def _norm(rows: list) -> list:
    """統一欄位（容忍 high/max、low/min），按日期排序。"""
    out = []
    for r in rows:
        out.append({
            "date":   r["date"],
            "open":   r.get("open"),
            "high":   r.get("high", r.get("max")),
            "low":    r.get("low", r.get("min")),
            "close":  r["close"],
            "volume": r.get("volume", r.get("Trading_Volume", 0)) or 0,
        })
    return sorted(out, key=lambda x: x["date"])


def _find_finmind_token() -> str:
    """環境變數 > stock-secrets/股票用bot.env（跨工具找，沿用 vp_brief 模式）。"""
    t = os.getenv("FINMIND_TOKEN", "")
    if t:
        return t
    cands = [os.getenv("STOCK_SECRETS_DIR"),
             str(Path.home() / "Desktop" / "Justin" / "stock-secrets")]
    for d in filter(None, cands):
        p = Path(d) / "股票用bot.env"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("FINMIND_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"')
    return ""


def _fetch_finmind(sid: str) -> list:
    """cache 沒有時即時抓 FinMind 日K（上市櫃都有）。回統一格式 bar list；抓不到回 []。"""
    tok = _find_finmind_token()
    start = (date.today() - timedelta(days=400)).isoformat()
    url = ("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice"
           f"&data_id={sid}&start_date={start}&token={tok}")
    try:
        r = json.load(urllib.request.urlopen(url, timeout=30,
                                             context=ssl.create_default_context()))
    except Exception:
        return []
    return _norm(r.get("data", []))


def load_bars(sid: str, cache_path: str) -> list:
    """讀 K 線：主 cache → 本地 extra cache → FinMind 即時抓(上市櫃通吃，存 extra 下次快)。
    cache 格式：{sid: [{date,open,high/max,low/min,close,volume}, ...]}"""
    d = json.load(gzip.open(cache_path)) if cache_path.endswith(".gz") \
        else json.load(open(cache_path, encoding="utf-8"))
    rows = d.get(sid)
    if rows:
        return _norm(rows)

    # 主 cache 沒有（多為上櫃 TPEX）→ 本地 extra cache（當日有效）
    extra = Path(cache_path).parent / "extra" / f"{sid}.json"
    if extra.exists():
        try:
            obj = json.loads(extra.read_text(encoding="utf-8"))
            if obj.get("fetched") == date.today().isoformat() and obj.get("bars"):
                return obj["bars"]
        except Exception:
            pass

    # 即時抓 FinMind，存 extra
    bars = _fetch_finmind(sid)
    if not bars:
        raise KeyError(f"查無 {sid}（cache 與 FinMind 都沒有，確認代號）")
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text(json.dumps({"fetched": date.today().isoformat(), "bars": bars},
                                ensure_ascii=False), encoding="utf-8")
    return bars


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


# ---------- 量價分布（Volume Profile）：算 壓力/中軸/支撐 參考價位 ----------

VP_BINS = 40      # 價格切 40 格
VP_VA   = 0.70    # 價值區涵蓋 70% 成交量

def vp_levels(bars: list):
    """對一段 bars 算量價分布 → (poc中軸, vah壓力, val支撐, skew量堆積比)。
    skew = POC 之上成交量占比（0.5 平衡、>0.58 偏多方堆量、<0.42 偏空方）。"""
    lo = min(b["low"] for b in bars if b["low"] is not None)
    hi = max(b["high"] for b in bars if b["high"] is not None)
    if hi <= lo:
        return bars[-1]["close"], hi, lo, 0.5
    step = (hi - lo) / VP_BINS
    vol = [0.0] * VP_BINS
    for b in bars:
        if b["low"] is None or b["high"] is None:
            continue
        b_lo = max(0, min(VP_BINS - 1, int((b["low"] - lo) / step)))
        b_hi = max(0, min(VP_BINS - 1, int((b["high"] - lo) / step)))
        per = (b["volume"] or 0) / (b_hi - b_lo + 1)
        for j in range(b_lo, b_hi + 1):
            vol[j] += per
    poc_i = max(range(VP_BINS), key=lambda j: vol[j])
    total = sum(vol)
    target = total * VP_VA
    lo_i = hi_i = poc_i
    acc = vol[poc_i]
    while acc < target and (lo_i > 0 or hi_i < VP_BINS - 1):  # 由 POC 往兩側擴張
        up = vol[hi_i + 1] if hi_i < VP_BINS - 1 else -1
        dn = vol[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1; acc += vol[hi_i]
        else:
            lo_i -= 1; acc += vol[lo_i]
    price = lambda j: lo + (j + 0.5) * step
    above = sum(vol[j] for j in range(VP_BINS) if price(j) > price(poc_i))
    skew = above / total if total else 0.5
    return price(poc_i), price(hi_i), price(lo_i), skew


def skew_tag(skew):
    if skew is None:
        return "資料不足"
    if skew > 0.58:
        return "量偏多方堆積"
    if skew < 0.42:
        return "量偏空方堆積"
    return "量能均衡"


# ---------- 週線趨勢 + 日週共振 ----------

def weekly_trend(bars: list):
    """日K 聚成週K(ISO週取最後收盤)，週收盤 vs 週MA10。
    回 (週線文字, w_up bool 或 None資料不足)。"""
    import datetime as _dt
    weeks = {}
    for b in bars:
        try:
            d = _dt.date.fromisoformat(b["date"][:10])
        except ValueError:
            continue
        iso = d.isocalendar()
        weeks[(iso[0], iso[1])] = b["close"]
    wcloses = [weeks[k] for k in sorted(weeks)]
    if len(wcloses) < 11:
        return "週期不足", None
    wma10 = sum(wcloses[-10:]) / 10
    w_up = wcloses[-1] > wma10
    return ("週線多頭(站上週MA10)" if w_up else "週線空頭(跌破週MA10)"), w_up


def resonance(d_up, w_up):
    """日週共振：兩個時間框同向=訊號較強。"""
    if w_up is None:
        return "週期不足"
    if d_up and w_up:
        return "日週同步偏多（共振）"
    if (not d_up) and (not w_up):
        return "日週同步偏空（共振）"
    return "日週分歧，方向未定"


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

def round_level(price: float):
    """最近的心理整數關卡（依價位大小調整級距）。"""
    if not price or price <= 0:
        return None
    step = 5 if price < 100 else 10 if price < 1000 else 50
    return round(price / step) * step


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

    # 量價分布參考位（近60日窗）+ 量堆積方向
    try:
        poc, vah, val, sk = vp_levels(win)
        poc, vah, val = round(poc, 2), round(vah, 2), round(val, 2)
        sk = round(sk, 2)
    except (ValueError, ZeroDivisionError):
        poc = vah = val = sk = None

    # 週線趨勢 + 日週共振
    wk_txt, w_up = weekly_trend(bars[:idx + 1])
    d_up = bool(ma20 and c > ma20)
    reso = resonance(d_up, w_up)

    # 收盤位置 CCP（當日收在高低區間哪 0~100）
    bn = bars[idx]
    hl = (bn["high"] - bn["low"]) if (bn["high"] is not None and bn["low"] is not None) else 0
    ccp = round((c - bn["low"]) / hl * 100) if hl > 0 else None
    ccp_tag = (None if ccp is None else
               "收高檔(買盤強收)" if ccp >= 70 else "收低檔(賣壓收尾)" if ccp <= 30 else "收中段(多空拉鋸)")

    # 整數關卡（最近心理關卡 + 距離%）
    rl = round_level(c)
    rl_dist = round((c - rl) / rl * 100, 1) if rl else None
    rl_tag = (None if rl_dist is None else
              "貼近關卡(效應強)" if abs(rl_dist) < 0.7 else
              "接近關卡" if abs(rl_dist) < 1.5 else "離關卡遠")

    # 動態 vs 靜態 POC 一致性（短窗 vs 60日窗）
    dyn_poc = None
    try:
        lo2 = max(0, idx - 19)
        dyn_poc = round(vp_levels(bars[lo2:idx + 1])[0], 2)
    except (ValueError, ZeroDivisionError):
        pass
    poc_consist = round(abs(dyn_poc - poc) / poc * 100, 1) if (dyn_poc and poc) else None
    poc_tag = (None if poc_consist is None else
               "共識穩定" if poc_consist < 1.0 else "POC分歧(換手中)")

    p = {
        "date": bars[idx]["date"], "close": round(c, 2),
        "open": round(bn["open"], 2) if bn.get("open") is not None else None,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma120": ma120,
        "trend_score": t,
        "structure": st,
        "rsi": round(r, 1) if r is not None else None, "momentum": mtag,
        "vol_ratio": vr, "vol_tag": vtag,
        "skew": sk, "skew_tag": skew_tag(sk),
        "weekly": wk_txt, "resonance": reso,
        "poc": poc, "vah": vah, "val": val,
        "pos_pct": position_pct(c, low60, high60),
        "high60": high60, "low60": low60,
        "ccp": ccp, "ccp_tag": ccp_tag,
        "round_level": rl, "round_dist": rl_dist, "round_tag": rl_tag,
        "dyn_poc": dyn_poc, "poc_consist": poc_consist, "poc_tag": poc_tag,
        "vp_score": score,
    }
    p["evo"] = evolution(bars, idx, p)
    return p


# ---------- Evolution Module v2.0：A–G 拆解 + 判讀燈號 ----------
# 仿作者 YH VP Pro 面板。部分行(B/C/G)是日K近似(無盤中tick)，標清楚「日K近似」。

def _excess(bar):
    """日K近似 Excess：上/下影線占全幅比例。
    長上影=頂部拒絕(賣壓)，長下影=底部承接(買盤)。非真tick Excess。"""
    h, l, o, c = bar.get("high"), bar.get("low"), bar.get("open"), bar.get("close")
    if None in (h, l, o, c) or h <= l:
        return None, None
    rng = h - l
    bh, bl = max(o, c), min(o, c)
    return round((h - bh) / rng, 2), round((bl - l) / rng, 2)


def evolution(bars, idx, p):
    """A–G 七行拆解。回 {行key: {k名稱, ok燈號(True綠/False紅/None黃), v文字}}。"""
    bn = bars[idx]
    op, poc, vah, val = p.get("open"), p["poc"], p["vah"], p["val"]
    evo = {}

    # A POC偏離度：開盤 vs 核心價
    if op and poc:
        d = round((op - poc) / poc * 100, 2)
        evo["A"] = {"k": "POC偏離度", "ok": abs(d) < 1.5,
                    "v": f"開盤{'貼近' if abs(d) < 1.5 else '偏離'}核心價 {abs(d)}%｜"
                         f"{'均衡開盤' if abs(d) < 1.5 else ('開高走勢' if d > 0 else '開低走勢')}"}

    # B 市場狀態：VA寬度 動態(20) vs 靜態(60)（日K近似）
    try:
        win20 = bars[max(0, idx - 19):idx + 1]
        p20, vh20, vl20, _ = vp_levels(win20)
        w20, w60 = (vh20 - vl20) / p20, (vah - val) / poc
        btag = ("VA擴張,波動放大" if w20 > w60 * 1.15
                else "VA收斂,盤整待變" if w20 < w60 * 0.85 else "VA持平,方向不明")
        evo["B"] = {"k": "市場狀態", "ok": None, "v": f"{btag}（日K近似）"}
    except (ValueError, ZeroDivisionError, TypeError):
        pass

    # C 頂/底端結構：日影線拒絕（近似，非tick Excess）
    up, dn = _excess(bn)
    if up is not None:
        evo["C_top"] = {"k": "頂端結構", "ok": up < 0.4,
                        "v": ("頂端正常,上方無異常壓力" if up < 0.4
                              else f"上影拒絕 {int(up * 100)}%,頂部賣壓（日K近似）")}
        evo["C_bot"] = {"k": "底端結構", "ok": (dn >= 0.4 or dn < 0.15),
                        "v": (f"下影承接 {int(dn * 100)}%,底部買盤（日K近似）" if dn >= 0.4
                              else "底端正常,下方無異常")}

    # D 買賣方向：CCP 收盤位置
    if p["ccp"] is not None:
        evo["D"] = {"k": "買賣方向", "ok": p["ccp"] >= 50,
                    "v": f"收盤 CCP={p['ccp']}%｜{p['ccp_tag']}"}

    # E 整數共振
    if p["round_level"]:
        evo["E"] = {"k": "整數關卡", "ok": abs(p["round_dist"]) < 1.5,
                    "v": f"{p['round_level']}（{p['round_dist']:+}%，{p['round_tag']}）"}

    # F Rolling POC：動態 vs 靜態（共識移動方向）
    if p["dyn_poc"] and poc:
        if p["dyn_poc"] > poc:
            ftag, fok = "共識上移,多方重心成形", True
        elif p["dyn_poc"] < poc:
            ftag, fok = "共識下移,空方重心成形", False
        else:
            ftag, fok = "共識穩定", None
        evo["F"] = {"k": "Rolling POC", "ok": fok,
                    "v": f"動態{p['dyn_poc']} vs 靜態{poc}｜{ftag}"}

    # G 價位匯聚：日POC + 動態POC + 整數關卡 聚攏度（誠實版，非AVWAP/H1）
    levels = [x for x in (poc, p["dyn_poc"], p["round_level"]) if x]
    if len(levels) >= 2 and poc:
        spread = round((max(levels) - min(levels)) / poc * 100, 1)
        near = sum(1 for x in levels if abs(x - poc) / poc * 100 < 1.5)
        evo["G"] = {"k": "價位匯聚", "ok": spread < 1.5,
                    "v": (f"三層匯聚 POC/動態/關卡集中 {spread}%,關鍵價位" if spread < 1.5
                          else f"{near}層靠近,價位分散 {spread}%")}
    return evo


def verdict(p, win20_rate=None):
    """判讀燈號：綜合結構/動能/量價/共識 → 偏多偏空現況研判。
    信心綁回測勝率(win20_rate)，沒數字不喊信心。描述現況，非保證獲利。"""
    st = p["structure"]
    net = 0
    if st in ("主升段", "突破"):
        net += 2
    elif st in ("多頭", "起漲"):
        net += 1
    elif st == "空頭":
        net -= 2
    elif st == "底部":
        net -= 1
    if p["trend_score"] >= 80:
        net += 1
    elif p["trend_score"] <= 20:
        net -= 1
    reso = p.get("resonance") or ""
    if "共振" in reso and "偏多" in reso:
        net += 1
    elif "共振" in reso and "偏空" in reso:
        net -= 1
    rsi = p.get("rsi")
    if rsi is not None:
        if rsi > 78:
            net -= 1
        elif 50 <= rsi <= 70:
            net += 1
        elif rsi < 40:
            net -= 1
    if p["vol_tag"] == "放量" and net > 0:
        net += 1
    if p["ccp"] is not None:
        if p["ccp"] >= 70:
            net += 1
        elif p["ccp"] <= 30:
            net -= 1
    if p["dyn_poc"] and p["poc"]:
        if p["dyn_poc"] > p["poc"]:
            net += 1
        elif p["dyn_poc"] < p["poc"]:
            net -= 1
    pp = p.get("pos_pct")
    if pp is not None and (pp > 95 or pp < 15):
        net -= 1

    if net >= 5 and p["vp_score"] >= 72:
        light, tone, sig = "🟢", "強多頭訊號", "多項共振，偏多看待"
    elif net >= 3:
        light, tone, sig = "🟢", "多頭有利", "多重共振，可評估"
    elif net <= -3:
        light, tone, sig = "🔴", "偏空轉弱", "結構轉弱，避開／減碼"
    elif net <= -1:
        light, tone, sig = "🟠", "偏弱待觀察", "訊號偏空，等止穩"
    else:
        light, tone, sig = "🟡", "方向待定", "部分共振，等確認"

    conf = (f"此分數區間過去20日勝率 {win20_rate}%" if win20_rate is not None
            else "（回測勝率未產生）")
    action = sig
    if net >= 3 and p.get("val"):
        action = f"{sig}；參考支撐 {p['val']}（失守減碼）"
    elif net <= -3 and p.get("vah"):
        action = f"{sig}；反彈壓力 {p['vah']}"
    return {"light": light, "tone": tone, "sig": sig, "net": net,
            "conf": conf, "action": action}


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
