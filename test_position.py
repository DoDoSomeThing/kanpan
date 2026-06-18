#!/usr/bin/env python3
"""
test_position — L3 持倉風控回歸測試（V2 Phase 1）。

純 assert，不靠 pytest。跑：python test_position.py
釘：硬停損/Trail/生效切換/距觸發/狀態燈邊界、peak 跨日累積、出場搬 closed。
用臨時 positions.json，不污染本機檔。
"""
import os
import sys
import tempfile

import position as P

try:
    sys.stdout.reconfigure(encoding="utf-8")   # Windows cp950 印不出 🟢🟡🔴
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


# ---------- compute_risk：硬停損 / Trail 生效切換 ----------
# 進場 100，peak 還在進場價 → 硬停損 96 應生效（Trail=92 < 96）
r = P.compute_risk(100.0, 100.0, 101.0)
check("早期 peak 低 → 硬停損生效", r["effective_by"] == "硬停損" and r["effective_exit"] == 96.0)
check("硬停損值 = entry×0.96", r["hard_stop"] == 96.0)
check("Trail值 = peak×0.92", r["trail_stop"] == 92.0)

# peak 漲到 120 → Trail=110.4 > 硬停損96 → Trail 接手
r2 = P.compute_risk(100.0, 120.0, 115.0)
check("peak 漲高 → Trail 生效", r2["effective_by"] == "Trail" and r2["effective_exit"] == 110.4)

# ---------- 未實現 % ----------
check("未實現% 正確", P.compute_risk(100.0, 100.0, 110.0)["unreal_pct"] == 10.0)
check("未實現% 負", P.compute_risk(100.0, 100.0, 95.0)["unreal_pct"] == -5.0)

# ---------- 距觸發 % ----------
# 生效 96，現價 100 → 距 (100-96)/100 = 4.0%
check("距觸發% 正確", P.compute_risk(100.0, 100.0, 100.0)["dist_pct"] == 4.0)

# ---------- 狀態燈邊界 ----------
# 🟢 正常：距 > 2%
check("🟢 距>2%", P.compute_risk(100.0, 100.0, 100.0)["light"] == "🟢")
# 🟡 接近：0 < 距 ≤ 2%（現價 97 → 生效96 → 距 1.03%）
check("🟡 距0~2%", P.compute_risk(100.0, 100.0, 97.0)["light"] == "🟡")
# 🔴 已觸發：現價 ≤ 生效（現價 96 = 生效96）
check("🔴 現價=生效觸發", P.compute_risk(100.0, 100.0, 96.0)["light"] == "🔴")
check("🔴 現價<生效觸發", P.compute_risk(100.0, 100.0, 90.0)["light"] == "🔴")
# 邊界：距剛好 2.0% 仍算接近(🟡)（現價約 97.96 → 生效96 → 2.0%）
check("距=2.0% 邊界算🟡", P.compute_risk(100.0, 100.0, 97.96)["dist_pct"] == 2.0
      and P.compute_risk(100.0, 100.0, 97.96)["light"] == "🟡")

# ---------- 用臨時檔測 open / peak 累積 / close ----------
tmp = tempfile.mkdtemp()
P.POS_PATH = os.path.join(tmp, "positions.json")

P.open_position("2356", 68.6, 0.5, entry_date="2026-06-18")
d = P.load_positions()
check("open 寫入 open 區", "2356" in d["open"])
check("open 初始 peak = 進場價", d["open"]["2356"]["peak_price"] == 68.6)

# peak 跨日累積：今日 high 73.2 > peak → 更新並寫回
r3 = P.position_risk("2356", 71.3, today_high=73.2)
check("peak 累積到 73.2", P.load_positions()["open"]["2356"]["peak_price"] == 73.2)
# 隔日 high 較低 → peak 不回退
P.position_risk("2356", 70.0, today_high=70.5)
check("peak 不回退", P.load_positions()["open"]["2356"]["peak_price"] == 73.2)
# 生效出場應由 Trail 接手：73.2×0.92=67.34 > 硬停損 68.6×0.96=65.86
check("Trail 接手(高點73.2−8%)", r3["effective_by"] == "Trail" and r3["effective_exit"] == 67.34)

# 平倉 → 搬進 closed，open 移除
rec = P.close_position("2356", 67.3, exit_date="2026-07-02", exit_reason="trail")
d2 = P.load_positions()
check("close 從 open 移除", "2356" not in d2["open"])
check("close 進 closed", len(d2["closed"]) == 1)
check("closed 報酬% 正確", rec["return_pct"] == round((67.3 - 68.6) / 68.6 * 100, 1))
check("closed 持有日數", rec["hold_days"] == 14)

# 無持倉 → position_risk 回 None
check("無持倉回 None", P.position_risk("9999", 50.0) is None)

print(f"\n通過 {passed}　失敗 {failed}")
sys.exit(1 if failed else 0)
