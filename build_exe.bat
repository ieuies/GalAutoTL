@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 安装依赖...
py -3 -m pip install -q -r requirements.txt
echo 打包 GalAutoTL.exe ...
py -3 -m PyInstaller --noconfirm --clean --windowed --onefile --name GalAutoTL --paths . --icon app\assets\galautotl.ico --add-data "app\assets;app\assets" --collect-all UnityPy --hidden-import UnityPy --hidden-import UnityPy.files app\main.py
if errorlevel 1 (
  echo 打包失败
  pause
  exit /b 1
)
echo.
echo 完成: dist\GalAutoTL.exe
explorer dist
pause
