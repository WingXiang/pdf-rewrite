@echo off
echo ==========================================
echo       PDF 錯誤偵測器 - 本地端伺服器啟動
echo ==========================================
echo 正在啟動伺服器，請勿關閉此視窗...
echo (若要停止伺服器，請直接關閉本視窗)
echo.

:: 切換到虛擬環境並啟動 FastAPI 伺服器
call .\venv\Scripts\activate
python -m uvicorn main:app --port 8000 --host 0.0.0.0

pause
