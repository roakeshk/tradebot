#!/usr/bin/env python3
# ============================================================
#  tradebot / run_backtest.py
#  Entry point for full walk-forward backtest.
#
#  Usage:
#      python run_backtest.py
#      python run_backtest.py --symbol NIFTY --lots 1
#      python run_backtest.py --broker shoonya --quick
#
#  Output:
#    - Console summary table
#    - CSV trade log in data/processed/
#    - Gate check result (PASS/FAIL per strategy)
# ============================================================

import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from utils.logger import setup_logging
from data.pipeline import DataPipeline
from strategy.strategies import build_strategies
from backtest.engine import WalkForwardRunner, BacktestEngine
from config.settings import PROC_DIR, INSTRUMENTS, MARKET, RISK


def _default_symbol():
    return list(INSTRUMENTS.keys())[0] if INSTRUMENTS else "SPY"

def _default_broker():
    return "us_paper" if MARKET == "US" else "zerodha"

_CURRENCY = "$" if MARKET == "US" else "₹"


def main():
    parser = argparse.ArgumentParser(description="Run walk-forward backtest")
    parser.add_argument("--symbol",    default=_default_symbol(), choices=list(INSTRUMENTS.keys()))
    parser.add_argument("--lots",      default=1, type=int)
    parser.add_argument("--broker",    default=_default_broker(), choices=["zerodha", "shoonya", "paper", "us_paper"])
    parser.add_argument("--timeframe", default="5min")
    parser.add_argument("--quick",     action="store_true", help="Single-pass backtest (faster)")
    args = parser.parse_args()

    logger = setup_logging("backtest")
    logger.info(f"Starting backtest | {args.symbol} | {args.broker} | lots={args.lots}")

    # ── Load data ─────────────────────────────────────────────
    dp = DataPipeline()
    logger.info("Loading data...")
    df = dp.get(args.symbol, args.timeframe)

    if df.empty:
        logger.info("No cached data found. Fetching now (this may take a few minutes)...")
        dp.fetch_and_store(args.symbol, args.timeframe, force=True)
        df = dp.get(args.symbol, args.timeframe)

    if df.empty:
        print(f"\nERROR: No data available for {args.symbol}. Run setup.py first.")
        sys.exit(1)

    logger.info(f"Loaded {len(df):,} bars | {df.index[0]} → {df.index[-1]}")

    # ── Build strategies ──────────────────────────────────────
    strategies = build_strategies(args.symbol)
    logger.info(f"Strategies: {[s.name for s in strategies]}")

    # ── Run backtest ──────────────────────────────────────────
    PROC_DIR.mkdir(parents=True, exist_ok=True)

    if args.quick:
        # Single-pass backtest on full dataset (faster, use for development)
        print("\n" + "=" * 100)
        print(f"SINGLE-PASS BACKTEST | {args.symbol} | {args.broker} | {len(df):,} bars")
        print("=" * 100)

        engine = BacktestEngine(args.symbol, args.lots, args.broker)
        all_trades = []

        for strat in strategies:
            result = engine.run(df, strat, is_oos=False)
            result.compute_metrics()
            print(f"\n{result.summary()}")
            all_trades.extend(result.trades)

        # Save trades to CSV
        if all_trades:
            trades_df = pd.DataFrame([vars(t) for t in all_trades])
            trades_df["direction"] = trades_df["direction"].apply(lambda x: x.value)
            out_path = PROC_DIR / f"backtest_{args.symbol}_{datetime.now():%Y%m%d_%H%M}.csv"
            trades_df.to_csv(out_path, index=False)
            print(f"\nTrade log saved to: {out_path}")

    else:
        # Full walk-forward analysis
        print("\n" + "=" * 100)
        print(f"WALK-FORWARD BACKTEST | {args.symbol} | {args.broker} | {len(df):,} bars")
        print("=" * 100)
        print("(Running multiple windows — this takes 1–3 minutes)")

        runner = WalkForwardRunner(
            symbol=args.symbol,
            lots=args.lots,
            broker=args.broker,
            in_sample_months=6,
            oos_months=1,
        )
        results = runner.run(df, strategies)

        print("\n" + "=" * 100)
        print("AGGREGATED OUT-OF-SAMPLE RESULTS")
        print("=" * 100)
        summary = runner.aggregate_oos(results)
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 200)
        print(summary.to_string(index=False))

        # Gate check
        print("\n" + "─" * 60)
        print("GATE CHECK (minimum to proceed to paper trading)")
        print("─" * 60)
        for _, row in summary.iterrows():
            status = "PASS" if row["passed_gate"] else "FAIL"
            print(f"  [{status}] {row['strategy']}")
            if not row["passed_gate"]:
                if row["total_trades"] < 200:
                    print(f"         Need ≥200 trades, got {row['total_trades']}")
                if row["win_rate"] < 55:
                    print(f"         Need ≥55% win rate, got {row['win_rate']:.1f}%")
                if row["profit_factor"] < 1.4:
                    print(f"         Need PF≥1.4, got {row['profit_factor']:.2f}")
                dd_limit = RISK["max_capital"] * (RISK["max_drawdown_pct"] / 100)
                if row["max_drawdown"] >= dd_limit:
                    print(f"         Need MaxDD<{_CURRENCY}{dd_limit:,.0f}, got {_CURRENCY}{row['max_drawdown']:,.0f}")

        # Save full trade logs
        all_trades = [t for res_list in results.values() for r in res_list for t in r.trades]
        if all_trades:
            trades_df = pd.DataFrame([vars(t) for t in all_trades])
            trades_df["direction"] = trades_df["direction"].apply(lambda x: x.value)
            out_path = PROC_DIR / f"wf_trades_{args.symbol}_{datetime.now():%Y%m%d_%H%M}.csv"
            trades_df.to_csv(out_path, index=False)
            print(f"\nFull trade log: {out_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
