@echo off
REM ============================================================
REM  TradeBot — One-command launcher (Windows)
REM
REM  Usage:
REM    run.bat                    Paper trading (default: SPY)
REM    run.bat --mode simulate    Full simulation
REM    run.bat --mode analyze     Stock analysis
REM    run.bat --mode screen      Multi-stock screener
REM    run.bat --symbol TSLA      Trade a different symbol
REM    run.bat --help             Show all options
REM ============================================================

cd /d "%~dp0"

REM Activate venv if present
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

REM Install deps if needed (first run)
python -c "import pandas" 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r tradebot\requirements.txt
)

REM Launch
cd tradebot
python main.py %*
