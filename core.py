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
from datetime import date, datetime, timedelta
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


# 台股日漲跌幅上限 ±10%；任一根 close 相對前一根變動超過此裕度 = 不可能的真實行情，
# 多半是 FinMind 盤中未定案/殘缺棒被抓進 cache(踩過：2409 抓到 12.5、2330 抓到 227)。
PRICE_LIMIT_TOL = 0.15     # 0.15 = 15%，比 10% 上限多留裕度，避免還權/零股價誤殺


def _bad_tail_date(bars: list):
    """檢查序列尾端是否有壞棒(相對前一根變動 > ±15%)。
    回 (壞, 該壞棒日期 or None)。只看最後一根——損壞發生在最新抓取的那根。"""
    if not bars or len(bars) < 2:
        return False, None
    prev, last = bars[-2], bars[-1]
    pc, lc = prev.get("close"), last.get("close")
    if not pc or not lc:
        return False, None
    if abs(lc - pc) / pc > PRICE_LIMIT_TOL:
        return True, last.get("date")
    return False, None


def _drop_bad_tail(bars: list) -> list:
    """去掉尾端壞棒(超過漲跌幅上限)，回乾淨序列。連續壞棒一併去掉。"""
    out = list(bars)
    while True:
        bad, _ = _bad_tail_date(out)
        if not bad:
            break
        out = out[:-1]
    return out


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


def _prev_trading_day(d: date) -> date:
    """往前推一個交易日(跳過週末)。"""
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d


# 台股收盤 13:30；留緩衝到 14:00 等當日日K定案。此時刻前「應有的最新日K」仍是前一交易日。
MARKET_CLOSE_HOUR = 14


def _recent_trading_day() -> str:
    """最近『應有日K』的交易日。
    週末退回上週五；平日但今日尚未收盤定案(現在 < 14:00)→ 退回前一交易日，
    避免收盤後~隔天開盤前這段把『最新=昨收』誤報成延遲。"""
    now = datetime.now()
    d = now.date()
    if d.weekday() >= 5:                      # 週六/日 → 上週五
        d = d - timedelta(days=d.weekday() - 4)
    elif now.hour < MARKET_CLOSE_HOUR:        # 平日但今日K未定案 → 前一交易日
        d = _prev_trading_day(d)
    return d.isoformat()


def load_bars(sid: str, cache_path: str) -> list:
    """讀 K 線。主 cache 來自(封存的)tw-stock-bot repo，會過期 → 過期就單檔抓 FinMind 補新。
    順序：extra 當日快取 → 主 cache(夠新就用) → FinMind(主 cache 舊/沒有時)。
    cache 格式：{sid: [{date,open,high/max,low/min,close,volume}, ...]}"""
    extra = Path(cache_path).parent / "extra" / f"{sid}.json"
    today = date.today().isoformat()

    # 1) extra 當日快取(今天已抓過 FinMind，直接用，免重抓)
    #    但先驗最後一根：若超過漲跌幅上限(壞棒)，不信此 cache，往下重抓校正。
    if extra.exists():
        try:
            obj = json.loads(extra.read_text(encoding="utf-8"))
            if obj.get("fetched") == today and obj.get("bars"):
                bad, bd = _bad_tail_date(obj["bars"])
                if not bad:
                    return obj["bars"]
                # 壞棒 → 刪掉這份毒 cache，往下走重抓
                extra.unlink(missing_ok=True)
        except Exception:
            pass

    # 2) 主 cache
    d = json.load(gzip.open(cache_path)) if cache_path.endswith(".gz") \
        else json.load(open(cache_path, encoding="utf-8"))
    rows = d.get(sid)
    main = _norm(rows) if rows else None

    # 主 cache 夠新(最後一根 >= 最近交易日)→ 直接用，免打 FinMind
    if main and main[-1]["date"][:10] >= _recent_trading_day():
        return main

    # 3) 主 cache 過期或沒有 → 抓 FinMind 補新，存 extra(當日有效)
    #    抓回後先去尾端壞棒(FinMind 偶爾回殘缺/未定案棒)，避免毒 cache 再被存起來。
    bars = _drop_bad_tail(_fetch_finmind(sid))
    if bars and (main is None or bars[-1]["date"] >= main[-1]["date"]):
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text(json.dumps({"fetched": today, "bars": bars},
                                    ensure_ascii=False), encoding="utf-8")
        return bars

    # FinMind 失敗 → 退回主 cache(舊總比沒有好)
    if main:
        return main
    raise KeyError(f"查無 {sid}（cache 與 FinMind 都沒有，確認代號）")


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


