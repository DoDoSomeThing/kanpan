#!/usr/bin/env python3
"""test_portfolio — 組合層純函式回歸（P1.5 累計α / P2 相關 / 曝險）。跑：python test_portfolio.py"""
import sys
import portfolio as PF

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


# ---------- 累計超額 α ----------
closed = [
    {"return_pct": 10.0, "bench_pct": 4.0, "alpha_pct": 6.0},    # 贏
    {"return_pct": -3.0, "bench_pct": 1.0, "alpha_pct": -4.0},   # 輸
    {"return_pct": 5.0, "bench_pct": 5.0, "alpha_pct": 0.0},     # 平（不算贏）
    {"return_pct": 2.0, "bench_pct": None, "alpha_pct": None},   # bench 缺→不納入
]
c = PF.cumulative_alpha(closed)
check("累計 n 排除無α", c["n"] == 3)
check("累計 sum_return", c["sum_return"] == 12.0)
check("累計 sum_bench", c["sum_bench"] == 10.0)
check("累計 sum_alpha", c["sum_alpha"] == 2.0)
check("累計 mean_alpha", c["mean_alpha"] == round(2.0 / 3, 1))
check("累計 win_vs_bench(>0 才算)", c["win_vs_bench"] == 1)
check("累計 beat_rate", c["beat_rate"] == round(1 / 3 * 100, 1))
# 空 → n=0 安全
c0 = PF.cumulative_alpha([])
check("累計 空回 n=0", c0["n"] == 0 and c0["beat_rate"] is None)

# ---------- pearson ----------
check("pearson 完美正相關=1", PF.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == 1.0)
check("pearson 完美負相關=-1", PF.pearson([1, 2, 3, 4], [8, 6, 4, 2]) == -1.0)
check("pearson 零變異回 None", PF.pearson([1, 1, 1], [1, 2, 3]) is None)
check("pearson 長度不符回 None", PF.pearson([1, 2], [1, 2, 3]) is None)

# ---------- aligned_returns（共同日對齊）----------
# 報酬需有變異才有相關性可言（全等漲幅=零變異→None）
# 共同日 06-02/03/04：報酬 +20%、+10%（兩檔同步）
ba = [{"date": "2026-06-01", "close": 100}, {"date": "2026-06-02", "close": 110},
      {"date": "2026-06-03", "close": 132}, {"date": "2026-06-04", "close": 145.2}]
bb = [{"date": "2026-06-02", "close": 50}, {"date": "2026-06-03", "close": 60},
      {"date": "2026-06-04", "close": 66}, {"date": "2026-06-05", "close": 70}]
ra, rb = PF.aligned_returns(ba, bb)
# 共同日 06-02/03/04 → 2 個報酬點
check("aligned 共同日報酬點數", len(ra) == 2 and len(rb) == 2)
check("aligned 同步漲→相關=1", PF.pearson(ra, rb) == 1.0)
# 共同日<3 → 空
check("aligned 共同日不足回空",
      PF.aligned_returns(ba, [{"date": "2026-06-04", "close": 1}]) == ([], []))

# ---------- correlation_matrix + high_corr ----------
mx = PF.correlation_matrix({"A": ba, "B": bb})
check("matrix 上三角一對", len(mx) == 1 and mx[0]["a"] == "A" and mx[0]["b"] == "B")
check("matrix corr=1", mx[0]["corr"] == 1.0)
check("high_corr 抓到", len(PF.high_corr_pairs(mx, 0.7)) == 1)
check("high_corr 門檻過濾", PF.high_corr_pairs(mx, 1.01) == [])

# ---------- exposure ----------
ex = PF.exposure([{"sid": "2356", "shares": 2, "price": 50},     # 100
                  {"sid": "2330", "shares": 1, "price": 300}])   # 300
check("exposure 總市值", ex["total_value"] == 400.0)
check("exposure 依市值排序", ex["positions"][0]["sid"] == "2330")
check("exposure 權重%", ex["positions"][0]["weight"] == 75.0)
check("exposure 無現金→投資比 None", ex["invested_pct"] is None)
ex2 = PF.exposure([{"sid": "2356", "shares": 2, "price": 50}], cash=100)  # 持100 現100
check("exposure 投資比 50%", ex2["invested_pct"] == 50.0)

print(f"\n通過 {passed}　失敗 {failed}")
sys.exit(1 if failed else 0)
