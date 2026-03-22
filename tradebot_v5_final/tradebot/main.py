#!/usr/bin/env python3
# ============================================================
#  tradebot / main.py
#  Single entry point — starts the complete trading system.
#
#  Usage:
#    python main.py                        # paper trading (default)
#    python main.py --mode paper           # explicit paper
#    python main.py --mode live            # live trading (confirm first!)
#    python main.py --symbol NIFTY         # different instrument
#    python main.py --no-ai                # algo-only, skip AI layer
#    python main.py --mode live --confirm  # live trading safety flag
# ============================================================

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from utils.logger import setup_logging
from config.settings import ACTIVE_BROKER


def main():
    parser = argparse.ArgumentParser(
        description="TradeBot — Autonomous Intraday Trading System"
    )
    parser.add_argument("--mode",    default="paper", choices=["paper", "live"])
    parser.add_argument("--symbol",  default="BANKNIFTY", choices=["BANKNIFTY", "NIFTY", "CRUDEOIL"])
    parser.add_argument("--capital", default=100000, type=float)
    parser.add_argument("--no-ai",   action="store_true", help="Disable AI layer (algo-only)")
    parser.add_argument("--confirm", action="store_true", help="Required for live mode")
    args = parser.parse_args()

    logger = setup_logging("main")

    print("\n" + "=" * 60)
    print("  TRADEBOT — Autonomous Intraday Trading System")
    print("=" * 60)

    # ── Safety check for live mode ────────────────────────────
    if args.mode == "live":
        if not args.confirm:
            print("\n⚠️  LIVE MODE requires --confirm flag.")
            print("   This will trade with REAL MONEY.")
            print("   Ensure paper trading gate has been PASSED.")
            print("   Re-run with: python main.py --mode live --confirm")
            sys.exit(1)

        broker = ACTIVE_BROKER
        if broker == "paper":
            print("\n❌ ACTIVE_BROKER is still 'paper' in settings.py")
            print("   Set ACTIVE_BROKER = 'zerodha' or 'shoonya' before live trading.")
            sys.exit(1)

        print(f"\n🔴 LIVE TRADING MODE — broker={broker}")
        print("   Starting in 5 seconds. Press Ctrl+C to abort...")
        import time
        time.sleep(5)

    else:
        print(f"\n📝 PAPER TRADING MODE")

    print(f"   Symbol:  {args.symbol}")
    print(f"   Capital: ₹{args.capital:,.0f}")
    print(f"   AI:      {'OFF' if args.no_ai else 'ON'}")
    print()

    # ── Start engine ──────────────────────────────────────────
    from execution.engine import ExecutionEngine

    broker_override = "paper" if args.mode == "paper" else None

    engine = ExecutionEngine(
        symbol=args.symbol,
        capital=args.capital,
        use_ai=not args.no_ai,
        broker_name=broker_override,
    )
    engine.start()


if __name__ == "__main__":
    main()
