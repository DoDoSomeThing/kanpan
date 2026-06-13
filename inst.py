#!/usr/bin/env python3
"""
kanpan inst — 三大法人買賣超（TWSE T86，上市；自含、自建 cache）

顯示用（描述現況），不是進場訊號——外資連買當訊號已驗證無 alpha。
T86 每交易日約 16:00 後公布當日；盤中看到的是最近一個已公布日（面板會標日期）。

cache：cache/t86/YYYYMMDD.json（{sid: [外資, 投信, 自營] 單位:張}）
"""
import os
import ssl
import json
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

TW_TZ = timezone(timedelta(hours=8))
HERE = os.path.dirname(os.path.abspath(__file__))
T86_DIR = os.path.join(HERE, "cache", "t86")

# TWSE 憑證鏈在 Python 3.14 嚴格驗證下會擋(Missing Subject Key Identifier) → 不驗證
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# T86 欄位（股數）：idx4 外陸資買賣超(不含外資自營) / idx10 投信買賣超 / idx11 自營商買賣超
IDX_FOREIGN, IDX_TRUST, IDX_DEALER = 4, 10, 11


def _fetch_t86(d8: str):
    """抓一天 T86。回 {sid: [外資,投信,自營] 張} 或 None(假日/未公布)。"""
    url = (f"https://www.twse.com.tw/rwd/zh/fund/T86"
           f"?date={d8}&selectType=ALL&response=json")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_CTX) as r:
            j = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    if j.get("stat") != "OK" or not j.get("data"):
        return None
    out = {}
    for row in j["data"]:
        try:
            sid = str(row[0]).strip()
            f = int(str(row[IDX_FOREIGN]).replace(",", "")) // 1000   # 股→張
            t = int(str(row[IDX_TRUST]).replace(",", "")) // 1000
            dl = int(str(row[IDX_DEALER]).replace(",", "")) // 1000
            out[sid] = [f, t, dl]
        except (ValueError, IndexError):
            continue
    return out


def ensure_cache(days: int = 12) -> list:
    """補近 N 個日曆日的 T86 cache（已存在跳過、假日寫空檔避免重抓）。
    回有資料的日期清單(舊→新)。"""
    os.makedirs(T86_DIR, exist_ok=True)
    today = datetime.now(TW_TZ).date()
    have = []
    for i in range(days, -1, -1):
        d = today - timedelta(days=i)
        if d.weekday() >= 6:           # 週日必休市(週六另有補班交易日,保留嘗試)
            continue
        d8 = d.strftime("%Y%m%d")
        p = os.path.join(T86_DIR, d8 + ".json")
        if os.path.exists(p):
            try:
                if json.load(open(p, encoding="utf-8")):
                    have.append(d8)
            except json.JSONDecodeError:
                pass
            continue
        # 今天 16:00 前 T86 還沒公布 → 不抓不寫,明天再說
        now = datetime.now(TW_TZ)
        if d == today and now.hour < 16:
            continue
        data = _fetch_t86(d8)
        json.dump(data or {}, open(p, "w", encoding="utf-8"))
        if data:
            have.append(d8)
        time.sleep(1.2)                # TWSE 防封
    return have


def _streak(xs):
    """從最新往回數同向天數。最新=0 回 0。正=連買、負=連賣。"""
    if not xs or xs[-1] == 0:
        return 0
    sign = 1 if xs[-1] > 0 else -1
    n = 0
    for v in reversed(xs):
        if (v > 0) == (sign > 0) and v != 0:
            n += 1
        else:
            break
    return n * sign


def _build(series: dict, last_date_iso: str) -> dict:
    """series={foreign:[],trust:[],dealer:[]}(舊→新,張) → 面板用 dict。"""
    out = {"date": last_date_iso}
    for k in ("foreign", "trust", "dealer"):
        xs = series[k]
        out[k] = {"net": xs[-1], "streak": _streak(xs)}
    return out


def _from_finmind(sid: str, days: int = 20) -> dict | None:
    """上櫃/T86 沒收錄時 → FinMind 法人買賣超(上市櫃都有)。回同 _build 結構。"""
    from datetime import date as _date
    try:
        from core import _find_finmind_token
        tok = _find_finmind_token()
    except Exception:
        tok = ""
    start = (_date.today() - timedelta(days=days)).isoformat()
    url = ("https://api.finmindtrade.com/api/v4/data?dataset="
           "TaiwanStockInstitutionalInvestorsBuySell"
           f"&data_id={sid}&start_date={start}&token={tok}")
    try:
        with urllib.request.urlopen(url, timeout=20, context=ssl.create_default_context()) as r:
            rows = json.loads(r.read().decode("utf-8")).get("data", [])
    except Exception:
        return None
    if not rows:
        return None
    # 按日期彙總成 外資/投信/自營 淨買超(張)
    by_date = {}
    cat = {"Foreign_Investor": "foreign", "Foreign_Dealer_Self": "foreign",
           "Investment_Trust": "trust", "Dealer_self": "dealer", "Dealer_Hedging": "dealer"}
    for x in rows:
        k = cat.get(x.get("name"))
        if not k:
            continue
        agg = by_date.setdefault(x["date"], {"foreign": 0, "trust": 0, "dealer": 0})
        agg[k] += (x.get("buy", 0) - x.get("sell", 0)) // 1000   # 股→張
    dates = sorted(by_date)
    series = {k: [by_date[d][k] for d in dates] for k in ("foreign", "trust", "dealer")}
    if not series["foreign"]:
        return None
    return _build(series, dates[-1])


def get_inst(sid: str, days: int = 12) -> dict | None:
    """回 {date, foreign:{net,streak}, trust:{...}, dealer:{...}}；查不到回 None。
    上市走本地 T86 cache；T86 沒收錄(上櫃)→ FinMind fallback。單位:張。"""
    dates = ensure_cache(days)
    series = {"foreign": [], "trust": [], "dealer": []}
    last_date = None
    for d8 in dates:
        p = os.path.join(T86_DIR, d8 + ".json")
        try:
            day = json.load(open(p, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        row = day.get(sid)
        if row:
            series["foreign"].append(row[0])
            series["trust"].append(row[1])
            series["dealer"].append(row[2])
            last_date = d8
    if series["foreign"]:
        return _build(series, f"{last_date[:4]}-{last_date[4:6]}-{last_date[6:]}")
    return _from_finmind(sid)        # 上櫃等 T86 未收錄 → FinMind


def fmt_row(name: str, d: dict) -> str:
    """一列文字：外資: +12,345 張（連買3日）"""
    net, st = d["net"], d["streak"]
    s = f"{name}: {net:+,} 張"
    if st > 1:
        s += f"（連買{st}日）"
    elif st < -1:
        s += f"（連賣{-st}日）"
    return s


if __name__ == "__main__":
    import sys
    sid = sys.argv[1] if len(sys.argv) > 1 else "2330"
    r = get_inst(sid)
    if not r:
        print(f"{sid} 無法人資料（上櫃股 T86 不含,或 cache 未建）")
    else:
        print(f"法人買賣超（{r['date']}）")
        print(" ", fmt_row("外資", r["foreign"]))
        print(" ", fmt_row("投信", r["trust"]))
        print(" ", fmt_row("自營", r["dealer"]))
