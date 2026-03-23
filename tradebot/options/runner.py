# ============================================================
#  tradebot / options / runner.py
#  T10 — Unified options runner + dashboard data provider
#
#  This ties everything together:
#    - Options engine runs alongside the existing futures engine
#    - Both share the same data pipeline, regime classifier, alerts
#    - Combined P&L report shows futures + options together
#    - Dashboard endpoint updated with options-specific metrics
#
#  Run standalone options paper trading:
#    python -m options.runner
#
#  Or integrated with main.py (both futures + options):
#    python main.py --options
# ============================================================

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from options.execution import OptionsExecutionEngine
from options.data import OptionsDataPipeline, ExpiryManager
from options.backtest import OptionsBacktester
from data.pipeline import DataPipeline
from utils.logger import setup_logging
from config.settings import PRIMARY_TF, RISK

logger = logging.getLogger(__name__)


class OptionsRunner:
    """
    Standalone options paper trading runner.
    Polls for new candles and processes options signals.
    """

    def __init__(
        self,
        symbol:   str   = "BANKNIFTY",
        capital:  float = 100000,
        use_paper:bool  = True,
    ):
        self.symbol    = symbol
        self.capital   = capital
        self.engine    = OptionsExecutionEngine(symbol, capital=capital, use_paper=use_paper)
        self.dp        = DataPipeline()
        self._running  = False
        self._last_ts  = None
        self._df_window: pd.DataFrame = pd.DataFrame()

    def start(self) -> None:
        """Start paper trading options."""
        logger.info(f"Options runner starting | {self.symbol} | ₹{self.capital:,.0f}")

        # Load warmup data
        self._df_window = self.dp.get(self.symbol, PRIMARY_TF, n_bars=500)
        if self._df_window.empty:
            self.dp.fetch_and_store(self.symbol, PRIMARY_TF)
            self._df_window = self.dp.get(self.symbol, PRIMARY_TF, n_bars=500)

        logger.info(f"Warmup: {len(self._df_window)} bars")
        self._running = True

        try:
            self._loop()
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        self._running = False
        self.engine.save_trades()
        summary = self.engine.daily_summary()
        logger.info(f"Options session ended | {summary}")

    def _loop(self) -> None:
        while self._running:
            try:
                now = datetime.now()
                t   = now.hour * 60 + now.minute
                if not (9 * 60 + 15 <= t <= 15 * 60 + 30):
                    time.sleep(60)
                    continue

                candle = self.dp.get_latest_candle(self.symbol, PRIMARY_TF)
                if candle is None:
                    time.sleep(30)
                    continue

                ts = pd.to_datetime(candle["timestamp"])
                if ts == self._last_ts:
                    time.sleep(30)
                    continue

                self._last_ts = ts
                new_row = pd.DataFrame([{
                    "open": float(candle["open"]), "high": float(candle["high"]),
                    "low":  float(candle["low"]),  "close": float(candle["close"]),
                    "volume": int(candle.get("volume", 0)), "oi": 0,
                }], index=[ts])
                self._df_window = pd.concat([self._df_window, new_row]).tail(500)

                self.engine.on_new_candle(candle, ts, self._df_window)

            except Exception as e:
                logger.error(f"Options loop error: {e}", exc_info=True)
                time.sleep(30)


def run_options_backtest(symbol: str = "BANKNIFTY") -> None:
    """Run options backtest and print results."""
    setup_logging("options_backtest")
    logger.info(f"Running options backtest for {symbol}")

    dp = DataPipeline()
    df = dp.get(symbol, "5min")
    if df.empty:
        logger.info("No data. Fetching...")
        dp.fetch_and_store(symbol, "5min")
        df = dp.get(symbol, "5min")

    if df.empty:
        print("No data available. Run setup.py first.")
        return

    backtester = OptionsBacktester(symbol, lots=1)
    results    = backtester.run(df)

    print("\n" + "=" * 90)
    print(f"OPTIONS BACKTEST RESULTS — {symbol}")
    print("=" * 90)
    for name, result in results.items():
        print(f"\n{result.summary()}")
        if result.trades:
            wins = [t for t in result.trades if t.net_pnl > 0]
            print(f"  Win rate by strategy: {len(wins)}/{len(result.trades)}")
            print(f"  Avg premium captured: {result.avg_pct_captured:.1f}%")

    print("\n" + "-" * 60)
    passed = [n for n, r in results.items() if r.passed_gate]
    print(f"Gate PASSED: {passed}")
    print(f"Gate FAILED: {[n for n in results if n not in passed]}")


# ── Options payoff diagram data ──────────────────────────────
# Used by webapp dashboard to render payoff visualisation

def get_payoff_data(position, spot: float) -> dict:
    """Generate payoff curve data for dashboard chart."""
    from options.pricing import BSModel
    bs = BSModel()

    spread = max(3000, spot * 0.08)
    spot_range = [spot - spread + i * 50 for i in range(int(spread * 2 / 50) + 1)]

    legs = [
        {
            "type":    l.option_type,
            "strike":  l.strike,
            "premium": l.premium,
            "qty":     l.lots * l.lot_size,
            "action":  l.action,
        }
        for l in position.legs
    ]

    pnls = bs.pnl_at_expiry(legs, spot_range)
    stats = bs.max_profit_loss(legs, spot)

    return {
        "spot_range":    spot_range,
        "pnl":           pnls,
        "max_profit":    stats["max_profit"],
        "max_loss":      stats["max_loss"],
        "breakevens":    stats["breakevens"],
        "current_spot":  spot,
        "legs":          [{
            "strike": l.strike,
            "type":   l.option_type.upper(),
            "action": l.action.upper(),
            "premium":l.premium,
        } for l in position.legs],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Options runner")
    parser.add_argument("--symbol",    default="BANKNIFTY")
    parser.add_argument("--capital",   default=100000, type=float)
    parser.add_argument("--backtest",  action="store_true")
    parser.add_argument("--paper",     action="store_true", default=True)
    args = parser.parse_args()

    setup_logging("options")

    if args.backtest:
        run_options_backtest(args.symbol)
    else:
        runner = OptionsRunner(args.symbol, args.capital, use_paper=args.paper)
        runner.start()
