@echo off
rem 一鍵部署到 Vercel（第一次大約 5 分鐘；之後改了程式再雙擊一次就會更新）
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\vercel_setup.ps1"
pause
