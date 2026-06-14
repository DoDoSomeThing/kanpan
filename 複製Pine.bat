@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 把 Pine 腳本複製到剪貼簿，貼進 TradingView Pine 編輯器
clip < pine\kanpan_vp.pine
echo Pine 腳本已複製到剪貼簿
echo TradingView - Pine 編輯器 - 全選刪掉 - 貼上 - 儲存 - 新增至圖表
pause
