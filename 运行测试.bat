@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing pytest (if needed)...
py -3 -m pip install -q pytest
echo.
echo Running GalAutoTL regression tests...
py -3 -m pytest tests -q
echo.
pause
