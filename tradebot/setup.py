#!/usr/bin/env python3
# ============================================================
#  tradebot / setup.py
#  One-time setup script. Run this first.
#
#  Usage:
#      python setup.py
#
#  What it does:
#    1. Installs required packages
#    2. Creates all directories
#    3. Fetches initial historical data
#    4. Runs cost model sanity check
#    5. Confirms everything is working
# ============================================================

import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent

PACKAGES = [
    "pandas>=2.0",
    "numpy>=1.24",
    "yfinance>=0.2",
    "requests>=2.31",
    "python-dateutil>=2.8",
    "pytz>=2023.3",
    "ta>=0.11",          # technical indicators (pure python, no C dependency)
    "streamlit>=1.28",   # monitoring dashboard (Phase 4)
    "scipy>=1.11",       # statistics for walk-forward analysis
    "scikit-learn>=1.3", # ML (Phase 2)
    "xgboost>=2.0",      # gradient boosted trees (Phase 2)
]

def install_packages():
    print("\n[1/5] Installing packages...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--quiet", *PACKAGES
    ])
    print("      Done.")

def create_dirs():
    print("\n[2/5] Creating directories...")
    dirs = [
        "data/raw", "data/processed", "data/cache",
        "broker", "strategy", "backtest", "risk",
        "execution", "monitor", "utils", "config",
        "logs", "paper_trading",
    ]
    for d in dirs:
        (BASE / d).mkdir(parents=True, exist_ok=True)
    # Create __init__.py files so Python treats dirs as packages
    for d in dirs:
        init = BASE / d / "__init__.py"
        if not init.exists():
            init.write_text("")
    (BASE / "__init__.py").touch(exist_ok=True)
    print("      Done.")

def fetch_data():
    print("\n[3/5] Fetching initial historical data (this may take a few minutes)...")
    sys.path.insert(0, str(BASE))
    try:
        from data.pipeline import DataPipeline
        from config.settings import DATA
        dp = DataPipeline()
        for sym in ["BANKNIFTY", "NIFTY"]:
            for tf in ["5min", "15min", "1day"]:
                print(f"      Fetching {sym} {tf}...", end="", flush=True)
                n = dp.fetch_and_store(sym, tf, years_back=1, force=False)
                print(f" {n} rows")
        print("\n      Data summary:")
        print(dp.data_summary().to_string(index=False))
    except Exception as e:
        print(f"      WARNING: Data fetch failed ({e}). Run 'python -m data.pipeline' manually.")

def cost_check():
    print("\n[4/5] Running cost model sanity check...")
    sys.path.insert(0, str(BASE))
    try:
        from risk.cost_model import CostModel
        cm = CostModel("zerodha")
        bep = cm.min_points_to_breakeven("BANKNIFTY", lots=1, price=48000)
        proj = cm.annual_cost_estimate("BANKNIFTY", 1, 48000, trades_per_day=5)
        print(f"      BankNifty 1 lot @ ₹48,000:")
        print(f"        Breakeven points per trade: {bep}")
        print(f"        Cost per round-trip:  ₹{proj['cost_per_round_trip_inr']}")
        print(f"        Annual cost (5 trades/day): ₹{proj['annual_cost_inr']:,.0f}")
        print(f"      Done.")
    except Exception as e:
        print(f"      WARNING: Cost check failed: {e}")

def final_check():
    print("\n[5/5] Final checks...")
    checks = {
        "config/settings.py":   BASE / "config/settings.py",
        "broker/base.py":        BASE / "broker/base.py",
        "broker/paper_broker.py":BASE / "broker/paper_broker.py",
        "data/pipeline.py":      BASE / "data/pipeline.py",
        "risk/cost_model.py":    BASE / "risk/cost_model.py",
        "utils/logger.py":       BASE / "utils/logger.py",
    }
    all_ok = True
    for name, path in checks.items():
        status = "OK" if path.exists() else "MISSING"
        if status == "MISSING":
            all_ok = False
        print(f"      [{status}] {name}")

    print()
    if all_ok:
        print("  Setup complete. Phase 1 foundation is ready.")
        print("  Next step: run the backtest framework builder.")
    else:
        print("  Some files are missing. Re-run setup.py.")

if __name__ == "__main__":
    print("=" * 60)
    print(" TRADEBOT — Phase 1 Setup")
    print("=" * 60)
    install_packages()
    create_dirs()
    fetch_data()
    cost_check()
    final_check()
