#!/usr/bin/env python3
# ============================================================
#  tradebot / main.py   v2 — futures + options + simulation
#
#  Modes:
#    paper            Paper futures (broker needed for data)
#    live             Live futures (--confirm required)
#    simulate         Futures + options simulation (NO broker!)
#    backtest         Futures walk-forward backtest
#    options-sim      Options simulation on history (NO broker!)
#    options-paper    Options paper with live chain
#    options-backtest Options historical backtest
# ============================================================

import argparse, sys, time
from pathlib   import Path
from datetime  import datetime

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from utils.logger    import setup_logging
from config.settings import ACTIVE_BROKER, MARKET, INSTRUMENTS, RISK


def main():
    # Build valid symbol choices from current market config
    valid_symbols = list(INSTRUMENTS.keys())
    default_symbol = valid_symbols[0] if valid_symbols else "SPY"
    currency = RISK.get("currency", "USD")

    parser = argparse.ArgumentParser(description="TradeBot — Futures + Options")
    parser.add_argument("--mode",    default="paper",
        choices=["paper","live","simulate","backtest",
                 "options-sim","options-paper","options-backtest",
                 "analyze","screen"])
    parser.add_argument("--symbol",  default=default_symbol,
        choices=valid_symbols)
    parser.add_argument("--capital", default=RISK["max_capital"], type=float)
    parser.add_argument("--days",    default=90,     type=int)
    parser.add_argument("--no-ai",   action="store_true")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--speed",   default=0.0,    type=float)
    args = parser.parse_args()

    logger = setup_logging("main")

    cur_sym = "$" if currency == "USD" else "₹"
    print("\n" + "="*60)
    print(f"  TradeBot — {MARKET} Market")
    print("="*60)
    print(f"  Symbol:  {args.symbol}")
    print(f"  Capital: {cur_sym}{args.capital:,.0f}")
    print(f"  Mode:    {args.mode}\n")

    # ── Stock analysis (no broker needed) ----------------------
    if args.mode == "analyze":
        from analysis.stock_analyzer import StockAnalyzer
        print(f"  Analyzing {args.symbol}...\n")
        report = StockAnalyzer(args.symbol).full_report()
        print(report.summary)
        return

    if args.mode == "screen":
        from analysis.stock_analyzer import screen_stocks
        symbols = valid_symbols
        print(f"  Screening {len(symbols)} stocks...\n")
        df = screen_stocks(symbols)
        print(df.to_string(index=False))
        return

    # ── Options simulation (no broker needed) -----------------
    if args.mode == "options-sim":
        print(f"  Simulating {args.days} days on historical data...")
        print("  Chain reconstructed from Black-Scholes — no broker required.\n")
        from simulation.options_backtest import OptionsBacktestRunner
        OptionsBacktestRunner(args.symbol, args.capital).run(n_days=args.days, verbose=True)
        return

    # ── Options historical backtest ---------------------------
    if args.mode == "options-backtest":
        from simulation.options_backtest import OptionsBacktestRunner
        OptionsBacktestRunner(args.symbol, args.capital).run(n_days=args.days, verbose=True)
        return

    # ── Full simulation (futures + options) -------------------
    if args.mode == "simulate":
        _run_full_simulation(args)
        return

    # ── Futures backtest -------------------------------------
    if args.mode == "backtest":
        sys.argv = ["run_backtest.py", "--symbol", args.symbol]
        from run_backtest import main as run_bt
        run_bt()
        return

    # ── Live safety check ------------------------------------
    if args.mode == "live":
        if not args.confirm:
            print("LIVE MODE requires --confirm. Re-run with --confirm.")
            sys.exit(1)
        if ACTIVE_BROKER == "paper":
            print("Set ACTIVE_BROKER in settings.py first.")
            sys.exit(1)
        print(f"LIVE TRADING — starting in 5s... Ctrl+C to abort")
        time.sleep(5)

    # ── Paper / live futures engine --------------------------
    if args.mode in ("paper", "live"):
        from execution.engine import ExecutionEngine
        ExecutionEngine(
            symbol      = args.symbol,
            capital     = args.capital,
            use_ai      = not args.no_ai,
            broker_name = "paper" if args.mode == "paper" else None,
        ).start()
        return

    # ── Options paper live -----------------------------------
    if args.mode == "options-paper":
        _run_options_paper_live(args)


def _run_full_simulation(args):
    from simulation.simulator     import MarketSimulator, SimMode
    from simulation.options_paper import OptionsPaperRunner
    from broker.paper_broker      import PaperBroker
    from strategy.strategies      import build_strategies
    from strategy.regime          import RegimeClassifier
    from risk.manager             import RiskManager
    from strategy.base_strategy   import Direction
    from broker.base              import Order, OrderSide, OrderType

    print("Full simulation: futures + options on historical data")
    print("No broker needed.\n")

    inst     = INSTRUMENTS.get(args.symbol, {})
    lot_size = inst.get("lot_size", 1)

    paper      = PaperBroker(args.capital)
    strategies = build_strategies(args.symbol)
    regime_clf = RegimeClassifier()
    risk_mgr   = RiskManager(args.capital)
    opt_runner = OptionsPaperRunner(args.symbol, args.capital)
    sim        = MarketSimulator(args.symbol, mode=SimMode.BACKTEST, speed=args.speed)

    def on_bar(sim_bar, price_df):
        paper.update_price(args.symbol, sim_bar.close)
        if len(price_df) < 50:
            return
        try:
            regime = regime_clf.classify(price_df.tail(60))
        except Exception:
            regime = None
        for strat in strategies:
            try:
                for sig in strat.generate_signals(price_df, regime):
                    res = risk_mgr.approve_signal(sig)
                    if not res.approved:
                        continue
                    paper.place_order(Order(
                        symbol=args.symbol,
                        side=OrderSide.BUY if sig.direction == Direction.LONG else OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        quantity=res.lots * lot_size,
                        tag=strat.name[:20],
                    ))
                    risk_mgr.record_open()
                    break
            except Exception:
                pass

    sim.on_bar(on_bar)
    sim.on_chain(opt_runner.on_chain_update)
    result = sim.run(n_days=args.days)

    # Report
    opt_runner.print_summary()
    fut_trades = paper.get_trade_log()
    cur_sym = "$" if MARKET == "US" else "₹"
    if not fut_trades.empty and "net_pnl" in fut_trades.columns:
        print(f"\n Futures: {len(fut_trades)} trades  PnL={cur_sym}{fut_trades['net_pnl'].sum():,.0f}")
    print(f" Simulation: {result.total_bars:,} bars | {args.days} days")


def _run_options_paper_live(args):
    from simulation.options_paper import OptionsPaperRunner
    from options.chain_feed       import ChainFeed
    from broker.factory           import get_data_source

    broker = get_data_source()
    feed   = ChainFeed(broker=broker, refresh_secs=60)
    runner = OptionsPaperRunner(args.symbol, args.capital)
    feed.subscribe(args.symbol, runner.on_live_chain)
    feed.start()
    print("Options paper started. Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(60)
            s = runner._risk.status()
            cur = "$" if MARKET == "US" else "₹"
            print(f"  [{datetime.now().strftime('%H:%M')}] "
                  f"Capital={cur}{s['capital']:,.0f}  "
                  f"DailyPnL={cur}{s['daily_pnl']:,.0f}  "
                  f"Trades={s['trades_today']}")
    except KeyboardInterrupt:
        feed.stop()
        runner.print_summary()
        runner.save_trades(args.symbol)


if __name__ == "__main__":
    main()