def trend_exit(bars):
    """個股 V3 趨勢出場：MA60 < MA120 = 趨勢轉空，建議出場／減碼。
    跨 7 市場 30+ 年驗證(crossasset_v3)：此狀態續抱回撤明顯較大；
    趨勢出場(MA60<MA120) 風險調整 >> 固定時間出場。純風控,非預測。
    回 {ma60, ma120, broken} 或 None(資料不足 120 根)。"""
    closes = [b["close"] for b in bars if b.get("close")]
    if len(closes) < 120:
        return None
    ma60 = sum(closes[-60:]) / 60
    ma120 = sum(closes[-120:]) / 120
    return {"ma60": round(ma60, 2), "ma120": round(ma120, 2), "broken": ma60 < ma120}


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

# ---------- 功能1 上方套牢量（Overhead Supply）參數 ----------
OH_TOP_N    = 2      # 取現價之上量最大的前 N 個高量節點(HVN)
OH_NEAR_PCT = 3.0    # 最近上方 HVN 距現價 < 此 % → 視為逼近套牢區(亮燈)

# ---------- 功能3 突破帶量（Breakout + Volume）參數 ----------
BO_N_BARS   = 1        # 需連續站上整數關卡的根數
BO_VOL_MODE = "rel"    # "rel"=相對量(均量倍數) / "abs"=絕對張數
BO_VOL_BASE = "MV5"    # 相對量基準均量："MV5"/"MV10"/"MV20"(預設MV5)
                       #   MV20 在急漲段被灌肥會低估真突破，MV5 較貼近當下量能
BO_VOL_MULT = 1.5      # 相對量倍數門檻：量 > 選定均量 × 此倍數
BO_VOL_ABS  = 30000    # 絕對量門檻(張)，BO_VOL_MODE="abs" 時生效

# ---------- 功能A 資料新鮮度 參數 ----------
FRESH_LAG_TH = 1       # 最後一根 bar 落後應有交易日 ≥ 此天數 → 示警

# ---------- 第九段 E 壓力叢集（Resistance-Cluster Round Level）參數 ----------
CL_BAND_PCT   = 2.0    # 叢集帶寬：壓力源彼此相距 < 此 % → 歸同一叢集
CL_MAX_DIST   = 12.0   # 現價上方搜尋上限 %：超過此距離無叢集 → 退回最近整數
CL_SWING_LB   = 60     # swing high 回看根數
CL_SWING_K    = 2      # swing high 認定：左右各 K 根都低於它（局部高點）
CL_SRC = {             # 納入叢集的壓力來源開關
    "ma5": True, "ma10": True, "ma20": True, "ma60": True,
    "overhead": True,  # H 上方套牢節點(HVN)
    "vah": True,       # VP 價值區上緣
    "swing": True,     # 近期 swing high
}

# ---------- 判讀燈號 net 權重（新增三項）----------
W_NET_OVERHEAD = 1     # 逼近上方套牢量 → net 扣此分
W_NET_BREAKOUT = 1     # 有效突破(價量俱足) → net 加此分
W_NET_INST     = 1     # 法人一致 → net 加/扣此分(分歧為0)

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


# ---------- 功能1：上方套牢量（Overhead Supply Node）----------

