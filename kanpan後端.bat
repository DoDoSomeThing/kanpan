@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ===============================
echo   kanpan 後端啟動中 (port 8771)
echo   Chrome 擴充靠這個
echo   要停止：關掉這個視窗
echo ===============================
rem 清掉殘留的舊後端，避免 port 被佔
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8771 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
rem 資料不用手動更新：看單檔時 load_bars 自動抓 FinMind 最新
python api.py
pause
