# ============================================================
#  tradebot / simulation / options_backtest.py
#  B3+B4 — Historical options backtest + performance analyser
#
#  Runs all options strategies on historical data.
#  Chain is reconstructed bar-by-bar using Black-Scholes.
#  Full cost model applied. Walk-forward supported.
#
#  Usage:
#    python -m simulation.options_backtest --symbol BANKNIFTY
#    python -m simulation.options_backtest --days 180 --strategy all
# ============================================================

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime    import date, datetime, timedelta
from pathlib     import Path
import pandas    as pd
import numpy     as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from simulation.simulator    import MarketSimulator, SimMode, SimBar
from simulation.options_paper import OptionsPaperRunner
from options.chain_feed       import ChainFeed
from options.data             import ExpiryManager
from config.settings          import PROC_DIR

logger = logging.getLogger(__name__)


@dataclass
class OptionsBacktestReport:
    symbol:        str
    strategy:      str
    from_date:     str
    to_date:       str
    total_trades:  int   = 0
    win_rate:      float = 0.0
    profit_factor: float = 0.0
    total_net_pnl: float = 0.0
    avg_win:       float = 0.0
    avg_loss:      float = 0.0
    max_drawdown:  float = 0.0
    sharpe:        float = 0.0
    expectancy:    float = 0.0
    avg_dte_entry: float = 0.0
    avg_iv_rank:   float = 0.0
    passed_gate:   bool  = False

    def __str__(self) -> str:
        gate = "PASS" if self.passed_gate else "FAIL"
        return (
            f"[{gate}] {self.strategy:22s} | "
            f"Trades:{self.total_trades:4d} | "
            f"WR:{self.win_rate:5.1f}% | "
            f"PF:{self.profit_factor:5.2f} | "
            f"NetPnL:Rs.{self.total_net_pnl:8,.0f} | "
            f"MaxDD:Rs.{self.max_drawdown:7,.0f} | "
            f"Sharpe:{self.sharpe:5.2f}"
        )