def overhead_supply(bars: list, close: float, top_n: int = OH_TOP_N,
                    near_pct: float = OH_NEAR_PCT):
    """對一段 bars 做量價分桶，找『現價之上』的高量節點(HVN)= 套牢賣壓。
    回 dict：{nearest, dist_pct, vol_share, nodes, near} 或 None(無上方節點)。
      nearest   最近一個上方 HVN 價位
      dist_pct  該節點距現價 %
      vol_share 該節點量能占全分布比
      nodes     前 top_n 個上方 HVN [(price, share), ...] 由近到遠
      near      最近 HVN 距現價 < near_pct → True(逼近套牢)
    註：Pine/Python 皆無現成 Volume Profile 節點 API，這是對回看 bar 自行分桶的
        近似分布(同 vp_levels)，桶數 VP_BINS 控效能。"""
    pts = [b for b in bars if b["low"] is not None and b["high"] is not None]
    if not pts:
        return None
    lo = min(b["low"] for b in pts)
    hi = max(b["high"] for b in pts)
    if hi <= lo:
        return None
    step = (hi - lo) / VP_BINS
    vol = [0.0] * VP_BINS
    for b in pts:
        b_lo = max(0, min(VP_BINS - 1, int((b["low"] - lo) / step)))
        b_hi = max(0, min(VP_BINS - 1, int((b["high"] - lo) / step)))
        per = (b["volume"] or 0) / (b_hi - b_lo + 1)
        for j in range(b_lo, b_hi + 1):
            vol[j] += per
    total = sum(vol)
    if total <= 0:
        return None
    price = lambda j: lo + (j + 0.5) * step
    # 現價之上的桶，依量由大到小取前 top_n（HVN）
    above = [(j, vol[j]) for j in range(VP_BINS) if price(j) > close and vol[j] > 0]
    if not above:
        return None
    above.sort(key=lambda x: x[1], reverse=True)
    hvn = above[:top_n]
    # nodes 由近到遠（價位低→高）
    hvn_sorted = sorted(hvn, key=lambda x: price(x[0]))
    nodes = [(round(price(j), 2), round(v / total, 3)) for j, v in hvn_sorted]
    nearest, vol_share = nodes[0]
    dist_pct = round((nearest - close) / close * 100, 1)
    return {"nearest": nearest, "dist_pct": dist_pct, "vol_share": vol_share,
            "nodes": nodes, "near": dist_pct < near_pct}


# ---------- 功能3：突破帶量（Breakout + Volume Composite）----------

def breakout_volume(closes: list, idx: int, round_level, vol_ratio, vol_lots,
                    n_bars: int = BO_N_BARS, mode: str = BO_VOL_MODE,
                    mult: float = BO_VOL_MULT, abs_th: float = BO_VOL_ABS,
                    base_label: str = BO_VOL_BASE, ref_ratio=None):
    """整數關卡突破綁量能，擋無量假突破。
    回 dict：{state, ok, vol_ok, above, v}。
      state  'none' 未觸發 / 'weak' 站上但量不足(存疑) / 'valid' 價量俱足
      ok     None(灰) / False(黃,存疑) / True(綠,有效突破) —— 對齊 evolution 燈號
      above  收盤是否連續 n_bars 站上整數關卡
      vol_ok 量能是否達門檻
    vol_ratio 為相對量基準(預設 MV5)；ref_ratio 為 MV20 比值，附註供參考。
    無量突破(weak)不給多方加分；只有 valid 才在 verdict net +分。"""
    if not round_level:
        return {"state": "none", "ok": None, "vol_ok": False,
                "above": False, "v": "無整數關卡"}
    # 連續 n_bars 收盤站上關卡
    above = all(closes[idx - k] > round_level
                for k in range(n_bars) if idx - k >= 0)
    ref = f"，vs MV20 {ref_ratio}x" if ref_ratio is not None else ""
    if mode == "abs":
        vol_ok = vol_lots is not None and vol_lots >= abs_th
        vtxt = f"量 {int(vol_lots):,}張" if vol_lots is not None else "量—"
        vthr = f"門檻 {int(abs_th):,}張"
        ref = ""
    else:
        vol_ok = vol_ratio is not None and vol_ratio >= mult
        vtxt = f"量 {vol_ratio}x({base_label})" if vol_ratio is not None else "量—"
        vthr = f"門檻 {mult}x"
    if not above:
        return {"state": "none", "ok": None, "vol_ok": vol_ok, "above": False,
                "v": f"未站上 {round_level}"}
    if vol_ok:
        return {"state": "valid", "ok": True, "vol_ok": True, "above": True,
                "v": f"有效突破 {round_level}（{vtxt}≥{vthr}{ref}）"}
    return {"state": "weak", "ok": False, "vol_ok": False, "above": True,
            "v": f"突破未帶量,存疑（{vtxt}<{vthr}{ref}）"}


