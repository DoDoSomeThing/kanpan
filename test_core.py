#!/usr/bin/env python3
"""
test_core — kanpan 核心純函數回歸測試（釘住已知行為，防改門檻時靜默改壞燈號）。

不靠 pytest，純 assert。跑：python test_core.py
合成資料：明確多頭/空頭序列，驗指標與燈號方向正確，非驗精確數值。
"""
import sys
import core


def _bars(closes, vols=None):
    """用收盤序列造 bars（high/low 取收盤±1%，open=前收，給結構/CCP 有料）。"""
    vols = vols or [1000] * len(closes)
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append({
            "date": f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "open": round(prev, 2),
            "high": round(c * 1.01, 2),
            "low": round(c * 0.99, 2),
            "close": round(c, 2),
            "volume": vols[i],
        })
        prev = c
    return out


passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


# ---------- sma ----------
s = core.sma([1, 2, 3, 4, 5], 3)
check("sma 前 n-1 為 None", s[0] is None and s[1] is None)
check("sma 值正確", s[2] == 2.0 and s[4] == 4.0)

# ---------- rsi14 ----------
up = list(range(1, 40))                       # 連漲 → RSI 應接近 100
r = core.rsi14([float(x) for x in up])
check("rsi 連漲接近100", r[-1] is not None and r[-1] > 95)
dn = list(range(40, 1, -1))                    # 連跌 → RSI 應接近 0
r2 = core.rsi14([float(x) for x in dn])
check("rsi 連跌接近0", r2[-1] is not None and r2[-1] < 5)

# ---------- trend_score ----------
check("trend 多頭排列滿分", core.trend_score(100, 95, 94, 90, 85, 80) == 100)
check("trend 空頭排列零分", core.trend_score(80, 85, 86, 90, 95, 100) == 0)
check("trend 任一均線 None 不爆", core.trend_score(100, None, 94, 90, 85, 80) == 80)

# ---------- structure ----------
check("structure 突破=創60新高", core.structure(110, 100, 99, 98, 95, 90, 110, 80) == "突破")
check("structure 空頭", core.structure(80, 85, 86, 90, 95, 100, 120, 60) == "空頭")
check("structure 底部=近60低", core.structure(80, 85, 86, 90, 95, 100, 120, 78) == "底部")
check("structure 資料不足", core.structure(100, None, None, None, None, None, None, None) == "資料不足")

# ---------- vp_score 範圍 ----------
sc = core.vp_score(100, 65, 1.2, 100, 80, 105)
check("vp_score 0~100", 0 <= sc <= 100)

# ---------- position / volume score 邊界 ----------
check("position 貼頂打折", core.position_score(100, 50, 100) == 70.0)
check("volume 爆量不給滿", core.volume_score(5.0) == 60.0)
check("volume None 中性", core.volume_score(None) == 50.0)

# ---------- compute_panel + verdict 方向 ----------
import math
bull = [50 + i * 0.4 + 4 * math.sin(i / 3.0) for i in range(160)]   # 多頭(有回檔)
pb = core.compute_panel(core._norm(_bars(bull)))
vb = core.verdict(pb)
check("多頭序列 vp_score 高", pb["vp_score"] >= 70)
check("多頭序列 net 為正", vb["net"] > 0)
check("多頭序列燈號非紅", vb["light"] != "🔴")
check("多頭結構偏多", pb["structure"] in ("主升段", "多頭", "突破", "起漲", "多頭修正"))

# verdict 嚴格性：直線過熱(RSI~100)不該喊強多頭/偏多（防追高 guard）
hot = [50 + i * 0.7 for i in range(160)]
ph = core.compute_panel(core._norm(_bars(hot)))
vh = core.verdict(ph)
check("過熱直線不喊綠燈(防追高)", vh["light"] != "🟢")

bear = [150 - i * 0.7 for i in range(160)]      # 穩定下降
pr = core.compute_panel(core._norm(_bars(bear)))
vr = core.verdict(pr)
check("空頭序列 vp_score 低", pr["vp_score"] <= 45)
check("空頭序列燈號不偏多", vr["light"] in ("🔴", "🟠", "🟡"))
check("空頭序列 net 為負", vr["net"] < 0)