class OptionsBacktestRunner:
    """
    Full options backtest using historical data + BS chain reconstruction.

    Runs through each 5-min bar of historical data.
    On each bar: reconstructs chain → runs strategies → paper fills.
    Applies full transaction costs at exit.
    """

    GATE = {
        "min_trades":     30,
        "min_win_rate":   55.0,
        "min_pf":         1.3,
        "max_drawdown":   15000,
    }

    def __init__(
        self,
        symbol:      str   = "BANKNIFTY",
        capital:     float = 100000.0,
        broker_name: str   = "zerodha",
    ):
        self.symbol      = symbol
        self.capital     = capital
        self.broker_name = broker_name

    def run(
        self,
        from_date:  str = None,
        to_date:    str = None,
        n_days:     int = 90,
        verbose:    bool = True,
    ) -> list[OptionsBacktestReport]:
        """Run options backtest and return per-strategy reports."""

        runner = OptionsPaperRunner(self.symbol, self.capital, self.broker_name)
        sim    = MarketSimulator(self.symbol, mode=SimMode.BACKTEST)
        sim.on_chain(runner.on_chain_update)

        if verbose:
            print(f"\nRunning options backtest | {self.symbol} | {n_days} days")
            print("Reconstructing option chains from Black-Scholes on each bar...")
            print("(No broker required — 100% offline simulation)\n")

        result = sim.run(from_date=from_date, to_date=to_date, n_days=n_days)

        trades_df = runner.get_trades_df()

        if verbose:
            runner.print_summary()

        reports = self._build_reports(trades_df)
        if verbose:
            self._print_reports(reports)

        # Save
        if not trades_df.empty:
            PROC_DIR.mkdir(parents=True, exist_ok=True)
            path = PROC_DIR / f"opt_backtest_{self.symbol}_{datetime.now():%Y%m%d_%H%M}.csv"
            trades_df.to_csv(path, index=False)
            if verbose:
                print(f"\nTrade log saved: {path}")

        return reports

    def _build_reports(self, df: pd.DataFrame) -> list[OptionsBacktestReport]:
        if df.empty:
            return []

        reports = []
        strategies = ["ALL"] + list(df["strategy"].unique()) if not df.empty else ["ALL"]

        for strat in strategies:
            if strat == "ALL":
                sub = df
            else:
                sub = df[df["strategy"] == strat]

            if sub.empty:
                continue

            pnls  = sub["net_pnl"].tolist()
            wins  = [p for p in pnls if p > 0]
            losses= [p for p in pnls if p < 0]
            n     = len(pnls)

            wr = len(wins) / n * 100 if n else 0
            pf = sum(wins) / abs(sum(losses)) if losses else 0
            avg= np.mean(pnls) if pnls else 0
            std= np.std(pnls)  if len(pnls) > 1 else 1
            sh = avg / std * (252 ** 0.5) if std > 0 else 0

            cum     = pd.Series(pnls).cumsum()
            max_dd  = float((cum - cum.cummax()).min())

            passed = (
                n   >= self.GATE["min_trades"]      and
                wr  >= self.GATE["min_win_rate"]     and
                pf  >= self.GATE["min_pf"]           and
                abs(max_dd) <= self.GATE["max_drawdown"]
            )

            r = OptionsBacktestReport(
                symbol        = self.symbol,
                strategy      = strat,
                from_date     = str(sub["entry_time"].min())[:10] if "entry_time" in sub else "",
                to_date       = str(sub["entry_time"].max())[:10] if "entry_time" in sub else "",
                total_trades  = n,
                win_rate      = round(wr, 1),
                profit_factor = round(pf, 2),
                total_net_pnl = round(sum(pnls), 2),
                avg_win       = round(np.mean(wins)   if wins   else 0, 2),
                avg_loss      = round(np.mean(losses) if losses else 0, 2),
                max_drawdown  = round(abs(max_dd), 2),
                sharpe        = round(sh, 2),
                expectancy    = round(float(avg), 2),
                avg_dte_entry = round(float(sub["dte_at_entry"].mean()), 1) if "dte_at_entry" in sub else 0,
                avg_iv_rank   = round(float(sub["iv_rank_entry"].mean()), 1) if "iv_rank_entry" in sub else 0,
                passed_gate   = passed,
            )
            reports.append(r)

        return reports

    def _print_reports(self, reports: list) -> None:
        if not reports:
            print("No trades — check data availability and strategy filters.")
            return

        print(f"\n{'='*85}")
        print(f" OPTIONS BACKTEST RESULTS — {self.symbol}")
        print(f"{'='*85}")
        print(f" Gate: trades≥{self.GATE['min_trades']} | WR≥{self.GATE['min_win_rate']}% | PF≥{self.GATE['min_pf']} | MaxDD≤Rs.{self.GATE['max_drawdown']:,}")
        print(f"{'─'*85}")
        for r in reports:
            print(f" {r}")
        print(f"{'='*85}")

        passed = [r for r in reports if r.passed_gate and r.strategy != "ALL"]
        if passed:
            print(f"\n ✓ Strategies ready for paper trading: {[r.strategy for r in passed]}")
        else:
            print(f"\n ✗ No strategies passed gate — review filters and data range")


def run_quick_demo(symbol: str = "BANKNIFTY") -> None:
    """
    Quick demo — simulates options trading on last 30 days.
    Shows that the entire system works without any broker.
    """
    print(f"\n{'='*55}")
    print(f" OPTIONS SIMULATION DEMO — {symbol}")
    print(f" Running on last 30 days of historical data")
    print(f" No broker account needed")
    print(f"{'='*55}\n")

    runner = OptionsBacktestRunner(symbol)
    reports = runner.run(n_days=30, verbose=True)
    return reports


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.logger import setup_logging
    setup_logging("options_backtest")

    parser = argparse.ArgumentParser(description="Options historical backtest")
    parser.add_argument("--symbol",   default="BANKNIFTY")
    parser.add_argument("--days",     default=90, type=int)
    parser.add_argument("--capital",  default=100000, type=float)
    parser.add_argument("--broker",   default="zerodha")
    args = parser.parse_args()

    runner = OptionsBacktestRunner(args.symbol, args.capital, args.broker)
    runner.run(n_days=args.days, verbose=True)