# ---------- 功能A：資料新鮮度檢查 ----------

def _bdays_between(d0: date, d1: date) -> int:
    """d0→d1 之間的工作日數(粗估交易日落後，不含假日/補班)。d1<=d0 回 0。"""
    if d1 <= d0:
        return 0
    n = 0
    d = d0
    while d < d1:
        d = d + timedelta(days=1)
        if d.weekday() < 5:        # 一~五
            n += 1
    return n


def data_freshness(last_date: str, expected: str = None, lag_th: int = FRESH_LAG_TH):
    """比對最後一根 bar 日期 vs 應有交易日，回新鮮度 dict。
    回 {last, expected, lag, stale, msg}。
      lag   落後幾個工作日(粗估)
      stale 落後 >= lag_th
    用於主圖價格 與 外部資料源(法人)各自比對。"""
    if not last_date:
        return {"last": None, "expected": expected, "lag": None,
                "stale": True, "msg": "無資料日期"}
    last = last_date[:10]
    exp = (expected or _recent_trading_day())[:10]
    try:
        ld = date.fromisoformat(last)
        ed = date.fromisoformat(exp)
    except ValueError:
        return {"last": last, "expected": exp, "lag": None,
                "stale": False, "msg": ""}
    lag = _bdays_between(ld, ed)
    stale = lag >= lag_th
    return {"last": last, "expected": exp, "lag": lag, "stale": stale,
            "msg": (f"⚠ 資料延遲 {lag} 日，訊號僅供參考" if stale else "")}


def consistency_check(ref_date: str, inst: dict = None):
    """全欄位『同一根 bar』一致性：各資料源日期 vs 基準 bar 日期。
    回 {ref, sources, mismatch, ok}。
    說明：A–H/RSI/位置/乖離/breakout/參考價位 皆來自同一次 compute_panel(同一 idx)，
    結構上必為同一根 bar，不逐欄追日期。真正跨源的只有『法人(T86)』另一資料源，
    故此處比對 價格基準 vs 法人。任一源 ≠ 基準即點名。"""
    ref = ref_date[:10] if ref_date else None
    sources = [{"name": "價格", "date": ref}]      # 價格即基準
    if inst and inst.get("date"):
        sources.append({"name": "法人", "date": inst["date"][:10]})
    mismatch = [s for s in sources if s["date"] != ref]
    return {"ref": ref, "sources": sources, "mismatch": mismatch,
            "ok": not mismatch}


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


def _round_step(price: float):
    """整數關卡級距（依價位量級）。"""
    return 5 if price < 100 else 10 if price < 1000 else 50


def swing_highs(bars: list, k: int = CL_SWING_K):
    """近 bars 的 swing high：左右各 k 根 high 都不高於它的局部高點價。"""
    hs = [b["high"] for b in bars if b.get("high") is not None]
    out = []
    for i in range(k, len(hs) - k):
        if all(hs[i] >= hs[i - j] for j in range(1, k + 1)) and \
           all(hs[i] >= hs[i + j] for j in range(1, k + 1)):
            out.append(round(hs[i], 2))
    return out


