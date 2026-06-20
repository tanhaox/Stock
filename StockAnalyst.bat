@echo off
REM *** Switch to UTF-8 codepage (avoid GBK cmd parse errors on Chinese)
chcp 65001 >nul
title Stock Analyst
setlocal

set BACKEND_DIR=C:\AI-Agent-Local\Stock\backend
set FRONTEND_DIR=C:\AI-Agent-Local\Stock\frontend
set BACKEND_PORT=8000
set FRONTEND_PORT=5173
set LOG_DIR=C:\AI-Agent-Local\Stock\logs

REM Workers config (default: 4 workers for parallel TG scan)
set NUM_WORKERS=%1
if "%NUM_WORKERS%"=="" set NUM_WORKERS=4
set SKIP_DOWNLOAD=%2

echo.
echo ========================================
echo   Stock Analyst Launcher (v4.9)
echo   Workers: %NUM_WORKERS%
echo ========================================
echo.

REM Kill stale (enhanced: port + title + fallback, no false-kill)
echo  [0] Cleaning...
REM 1. Port-precise kill (8000=uvicorn, 5173=vite)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000.*LISTENING"') do (
    taskkill /F /PID %%P >NUL 2>&1
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5173.*LISTENING"') do (
    taskkill /F /PID %%P >NUL 2>&1
)
REM 2. Title-precise kill (windows started by this bat)
taskkill /F /FI "WINDOWTITLE eq StockAnalyst-Backend*" >NUL 2>&1
taskkill /F /FI "WINDOWTITLE eq StockAnalyst-Frontend*" >NUL 2>&1
ping -n 3 127.0.0.1 >NUL
echo       Done

REM PostgreSQL
echo  [1] PostgreSQL...
docker start stock-postgres >NUL 2>&1
ping -n 4 127.0.0.1 >NUL
echo       Ready

REM Backend: single process + ProcessPoolExecutor workers
echo  [2] Backend (workers=%NUM_WORKERS%)...
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set UVI_CMD=python -B -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%
REM NOTE: NUM_WORKERS controls ProcessPoolExecutor, NOT uvicorn workers
REM /MIN = minimized window + title "StockAnalyst-Backend" for precise cleanup
REM Independent window does NOT steal bat's stdin (fixes pause broken)
REM *** Use cmd /k to keep window open (cmd /c exits immediately, looks like flash-crash)

start "StockAnalyst-Backend" /MIN cmd /k "cd /d %BACKEND_DIR% && set NUM_WORKERS=%NUM_WORKERS% && %UVI_CMD% > %LOG_DIR%\backend.log 2>&1"

echo       Waiting...
for /l %%n in (1,1,30) do (
    curl -s http://127.0.0.1:%BACKEND_PORT%/api/health >NUL 2>&1
    if !errorlevel!==0 goto be_ready
    ping -n 2 127.0.0.1 >NUL
)
echo       [WARN] Backend timeout
goto backend_done

:be_ready
echo       Backend ready

:backend_done
REM Frontend
echo  [3] Frontend :%FRONTEND_PORT%...

if not exist "%FRONTEND_DIR%\node_modules" (
    echo       Installing deps...
    cd /d "%FRONTEND_DIR%" && call npm install
)

REM /MIN + title "StockAnalyst-Frontend" for precise cleanup, no stdin steal
REM *** cmd /k keeps window open, log redirected to file

start "StockAnalyst-Frontend" /MIN cmd /k "cd /d %FRONTEND_DIR% && npx vite --port %FRONTEND_PORT% --host 127.0.0.1 --no-open > %LOG_DIR%\frontend.log 2>&1"

echo       Waiting...
for /l %%n in (1,1,15) do (
    curl -s http://127.0.0.1:%FRONTEND_PORT% >NUL 2>&1
    if !errorlevel!==0 goto fe_ready
    ping -n 2 127.0.0.1 >NUL
)
echo       [WARN] Frontend timeout
goto open_browser

:fe_ready
echo       Frontend ready

:open_browser
echo  [4] Opening browser...
start http://127.0.0.1:%FRONTEND_PORT%

echo.
echo ========================================
echo   Stock Analyst Running
echo   Frontend : http://127.0.0.1:%FRONTEND_PORT%
echo   Backend  : http://127.0.0.1:8000/api
echo ========================================
echo.
echo   Press any key to stop...
echo   Logs: %LOG_DIR%\backend.log  /  %LOG_DIR%\frontend.log
pause >NUL

echo   Stopping services...

REM 1. Port-precise kill (no false-kill of other python/node processes)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000.*LISTENING"') do (
    taskkill /F /PID %%P >NUL 2>&1
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5173.*LISTENING"') do (
    taskkill /F /PID %%P >NUL 2>&1
)

REM 2. Let children exit naturally (ProcessPoolExecutor workers clean up)
ping -n 2 127.0.0.1 >NUL

REM 3. Title-precise kill with /T (tree-kill uvicorn/vite children)
taskkill /F /T /FI "WINDOWTITLE eq StockAnalyst-Backend*" >NUL 2>&1
taskkill /F /T /FI "WINDOWTITLE eq StockAnalyst-Frontend*" >NUL 2>&1

REM 4. Fallback: image-name sweep (only if previous steps fail)
REM    postgres.exe lives in docker container, safe from host taskkill
taskkill /F /IM node.exe >NUL 2>&1
taskkill /F /IM python.exe >NUL 2>&1

REM 5. Verify ports released
netstat -ano | findstr ":8000.*LISTENING" >NUL 2>&1 && echo       [WARN] port 8000 still in use
netstat -ano | findstr ":5173.*LISTENING" >NUL 2>&1 && echo       [WARN] port 5173 still in use

echo   Done.