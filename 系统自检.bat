@echo off
chcp 65001 >nul
cd /d "%~dp0backend"
echo.
echo ========================================
echo   Stock Analyst 系统自检
echo ========================================
echo.
python system_check.py --quick %*
echo.
pause
