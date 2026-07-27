@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -m pip install -q -r requirements.txt
py -3 -m app.main
if errorlevel 1 pause
