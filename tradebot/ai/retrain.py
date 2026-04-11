#!/usr/bin/env python3
# ============================================================
#  tradebot / ai / retrain.py
#  Weekly retraining pipeline for Engine B classifiers.
#
#  Run every Sunday evening (or trigger when accuracy drops):
#    python -m ai.retrain
#    python -m ai.retrain --symbol SPY --force
#
#  What it does:
#    1. Loads latest OHLCV data from cache
#    2. Loads all paper/live trade logs
#    3. Trains XGBoost per strategy on combined data
#    4. Evaluates OOS accuracy — rejects if < 52%
#    5. Saves new model only if better than existing
#    6. Sends summary report via alerts
# ============================================================

import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.logger import setup_logging
from data.pipeline import DataPipeline
from ai.features import FeatureEngine
from ai.classifier import MultiStrategyClassifier
from strategy.strategies import ALL_STRATEGIES
from config.settings import PROC_DIR, PRIMARY_TF, INSTRUMENTS

logger = logging.getLogger(__name__)


def load_all_trades(symbol: str) -> pd.DataFrame:
    """Load and merge all trade logs from paper and live trading."""
    frames = []

    # Walk-forward backtest trades
    for f in sorted(PROC_DIR.glob(f"wf_trades_{symbol}_*.csv")):
        df = pd.read_csv(f)
        df["source"] = "backtest"
        frames.append(df)

    # Paper trading logs
    for f in sorted(PROC_DIR.glob(f"paper_{symbol}_*.csv")):
        df = pd.read_csv(f)
        df["source"] = "paper"
        frames.append(df)

    # Live trading logs
    for f in sorted(PROC_DIR.glob(f"live_{symbol}_*.csv")):
        df = pd.read_csv(f)
        df["source"] = "live"
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    logger.info(f"Loaded {len(combined)} trades from {len(frames)} files")
    return combined


def main():
    parser = argparse.ArgumentParser(description="Retrain AI classifiers")
    _def_sym = list(INSTRUMENTS.keys())[0] if INSTRUMENTS else "SPY"
    parser.add_argument("--symbol",    default=_def_sym)
    parser.add_argument("--timeframe", default="5min")
    parser.add_argument("--force",     action="store_true", help="Force retrain even if no new data")
    parser.add_argument("--threshold", default=0.55, type=float)
    args = parser.parse_args()

    setup_logging("retrain")
    logger.info(f"Starting retraining | {args.symbol} {args.timeframe}")

    # ── Load data ─────────────────────────────────────────────
    dp = DataPipeline()
    df = dp.get(args.symbol, args.timeframe)
    if df.empty:
        logger.error("No data available. Run data pipeline first.")
        sys.exit(1)
    logger.info(f"Data: {len(df)} bars {df.index[0]} → {df.index[-1]}")

    # ── Load trades ───────────────────────────────────────────
    trades = load_all_trades(args.symbol)
    if trades.empty:
        logger.error("No trades found. Run backtest first to generate training data.")
        sys.exit(1)

    # ── Train classifiers ─────────────────────────────────────
    strategy_names = list(ALL_STRATEGIES.keys())
    clf_manager    = MultiStrategyClassifier(strategy_names, threshold=args.threshold)

    print("\n" + "=" * 70)
    print(f"RETRAINING AI CLASSIFIERS | {args.symbol} | {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 70)

    results = clf_manager.train_all(df, trades)

    # ── Report ────────────────────────────────────────────────
    print("\nRESULTS:")
    print("-" * 70)
    for name, res in results.items():
        if "error" in res:
            print(f"  {name:35s} SKIP — {res['error']}")
            continue
        gate   = "PASS" if res.get("passed_gate") else "FAIL"
        print(
            f"  {name:35s} [{gate}] "
            f"acc={res['accuracy']:.3f} auc={res['auc']:.3f} "
            f"prec={res['precision']:.3f}"
        )
        if res.get("top_features"):
            top3 = list(res["top_features"].keys())[:3]
            print(f"    Top features: {', '.join(top3)}")

    passed = sum(1 for r in results.values() if r.get("passed_gate"))
    print(f"\n{passed}/{len(results)} classifiers passed gate (acc≥52%, auc≥55%)")
    print(f"Models saved to: {Path('data/models').absolute()}")
    print("\nRetraining complete.")


if __name__ == "__main__":
    main()
