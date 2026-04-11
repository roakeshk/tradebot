#!/usr/bin/env bash
# ============================================================
#  TradeBot — One-command launcher (Linux/Mac)
#
#  Usage:
#    ./run.sh                    Paper trading (default: SPY)
#    ./run.sh --mode simulate    Full simulation
#    ./run.sh --mode analyze     Stock analysis
#    ./run.sh --mode screen      Multi-stock screener
#    ./run.sh --symbol TSLA      Trade a different symbol
#    ./run.sh --help             Show all options
# ============================================================

set -e
cd "$(dirname "$0")"

# Activate venv if present
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

# Install deps if needed (first run)
if ! python3 -c "import pandas" 2>/dev/null; then
    echo "Installing dependencies..."
    pip install -r tradebot/requirements.txt
fi

# Launch
cd tradebot
python3 main.py "$@"
