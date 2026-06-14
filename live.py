#!/usr/bin/env python3
"""
kanpan live — 台股盤中即時價（TWSE MIS，公開免登入）

盤中(週一~五 9:00-13:30)抓即時成交價，讓面板現況隨盤動；
非盤中回 None，照日線收盤。kanpan 自含，不依賴其他專案。
"""
import ssl
import json
import urllib.request
from datetime import datetime, timezone, timedelta

TW_TZ = timezone(timedelta(hours=8))

# TWSE MIS 憑證有瑕疵(Missing Subject Key Identifier)，python 嚴格驗證會擋 → 不驗證
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def market_open(now: datetime = None) -> bool:
    now = now or datetime.now(TW_TZ)
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return 900 <= t <= 1330


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def live_quote(sid: str):
    """回盤中即時報價 dict 或 None。含真開高低 + 累積量(MIS o/h/l/v)，讓量能/CCP/乖離也即時。
    price 來源優先：z 當前成交 > pz 上一筆 > 最佳買賣中點。vol 單位=張(後續×1000轉股對齊cache)。"""
    ex = "|".join(f"{m}_{sid}.tw" for m in ("tse", "otc"))
    url = ("https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
           f"?ex_ch={ex}&json=1&delay=0")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8, context=_CTX) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None

    def first(s):
        for part in str(s or "").split("_"):
            x = _f(part)
            if x and x > 0:
                return x
        return None

    for d in data.get("msgArray", []):
        z = _f(d.get("z"))
        if not z or z <= 0:
            z = _f(d.get("pz"))
        if not z or z <= 0:
            ask, bid = first(d.get("a")), first(d.get("b"))
            z = round((ask + bid) / 2, 2) if (ask and bid) else (ask or bid)
        if not z or z <= 0:
            continue
        y = _f(d.get("y"))
        return {
            "price": z,
            "chg": round((z - y) / y * 100, 2) if y else None,
            "open": _f(d.get("o")),
            "high": _f(d.get("h")),
            "low": _f(d.get("l")),
            "vol_lots": _f(d.get("v")),   # 累積成交量(張)
        }
    return None


def live_price(sid: str):
    """相容舊介面：回 (即時價, 漲跌%)。"""
    q = live_quote(sid)
    return (q["price"], q["chg"]) if q else (None, None)