def cluster_round_level(price, sources, step=None,
                        band_pct=CL_BAND_PCT, max_dist_pct=CL_MAX_DIST):
    """第九段：E = 站在『最近壓力叢集上緣』之上的最近整數關卡。

    sources = [(price, label), ...] 壓力源（含 MA/套牢/VA上緣/swing high）。
    流程：取現價上方且距離 < max_dist% 的壓力源 → 由近到遠，把相距 < band% 者
    連鎖併成一個叢集 → 取『最近叢集』的上緣價 → E = 嚴格大於上緣的最近整數。
    上方無叢集（空曠區）→ 退回 round_level（最近整數）。
    回 (level, members)；members = [(price, label), ...] 該叢集組成（無則 []）。"""
    if not price or price <= 0:
        return (round_level(price), [])
    step = step or _round_step(price)
    # 現價上方、距離內的壓力源，由近到遠
    cand = [(p, lab) for (p, lab) in sources
            if p is not None and p > price and (p - price) / price * 100 <= max_dist_pct]
    cand.sort(key=lambda x: x[0])
    if not cand:
        return (round_level(price), [])
    # 從最近的源連鎖併成最近叢集（下一個與『叢集目前上緣』相距 < band% 才併入）
    cluster = [cand[0]]
    upper = cand[0][0]
    for p, lab in cand[1:]:
        if (p - upper) / upper * 100 < band_pct:
            cluster.append((p, lab))
            upper = p
        else:
            break
    # E = 嚴格大於叢集上緣的最近整數（floor 到 step，不夠就 +step）
    level = (int(upper / step)) * step
    if level <= upper:
        level += step
    # 輸出成員：價四捨五入、去重（同價同標籤只留一個）
    seen, members = set(), []
    for p, lab in cluster:
        key = (round(p, 2), lab)
        if key not in seen:
            seen.add(key)
            members.append((round(p, 2), lab))
    return (level, members)


