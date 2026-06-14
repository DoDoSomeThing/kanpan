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

## 已知近似（同本機，誠實標）
B 市場狀態 / C 頂底Excess / G 價位匯聚 = 日K近似（無盤中 tick）。
真 Volume Profile / AVWAP 需逐筆資料，Pine 日線一樣算不出，不假裝。
