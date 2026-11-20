@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [1/2] 运行单元测试...
python -m unittest discover -s tests -v
if errorlevel 1 (
    echo 单元测试未通过，已终止打包。
    pause
    exit /b 1
)

echo [2/2] 正在使用 PyInstaller 打包单文件 EXE...
python -m PyInstaller --noconfirm --clean --noconsole --onefile ^
  --name "迈从电量托盘" ^
  --icon "assets\app.ico" ^
  --version-file "version_info.txt" ^
  --collect-all hidapi ^
  --paths "." ^
  mchose_battery.py

if errorlevel 1 (
    echo 打包失败，请检查 conda rec-demo 环境依赖是否安装完整。
    pause
    exit /b 1
)

echo.
echo 打包成功！生成文件位于: %~dp0dist\迈从电量托盘.exe
pause
