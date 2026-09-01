@echo off
chcp 65001 >nul
cd /d %~dp0
echo ===== QuickTool 打包 =====
py -3.12 -m PyInstaller QuickTool.spec --noconfirm --clean
if errorlevel 1 (
  echo [打包失败] 请确认已安装： py -3.12 -m pip install pyinstaller
) else (
  echo [完成] 输出： dist\QuickTool.exe
  for %%F in (dist\QuickTool.exe) do echo 体积： %%~zF 字节
)
pause