def state_layer(p: dict) -> dict:
    """L2.0 L1 狀態層：純把現有欄位重排成『一眼看現況』。
    不算新指標、不給方向/買賣意見(spec Phase 2)。籌碼需 inst_consensus，
    故此函式須在 inst_consensus 併入 p 後才呼叫(api/panel 各一次)。"""
    c, ma5, ma20 = p.get("close"), p.get("ma5"), p.get("ma20")
    vr = p.get("vol_ratio")
    ic = p.get("inst_consensus")
    checklist = [
        {"k": "站上MA5",    "ok": c is not None and ma5 is not None and c > ma5},
        {"k": "站上MA20",   "ok": c is not None and ma20 is not None and c > ma20},
        {"k": "放量(>1.2x)", "ok": vr is not None and vr > 1.2},
        {"k": "POC穩定",    "ok": p.get("poc_tag") == "共識穩定"},
    ]
    return {
        "trend": p.get("structure") or "—",
        "chips": (ic.get("status") if ic else None) or "—",
        "chips_light": (ic.get("light") if ic else ""),
        "momentum": p.get("momentum") or "—",
        "checklist": checklist,
    }


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
    vol5  = sma(vols, 5)[idx]
    vol10 = sma(vols, 10)[idx]
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

    # 功能1 上方套牢量（用同一 60 日窗）— 先算，供第九段叢集當壓力源
    try:
        overhead = overhead_supply(win, c)
    except (ValueError, ZeroDivisionError):
        overhead = None

    # 第九段 整數關卡升級：壓力叢集之上的整數（叢集源 = MA/套牢/VA上緣/swing high）
    src = []
    if CL_SRC.get("ma5"):  src.append((ma5, "MA5"))
    if CL_SRC.get("ma10"): src.append((ma10, "MA10"))
    if CL_SRC.get("ma20"): src.append((ma20, "MA20"))
    if CL_SRC.get("ma60"): src.append((ma60, "MA60"))
    if CL_SRC.get("overhead") and overhead:
        src.append((overhead["nearest"], "套牢"))
    if CL_SRC.get("vah") and vah:
        src.append((vah, "VA上緣"))
    if CL_SRC.get("swing"):
        for sh in swing_highs(win):
            src.append((sh, "swing"))
    rl, rl_members = cluster_round_level(c, src)
    rl_dist = round((rl - c) / c * 100, 1) if rl else None    # 關卡在上方→正距離
    rl_tag = (None if rl_dist is None else
              "貼近關卡(效應強)" if abs(rl_dist) < 0.7 else
              "接近關卡" if abs(rl_dist) < 1.5 else "離關卡遠")
    # 叢集組成註記（價由近到遠）
    rl_cluster = ([f"{lab} {p}" for p, lab in rl_members] if rl_members else None)

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

    # 乖離率（價離均線多遠）：月線MA20 + 季線MA60
    bias20 = round((c - ma20) / ma20 * 100, 1) if ma20 else None
    bias60 = round((c - ma60) / ma60 * 100, 1) if ma60 else None
    bias_tag = (None if bias20 is None else
                "乖離過大(追高險)" if bias20 >= 25 else
                "正乖離偏大" if bias20 >= 15 else
                "超跌(負乖離大)" if bias20 <= -15 else "乖離正常")

    # 功能3 突破帶量（綁整數關卡 rl + 量能；vol 股→張）
    # 功能B：相對量基準改可選均量(預設 MV5)，MV20 比值附註參考
    vol_lots = vols[idx] / 1000 if vols[idx] is not None else None
    base_map = {"MV5": vol5, "MV10": vol10, "MV20": vol20}
    base_v = base_map.get(BO_VOL_BASE, vol5)
    bo_ratio = round(vols[idx] / base_v, 2) if (base_v and vols[idx] is not None) else None
    brk = breakout_volume(closes, idx, rl, bo_ratio, vol_lots,
                          base_label=BO_VOL_BASE, ref_ratio=vr)

    # 功能A 資料新鮮度（主圖價格；排除盤中臨時 live 棒，取最後真實日K）
    real = [b for b in bars[:idx + 1] if b["date"] != "live"]
    fresh = data_freshness(real[-1]["date"]) if real else None
    ref_date = real[-1]["date"][:10] if real else None   # 全欄位基準 bar 日期(功能七)

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
        "round_cluster": rl_cluster,
        "dyn_poc": dyn_poc, "poc_consist": poc_consist, "poc_tag": poc_tag,
        "bias20": bias20, "bias60": bias60, "bias_tag": bias_tag,
        "overhead": overhead, "breakout": brk,
        "freshness": fresh, "ref_date": ref_date,
        "vp_score": score,
    }
    # 個股 V3 趨勢燈(MA60 vs MA120,唯一跨市場驗證的規則);panel/api/擴充共用
    p["trend"] = ({"ma60": ma60, "ma120": ma120, "broken": ma60 < ma120}
                  if (ma60 is not None and ma120 is not None) else None)
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
                         f"{'開在核心區' if abs(d) < 1.5 else ('開於核心之上' if d > 0 else '開於核心之下')}"}

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

    # E 整數共振（第九段：附壓力叢集組成）
    if p["round_level"]:
        cl = p.get("round_cluster")
        cl_txt = f"｜叢集 {' / '.join(cl)}" if cl else ""
        evo["E"] = {"k": "整數關卡", "ok": abs(p["round_dist"]) < 1.5,
                    "v": f"{p['round_level']}（{p['round_dist']:+}%，{p['round_tag']}）{cl_txt}"}

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

    # H 上方套牢量（功能1）：頭頂高量節點=突破前要消化的賣壓
    oh = p.get("overhead")
    if oh:
        evo["H"] = {"k": "上方套牢", "ok": (False if oh["near"] else None),
                    "v": (f"逼近套牢 {oh['nearest']}（+{oh['dist_pct']}%,量占"
                          f"{int(oh['vol_share'] * 100)}%）" if oh["near"]
                          else f"上方套牢 {oh['nearest']}（+{oh['dist_pct']}%,量占"
                               f"{int(oh['vol_share'] * 100)}%）")}
    elif p.get("close") is not None:
        evo["H"] = {"k": "上方套牢", "ok": True, "v": "上方無明顯套牢量"}

    # BO 突破帶量（功能3）：整數關卡突破綁量能，擋無量假突破
    bo = p.get("breakout")
    if bo:
        evo["BO"] = {"k": "突破帶量", "ok": bo["ok"], "v": bo["v"]}
    return evo


