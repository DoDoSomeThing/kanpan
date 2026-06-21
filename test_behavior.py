#!/usr/bin/env python3
"""test_behavior — 行為守門純函式回歸（P3 追高/凹單/頻率）。跑：python test_behavior.py"""
import sys
import behavior as BH

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


# ---------- 追高警示 ----------
# 乖離大 + 無持倉 → 警示
check("追高 乖離>=15 發警示",
      BH.chase_warning({"bias20": 18.0, "pos_pct": 50}, has_position=False) is not None)
# 位置貼天花板 → 警示
check("追高 位置>=85 發警示",
      BH.chase_warning({"bias20": 2.0, "pos_pct": 90}, has_position=False) is not None)
# 正常 → 無
check("追高 正常不發",
      BH.chase_warning({"bias20": 3.0, "pos_pct": 50}, has_position=False) is None)
# 已持倉 → 不對抱單者誤報
check("追高 已持倉不發",
      BH.chase_warning({"bias20": 30.0, "pos_pct": 99}, has_position=True) is None)

# ---------- 凹單偵測 ----------
check("凹單 🔴觸發發警示",
      BH.hold_loser_warning({"light": "🔴", "state": "已觸發", "cur_price": 60,
                             "effective_exit": 62, "effective_by": "硬停損"}) is not None)
check("凹單 🟢正常不發",
      BH.hold_loser_warning({"light": "🟢", "state": "正常持有"}) is None)
check("凹單 無持倉(None)不發", BH.hold_loser_warning(None) is None)

# ---------- 頻率警示 ----------
# 近30日5筆 > 4 → 警示（ref=最晚 exit）
closed = [{"exit_date": f"2026-06-{d:02d}"} for d in (1, 5, 10, 15, 20)]
check("頻率 30日5筆發警示",
      BH.frequency_warning(closed, window=30, max_trades=4) is not None)
# 拉長間隔 → 視窗內只剩少數
spread = [{"exit_date": d} for d in
          ("2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-06-20")]
check("頻率 分散不發", BH.frequency_warning(spread, window=30, max_trades=4) is None)
check("頻率 空歷史不發", BH.frequency_warning([]) is None)

# ---------- 彙整 ----------
out = BH.behavior_checks(
    p={"bias20": 20.0, "pos_pct": 90}, risk=None,
    closed_records=closed)
# 無 risk → 視為無持倉 → 追高會發；頻率也發；凹單不發
codes = {w["code"] for w in out}
check("彙整 含追高+頻率", "chase" in codes and "frequency" in codes)
check("彙整 無凹單(無持倉)", "hold_loser" not in codes)
# 有持倉且觸發 → 凹單發、追高不發
out2 = BH.behavior_checks(
    p={"bias20": 20.0, "pos_pct": 90},
    risk={"light": "🔴", "state": "已觸發", "cur_price": 60,
          "effective_exit": 62, "effective_by": "Trail"},
    closed_records=[])
codes2 = {w["code"] for w in out2}
check("彙整 有持倉觸發→凹單發、追高不發",
      "hold_loser" in codes2 and "chase" not in codes2)

print(f"\n通過 {passed}　失敗 {failed}")
sys.exit(1 if failed else 0)