# ---------- verdict 嚴格性：盤整不該喊強多頭 ----------
flat = [100 + (i % 2) for i in range(160)]       # 橫盤
pf = core.compute_panel(core._norm(_bars(flat)))
vf = core.verdict(pf)
check("橫盤不喊強多頭", vf["tone"] != "強多頭訊號")

# ---------- 壞棒防呆：超過 ±10% 漲跌幅上限的尾棒視為損壞 ----------
clean = [{"date": f"2026-06-{10+i:02d}", "open": 24, "high": 25, "low": 23,
          "close": 24.0, "volume": 1000} for i in range(3)]
poison = clean + [{"date": "2026-06-13", "open": 12, "high": 12, "low": 12,
                   "close": 12.5, "volume": 100}]    # 腰斬壞棒(踩過 2409/2330)
bad, bd = core._bad_tail_date(poison)
check("壞棒被偵測(變動>15%)", bad and bd == "2026-06-13")
check("乾淨序列不誤判", not core._bad_tail_date(clean)[0])
check("_drop_bad_tail 去掉壞尾棒", core._drop_bad_tail(poison) == clean)
check("漲停(+10%)不誤殺", not core._bad_tail_date(
    clean + [{"date": "2026-06-13", "close": 26.4, "volume": 1}])[0])

# ---------- 第九段 E 壓力叢集 ----------
# 英業達案例：現價67.9，叢集 MA10 68.35 / 套牢 68.53 / MA20 69.41 → E=70
lvl, mem = core.cluster_round_level(
    67.9, [(68.35, "MA10"), (68.53, "套牢"), (69.41, "MA20")])
check("叢集案例 E=70", lvl == 70)
check("叢集成員含三項", len(mem) == 3)

# 區辨案例（證明新舊不同）：現價71、MA20 73、套牢 74、step=1 → 新邏輯 75（非舊 72）
lvl2, mem2 = core.cluster_round_level(71, [(73, "MA20"), (74, "套牢")], step=1)
check("區辨案例 新邏輯取75(非72)", lvl2 == 75)
check("區辨叢集上緣=74兩成員", len(mem2) == 2)

# 空曠區：上方無壓力源 → 退回最近整數 round_level
lvl3, mem3 = core.cluster_round_level(71, [])
check("空曠區退回最近整數", lvl3 == core.round_level(71) and mem3 == [])
# 上方源超過 max_dist → 也視為空曠
lvl4, _ = core.cluster_round_level(71, [(95, "MA60")], max_dist_pct=12.0)
check("超距源不成叢集→退回", lvl4 == core.round_level(71))

# 帶寬切割：兩源相距 > 帶寬 → 只取最近那叢集上緣
lvl5, mem5 = core.cluster_round_level(70, [(71, "MA10"), (80, "套牢")],
                                      step=1, band_pct=2.0)
check("超帶寬不併叢集(只取近端71→72)", lvl5 == 72 and len(mem5) == 1)

# swing_highs：中間高點被抓出
sh = core.swing_highs([{"high": h} for h in [10, 12, 11, 9, 15, 13]], k=1)
check("swing_highs 抓區域高點", 12 in sh and 15 in sh)

# ---------- L1 狀態層：純重排，checklist 真值 ----------
sl = core.state_layer({
    "close": 100, "ma5": 98, "ma20": 102, "vol_ratio": 1.5,
    "poc_tag": "共識穩定", "structure": "盤整", "momentum": "普通",
    "inst_consensus": {"status": "一致偏多", "light": "🟢"},
})
ckd = {x["k"]: x["ok"] for x in sl["checklist"]}
check("狀態層 趨勢取 structure", sl["trend"] == "盤整")
check("狀態層 籌碼取 inst_consensus", sl["chips"] == "一致偏多")
check("checklist 站上MA5(100>98)", ckd["站上MA5"] is True)
check("checklist 未站上MA20(100<102)", ckd["站上MA20"] is False)
check("checklist 放量(1.5>1.2)", ckd["放量(>1.2x)"] is True)
check("checklist POC穩定", ckd["POC穩定"] is True)
check("狀態層 無法人時籌碼=—", core.state_layer({"inst_consensus": None})["chips"] == "—")

print(f"\n通過 {passed}　失敗 {failed}")
sys.exit(1 if failed else 0)
