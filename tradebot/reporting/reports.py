# ============================================================
#  tradebot / reporting / reports.py
#  Daily and monthly P&L reporting.
#
#  Usage:
#    python -m reporting.reports --period today
#    python -m reporting.reports --period month
#    python -m reporting.reports --period all
# ============================================================

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


def load_trades(period: str = "all") -> pd.DataFrame:
    log_dir = Path("data/processed")
    files   = sorted(log_dir.glob("*.csv"))
    if not files:
        return pd.DataFrame()

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f)
            df["_source"] = f.name
            frames.append(df)
        except Exception:
            pass

    if not frames:
        return pd.DataFrame()

    all_df = pd.concat(frames, ignore_index=True)

    # Parse timestamps
    for col in ["entry_time", "time", "timestamp"]:
        if col in all_df.columns:
            all_df["_dt"] = pd.to_datetime(all_df[col], errors="coerce")
            break

    if "_dt" not in all_df.columns:
        return all_df

    now = datetime.now()
    if period == "today":
        all_df = all_df[all_df["_dt"].dt.date == now.date()]
    elif period == "week":
        week_start = now - timedelta(days=now.weekday())
        all_df = all_df[all_df["_dt"] >= week_start]
    elif period == "month":
        all_df = all_df[
            (all_df["_dt"].dt.year == now.year) &
            (all_df["_dt"].dt.month == now.month)
        ]

    return all_df


def print_report(trades: pd.DataFrame, period: str) -> None:
    if trades.empty or "net_pnl" not in trades.columns:
        print(f"\nNo trade data found for period: {period}")
        return

    pnl     = trades["net_pnl"]
    wins    = pnl[pnl > 0]
    losses  = pnl[pnl < 0]
    n       = len(pnl)

    # Metrics
    total_pnl    = pnl.sum()
    win_rate     = len(wins) / n * 100 if n else 0
    profit_factor= wins.sum() / abs(losses.sum()) if len(losses) > 0 else 0
    avg_win      = wins.mean()  if len(wins)   > 0 else 0
    avg_loss     = losses.mean() if len(losses) > 0 else 0
    expectancy   = pnl.mean()
    max_win      = pnl.max()
    max_loss     = pnl.min()

    cumulative   = pnl.cumsum()
    roll_max     = cumulative.cummax()
    max_dd       = (cumulative - roll_max).min()

    daily_pnl    = pd.Series(dtype=float)
    if "_dt" in trades.columns:
        daily_pnl = trades.groupby(trades["_dt"].dt.date)["net_pnl"].sum()

    sharpe = 0.0
    if len(daily_pnl) > 1 and daily_pnl.std() > 0:
        sharpe = daily_pnl.mean() / daily_pnl.std() * np.sqrt(252)

    print(f"\n{'='*60}")
    print(f" P&L REPORT — {period.upper()} — {datetime.now():%d %b %Y %H:%M}")
    print(f"{'='*60}")
    print(f"  Total trades:    {n}")
    print(f"  Win rate:        {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Total net P&L:   ₹{total_pnl:,.0f}")
    print(f"  Profit factor:   {profit_factor:.2f}")
    print(f"  Expectancy:      ₹{expectancy:.0f} per trade")
    print(f"  Avg win:         ₹{avg_win:,.0f}")
    print(f"  Avg loss:        ₹{avg_loss:,.0f}")
    print(f"  Max win:         ₹{max_win:,.0f}")
    print(f"  Max loss:        ₹{max_loss:,.0f}")
    print(f"  Max drawdown:    ₹{max_dd:,.0f}")
    print(f"  Sharpe ratio:    {sharpe:.2f}")

    if "strategy" in trades.columns:
        print(f"\n  By strategy:")
        by_strat = trades.groupby("strategy")["net_pnl"].agg(
            trades="count", total="sum", win_rate=lambda x: (x > 0).mean() * 100
        )
        for name, row in by_strat.iterrows():
            print(f"    {name:35s} trades={int(row['trades'])} wr={row['win_rate']:.0f}% pnl=₹{row['total']:,.0f}")

    if len(daily_pnl) > 0:
        print(f"\n  Daily P&L:")
        for day, dpnl in daily_pnl.items():
            bar = "█" * min(20, int(abs(dpnl) / 500)) if dpnl != 0 else ""
            sign = "+" if dpnl >= 0 else ""
            print(f"    {day}  {sign}₹{dpnl:,.0f}  {bar}")

    print(f"\n{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="P&L Report")
    parser.add_argument("--period", default="today",
                        choices=["today", "week", "month", "all"])
    args = parser.parse_args()
    setup_logging("reporting")
    trades = load_trades(args.period)
    print_report(trades, args.period)


if __name__ == "__main__":
    main()
