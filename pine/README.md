# kanpan Pine 版（疊在 TradingView 圖上）

`kanpan_vp.pine` = 把本機 kanpan 的 VP Score + 判讀燈號 + A–G 拆解搬進 TradingView，
直接畫在 K 線圖上（箭頭 + 均線 + POC/壓力/支撐軌道 + 右下表格）。

## 安裝
1. 開 TradingView，任一台股圖（如 2330）。
2. 下方「Pine 編輯器」→ 貼上 `kanpan_vp.pine` 全文 → 儲存 → 「加到圖表」。
3. 換股自動重算，免本機後端。

## 畫什麼
- K線箭頭：net 共振分跨門檻 → 「多」(轉多) / 「空」(轉空)
- 均線 MA20(黃)/MA60(藍)
- POC中軸(紫)、VAH壓力(紅)、VAL支撐(綠)
- 右下表格：判讀燈號 + A–G 七行（同本機面板）

## 跟本機擴充的差別
| | Pine 版 | 本機擴充 |
|---|---|---|
| 資料 | TradingView 自帶 | 本機日K + FinMind |
| 三大法人 | ❌ 抓不到 | ✅ 有(FinMind/T86) |
| 回測勝率 | ❌ 沒有(顯示 net 共振數) | ✅ 有(score_stats) |
| 畫在K線上 | ✅ | ❌(圖用網站原生) |

兩套並存：圖上即時看訊號用 Pine；要法人+回測勝率看本機擴充。

---

# justin_fib.pine — 自動 Fib + 關鍵價位 + MA

獨立小指標，跟 kanpan_vp 無關。自動畫 Fib 回撤 + 壓力支撐標籤 + 均線。

## 功能
- **Fib 回撤** 0/.236/.382/.5/.618/.786/1/1.618（色塊 + 標籤），公式 `p = hiP - rng*r`，對原圖數字驗過正確
- **波段抓法** 下拉切：Pivot 真轉折（`ta.pivothigh/low`，顯紅綠三角）／ Lookback 區間極值（`ta.highest/lowest`）
- **壓力支撐自動標**：線價 > 現價=紅壓力、< 現價=綠支撐（用 `close` 判）
- **MA5(橘)/MA20(藍)**
- **關鍵水平線** h1~h4：`input.price`，填 0 不畫
- 左上標 `代號 現價`

## 安裝
Pine 編輯器貼全文 → 儲存 → 加到圖表。指標名要顯示 **「Justin Fib + 關鍵價位」**（無 "(Pivot)" = 舊檔，載錯）。

## 限制（誠實標）
- Pine 自動畫的線**不能手動拖**。要拖動改用 TradingView 原生 Fib 回撤工具 + 水平線工具（左工具列）。
- Pine 雷已修：①不准逗號串接語句 `f(),g()`；②函式內 push 全域 array 改 inline；③`for 0 to size-1` 當 array 空會倒數越界爆，刪除迴圈加 `if size>0` 守衛。

## 開發日誌
- 2026-06-23：建 justin_fib.pine。原拆 pivot/lookback 兩檔，後合一檔下拉切模式。加壓力支撐自動標、MA5/MA20。源自驗證 06-04 兩檔手畫 Fib 劇本（2408/2377）後，把可自動化的 Fib+水平線做成指標；手畫箭頭劇本主觀無法復刻。

## 已知近似（同本機，誠實標）
B 市場狀態 / C 頂底Excess / G 價位匯聚 = 日K近似（無盤中 tick）。
真 Volume Profile / AVWAP 需逐筆資料，Pine 日線一樣算不出，不假裝。
