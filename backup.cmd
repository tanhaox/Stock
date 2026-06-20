@echo off
REM ============================================================
REM Stock 智能备份脚本 - Windows 版
REM
REM 用法:
REM   双击运行         - 默认模式 (check + push)
REM   命令行带参数      - backup.cmd check / push / snapshot / flush
REM
REM 依赖: Git Bash (项目使用 git-for-windows, 一般已装)
REM ============================================================

chcp 65001 >nul
cd /d "%~dp0"

REM 检查 Git Bash
where bash >nul 2>&1
if errorlevel 1 (
    echo [ERR] 找不到 bash, 请安装 Git for Windows
    pause
    exit /b 1
)

REM 默认参数
set "MODE=%~1"
if "%MODE%"=="" set "MODE=default"

echo ================================================
echo   Stock 智能备份脚本 (Windows)
echo   %date% %time%
echo ================================================

bash backup.sh %MODE%

echo.
pause