def wilson_ci(rate_pct, n, z=1.96):
    """勝率的 Wilson 95% 信賴區間（ROADMAP P4）。
    rate_pct=桶內勝率%，n=樣本數。回 (lo%, hi%)；n<=0 或 rate 無效回 None。
    為何 Wilson 不用常態近似：小樣本 / 接近 0/1 時常態會超出 [0,1]、低估不確定。
    用途：30 筆的 47% 與 3000 筆的 47% CI 寬度天差地別 → 避免過度解讀自己分數。"""
    if not n or n <= 0 or rate_pct is None:
        return None
    p = rate_pct / 100.0
    if p < 0 or p > 1:
        return None
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5)) / denom
    lo = max(0.0, center - half)
    hi = min(1.0, center + half)
    return (round(lo * 100, 1), round(hi * 100, 1))


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
    b20 = p.get("bias20")
    if b20 is not None and b20 >= 25:           # 乖離過大=追高過熱，扣分
        net -= 1
    # 功能1：逼近上方套牢量 → 突破前有賣壓，扣分
    oh = p.get("overhead")
    if oh and oh.get("near"):
        net -= W_NET_OVERHEAD
    # 功能3：有效突破(價量俱足)才加分；無量突破(weak)不加分
    bo = p.get("breakout")
    if bo and bo.get("state") == "valid":
        net += W_NET_BREAKOUT
    # 功能2：法人一致偏多/偏空 → 加/扣；分歧為 0
    ic = p.get("inst_consensus")
    if ic:
        if ic.get("status") == "一致偏多":
            net += W_NET_INST
        elif ic.get("status") == "一致偏空":
            net -= W_NET_INST

    # 嚴格：光加分不夠，要結構/分數/不過熱/不追高/乖離沒爆 同時成立才喊偏多
    not_hot = rsi is None or rsi < 78
    not_chase = pp is None or pp <= 90          # 貼頂(>90%)不喊進場
    not_overext = b20 is None or b20 < 22       # 乖離太大不喊強多頭
    strong = (net >= 6 and p["vp_score"] >= 78
              and st in ("主升段", "突破") and not_hot and not_chase and not_overext)
    favor = (net >= 4 and p["vp_score"] >= 68
             and st in ("主升段", "多頭", "突破", "起漲") and not_hot)

    if strong:
        light, tone, sig = "🟢", "強多頭訊號", "結構強＋多項共振，偏多看待"
    elif favor:
        light, tone, sig = "🟢", "多頭有利", "偏多但別追高，回測再評估"
    elif net <= -3:
        light, tone, sig = "🔴", "偏空轉弱", "結構轉弱，避開／減碼"
    elif net <= -1:
        light, tone, sig = "🟠", "偏弱待觀察", "訊號偏空，等止穩"
    else:
        light, tone, sig = "🟡", "方向待定", "條件不齊，等確認"

    # L1 誠實框：分桶勝率已驗證無 alpha → 標為歷史描述、非預測、非買訊
    if win20_rate is not None:
        conf = f"歷史描述·非預測｜此分數桶20日勝率 {win20_rate}%（<50%，非買訊）"
    else:
        conf = "無回測數據（不補勝率）"
    action = sig
    if strong and p.get("val"):
        action = f"{sig}；參考支撐 {p['val']}（失守減碼）"
    elif favor and p.get("poc"):
        action = f"{sig}；回測 {p['poc']} 站穩再看"
    elif net <= -3 and p.get("vah"):
        action = f"{sig}；反彈壓力 {p['vah']}"
    return {"light": light, "tone": tone, "sig": sig, "net": net,
            "conf": conf, "action": action, "frame": "現況研判·非預測（分數無方向 alpha）"}


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
