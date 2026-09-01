@echo off
chcp 65001 >nul
cd /d %~dp0
rem 源码模式运行；加 --settings 可直接打开设置界面
py -3.12 main.py --settings
pause
