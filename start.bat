@echo off
chcp 65001 >nul
title AI 理專對練教練
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo 找不到 Python，請先安裝 Python 3.10 以上版本。
  pause
  exit /b 1
)

tasklist /fi "imagename eq ollama.exe" | find /i "ollama.exe" >nul
if errorlevel 1 (
  if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
    echo 正在啟動 Ollama...
    start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
    timeout /t 3 /nobreak >nul
  )
)

rem 之前開著的對練教練會被自動關掉，換成這次的新版
python server.py
rem 99：這個視窗的伺服器已經被新開的取代，直接關掉視窗
if %errorlevel%==99 exit /b
pause
