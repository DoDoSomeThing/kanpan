#!/usr/bin/env python3
"""test_review — 交易檢討純函式回歸（樣本外/各策略/賭博特徵）。跑：python test_review.py"""
import sys
import review as RV

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


def mk(ret, date, reason="manual"):
    return {"return_pct": ret, "exit_date": date, "exit_reason": reason}


# ---------- 樣本外 ----------
# 前段賺、後段翻負 → 翻負（過擬合）
c = ([mk(5, f"2026-01-{d:02d}") for d in range(1, 8)]      # 7 筆 +5
     + [mk(-4, f"2026-02-{d:02d}") for d in range(1, 4)])  # 3 筆 -4
o = RV.out_of_sample(c, split=0.7)
check("樣本外 前段正後段負→翻負", o["verdict"] == "翻負（過擬合徵兆）")
check("樣本外 in/out 期望值", o["in_exp"] == 5.0 and o["out_exp"] == -4.0)
# 全程穩定正 → 持續
c2 = [mk(3, f"2026-01-{d:02d}") for d in range(1, 11)]
check("樣本外 穩定→持續", RV.out_of_sample(c2)["verdict"] == "持續（相對穩）")
# <6 筆 → 樣本不足
check("樣本外 <6筆不足", RV.out_of_sample([mk(1, "2026-01-01")])["verdict"] == "樣本不足")
# 時序：亂序輸入也要按 exit_date 切（前5日+8、後3日-9，打亂順序餵）
c3 = [mk(-9, "2026-01-08"), mk(8, "2026-01-01"), mk(-9, "2026-01-06"),
      mk(8, "2026-01-02"), mk(8, "2026-01-03"), mk(8, "2026-01-05"),
      mk(8, "2026-01-04"), mk(-9, "2026-01-07")]
o3 = RV.out_of_sample(c3, split=0.7)   # k=int(8*0.7)=5 → in 前5(+8)、out 後3(-9)
check("樣本外 依日期排序(後3日-9進後段)",
      o3["in_exp"] == 8.0 and o3["out_exp"] == -9.0)

# ---------- 各策略體檢 ----------
mix = [mk(5, "2026-01-01", "trail"), mk(7, "2026-01-02", "trail"),
       mk(-4, "2026-01-03", "manual"), mk(-6, "2026-01-04", "manual")]
tags = RV.per_tag(mix)
check("各策略 兩組", len(tags) == 2)
check("各策略 最差排前(manual負)", tags[0]["tag"] == "manual" and tags[0]["expectancy"] < 0)
check("各策略 賠錢招標記", tags[0]["verdict"] == "🟥 賠錢招")
check("各策略 trail 賺錢招", tags[1]["tag"] == "trail" and tags[1]["verdict"] == "🟩 賺錢招")
check("各策略 勝率", tags[1]["win_rate"] == 100.0)

# ---------- 賭博特徵 ----------
# 負期望 + 賺小賠大
neg = [mk(2, "2026-01-01"), mk(2, "2026-01-02"), mk(-10, "2026-01-03")]
fl = RV.gambling_flags(neg)
codes = {f["code"] for f in fl}
check("賭博 負期望偵測", "neg_exp" in codes)
check("賭博 賺小賠大偵測", "small_win_big_loss" in codes)
# 獲利集中：一筆巨贏佔絕大多數
conc = [mk(100, "2026-01-01"), mk(2, "2026-01-02"), mk(2, "2026-01-03"),
        mk(-1, "2026-01-04")]
check("賭博 獲利集中偵測",
      "concentration" in {f["code"] for f in RV.gambling_flags(conc)})
# 長連虧
streak = [mk(1, "2026-01-01")] + [mk(-1, f"2026-02-{d:02d}") for d in range(1, 7)]
check("賭博 長連虧偵測",
      "lose_streak" in {f["code"] for f in RV.gambling_flags(streak)})
# 乾淨：穩定正、分散 → 無旗標
clean = [mk(3, "2026-01-01"), mk(4, "2026-01-02"), mk(-1, "2026-01-03"),
         mk(3, "2026-01-04"), mk(4, "2026-01-05")]
check("賭博 乾淨無旗標", RV.gambling_flags(clean) == [])
# <3 筆不掃
check("賭博 <3筆不掃", RV.gambling_flags([mk(1, "2026-01-01")]) == [])

# ---------- review 彙整 ----------
r = RV.review(mix)
check("review 三鍵齊", set(r) == {"out_of_sample", "per_tag", "gambling_flags"})

print(f"\n通過 {passed}　失敗 {failed}")
sys.exit(1 if failed else 0)
