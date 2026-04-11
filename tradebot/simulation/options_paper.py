# ============================================================
#  tradebot / simulation / options_paper.py
#  B2 — Options paper trading runner
#
#  Works in two modes:
#    1. SIMULATION: hooks into MarketSimulator callbacks
#       → runs on historical data, no broker needed at all
#    2. PAPER LIVE: hooks into live chain feed
#       → real market data, paper fills
#
#  Tracks every position open/close with full Greeks,
#  P&L breakdown, and strategy attribution.
# ============================================================

import logging
from dataclasses import dataclass, field
from datetime    import date, datetime, timedelta
from pathlib     import Path
from typing      import Optional
import pandas    as pd
import numpy     as np

from options.chain_feed  import ChainFeed
from options.strategies  import OptionsStrategyBuilder, OptionsPosition
from options.signals     import OptionsSignalEngine
from options.risk        import OptionsRiskManager
from options.pricing     import BSModel
from options.data        import ExpiryManager, OptionChain
from risk.cost_model     import CostModel
from config.settings     import PROC_DIR, INSTRUMENTS, SESSION, MARKET, COST_MODEL
from utils.railway_push  import get_pusher

def _parse_time(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m

_CURRENCY = "$" if MARKET == "US" else "Rs."

logger = logging.getLogger(__name__)


@dataclass
class PaperTrade:
    trade_id:       int
    strategy:       str
    symbol:         str
    legs_desc:      str         # e.g. "Sell CE 48500 @ 420, Sell PE 48500 @ 380"
    entry_time:     datetime
    entry_spot:     float
    entry_premium:  float       # net credit (+) or debit (-)
    max_profit:     float
    max_loss:       float
    breakevens:     list
    lots:           int
    exit_time:      Optional[datetime] = None
    exit_spot:      float = 0.0
    exit_reason:    str   = ""
    gross_pnl:      float = 0.0
    cost:           float = 0.0
    net_pnl:        float = 0.0
    dte_at_entry:   int   = 0
    iv_rank_entry:  float = 0.0
    regime:         str   = ""
    peak_pnl:       float = 0.0
    max_adverse:    float = 0.0  # max adverse excursion


class OptionsPaperRunner:
    """
    Runs options strategies in paper mode.
    Can be driven by simulator (historical replay) or live chain feed.

    Usage — simulation:
        from simulation.simulator import MarketSimulator, SimMode
        sim = MarketSimulator("BANKNIFTY", mode=SimMode.PAPER_SIM)
        runner = OptionsPaperRunner(capital=100000)
        sim.on_chain(runner.on_chain_update)
        result = sim.run(n_days=90)
        runner.print_summary()

    Usage — live paper:
        feed = ChainFeed(broker=angel_broker)
        runner = OptionsPaperRunner(capital=100000)
        feed.subscribe("BANKNIFTY", runner.on_live_chain)
        feed.start()
    """

    def __init__(
        self,
        symbol:      str   = None,
        capital:     float = 100000.0,
        broker_name: str   = None,
    ):
        self.symbol      = symbol or (list(INSTRUMENTS.keys())[0] if INSTRUMENTS else "SPY")
        self.capital     = capital
        self.initial_cap = capital

        _broker = broker_name or ("us_paper" if MARKET == "US" else "zerodha")
        self._builder    = OptionsStrategyBuilder()
        self._risk       = OptionsRiskManager(capital)
        self._cost       = CostModel(_broker)
        self._sig_engine = OptionsSignalEngine()

        self._open: list[tuple[PaperTrade, OptionsPosition]] = []
        self._closed: list[PaperTrade]  = []
        self._trade_id  = 0
        self._pusher    = get_pusher()
        self._daily_pnl = 0.0
        self._today: Optional[date] = None

    # ── Main callbacks ──────────────────────────────────────────

    def on_chain_update(self, sim_bar, chain: OptionChain, price_df: pd.DataFrame = None) -> None:
        """
        Called by simulator on each bar.
        sim_bar: SimBar object with timestamp, OHLCV, iv_estimate
        chain:   reconstructed OptionChain for this bar
        """
        ts   = sim_bar.timestamp if hasattr(sim_bar, "timestamp") else datetime.now()
        spot = sim_bar.close     if hasattr(sim_bar, "close")     else chain.spot

        self._daily_reset(ts)
        self._manage_open_positions(chain, spot, ts)

        # Session time filter (config-driven)
        h, m = ts.hour, ts.minute
        mins = h * 60 + m
        mkt_open  = _parse_time(SESSION["market_open"])
        no_trade  = _parse_time(SESSION["no_trade_after"])
        if mins < mkt_open or mins > no_trade:
            return

        # Don't add new positions if at max
        if len(self._open) >= self._risk.max_open_positions:
            return

        # Daily loss check
        max_loss = self.initial_cap * 0.03
        if self._daily_pnl <= -max_loss:
            return

        # Generate signal
        price_df_arg = price_df if price_df is not None else pd.DataFrame()
        signal = self._sig_engine.generate(chain, price_df_arg)
        if signal is None:
            return

        # Risk approval
        approval = self._risk.approve_position(signal.position, chain)
        if not approval.approved:
            logger.debug(f"  [OPTIONS PAPER] Blocked: {approval.reason}")
            return

        # Enter position
        self._enter(signal.position, chain, ts, signal)

    def on_live_chain(self, chain: OptionChain) -> None:
        """Called by live ChainFeed every 60s during market hours."""
        from simulation.simulator import SimBar
        sb = SimBar(
            timestamp=datetime.now(), symbol=chain.symbol,
            open=chain.spot, high=chain.spot, low=chain.spot, close=chain.spot,
            volume=0, bar_index=0, total_bars=0,
            session_pct=0.5, iv_estimate=chain.iv_rank or 0.18,
        )
        self.on_chain_update(sb, chain, pd.DataFrame())

    # ── Position management ─────────────────────────────────────

    def _enter(
        self,
        position: OptionsPosition,
        chain:    OptionChain,
        ts:       datetime,
        signal,
    ) -> None:
        self._trade_id += 1
        legs_desc = " | ".join(
            f"{l.action.upper()} {l.option_type.upper()} {l.strike:.0f}@{l.premium:.1f}"
            for l in position.legs
        )
        dte = (position.expiry - ts.date()).days

        trade = PaperTrade(
            trade_id      = self._trade_id,
            strategy      = position.strategy_name,
            symbol        = self.symbol,
            legs_desc     = legs_desc,
            entry_time    = ts,
            entry_spot    = chain.spot,
            entry_premium = position.net_premium,
            max_profit    = position.max_profit,
            max_loss      = position.max_loss if position.max_loss != float("inf") else 0,
            breakevens    = position.breakevens,
            lots          = signal.position.legs[0].lots if signal.position.legs else 1,
            dte_at_entry  = dte,
            iv_rank_entry = chain.iv_rank or 0,
            regime        = signal.regime if hasattr(signal, "regime") else "",
        )
        self._open.append((trade, position))
        self._risk.record_open(position)

        logger.info(
            f"[OPTIONS PAPER] ENTER #{trade.trade_id} {position.strategy_name} "
            f"spot={chain.spot:.0f} prem={_CURRENCY}{position.net_premium:.0f} "
            f"DTE={dte} IV_rank={chain.iv_rank:.0f}"
        )

    def _manage_open_positions(self, chain: OptionChain, spot: float, ts: datetime) -> None:
        still_open = []
        for trade, position in self._open:
            # Update current P&L
            curr_pnl = position.current_pnl(chain)
            trade.peak_pnl    = max(trade.peak_pnl, curr_pnl)
            trade.max_adverse = min(trade.max_adverse, curr_pnl)

            # Check exit conditions
            should_exit, reason = position.should_exit(chain)

            # Also check for EOD (config-driven)
            _nta = _parse_time(SESSION.get("no_trade_after", "15:15"))
            if ts.hour * 60 + ts.minute >= _nta:
                should_exit = True
                reason      = "EOD"

            if should_exit:
                self._exit(trade, position, chain, spot, ts, reason, curr_pnl)
            else:
                still_open.append((trade, position))

        self._open = still_open

    def _exit(
        self,
        trade:    PaperTrade,
        position: OptionsPosition,
        chain:    OptionChain,
        spot:     float,
        ts:       datetime,
        reason:   str,
        curr_pnl: float,
    ) -> None:
        # Estimate transaction cost (one round trip per leg)
        _broker_key = "us_paper" if MARKET == "US" else "zerodha"
        _brokerage = COST_MODEL.get(_broker_key, {}).get("brokerage_per_order", 0.0)
        _stt = COST_MODEL.get(_broker_key, {}).get("stt_pct_sell", 0.0001)
        leg_cost = 0.0
        for leg in position.legs:
            notional = spot * leg.quantity
            leg_cost += _brokerage
            if leg.action == "sell":
                leg_cost += notional * _stt

        trade.exit_time   = ts
        trade.exit_spot   = spot
        trade.exit_reason = reason
        trade.gross_pnl   = round(curr_pnl, 2)
        trade.cost        = round(leg_cost, 2)
        trade.net_pnl     = round(curr_pnl - leg_cost, 2)

        self._daily_pnl += trade.net_pnl
        self.capital    += trade.net_pnl
        self._closed.append(trade)
        self._risk.record_exit(position, trade.net_pnl)
        # Push to Railway dashboard
        try:
            self._pusher.push_options_trade(trade)
        except Exception:
            pass

        emoji = "✓" if trade.net_pnl > 0 else "✗"
        logger.info(
            f"[OPTIONS PAPER] EXIT {emoji} #{trade.trade_id} {trade.strategy} "
            f"reason={reason} gross={_CURRENCY}{curr_pnl:.0f} "
            f"cost={_CURRENCY}{leg_cost:.0f} net={_CURRENCY}{trade.net_pnl:.0f}"
        )

    def _daily_reset(self, ts: datetime) -> None:
        today = ts.date()
        if today != self._today:
            self._today     = today
            self._daily_pnl = 0.0
            self._risk.reset_day()

    # ── Reporting ───────────────────────────────────────────────

    def print_summary(self) -> None:
        trades = self._closed
        if not trades:
            print("No closed options trades yet.")
            return

        pnls  = [t.net_pnl for t in trades]
        wins  = [p for p in pnls if p > 0]
        loss  = [p for p in pnls if p < 0]
        n     = len(pnls)

        print(f"\n{'='*55}")
        print(f" OPTIONS PAPER TRADING SUMMARY — {self.symbol}")
        print(f"{'='*55}")
        print(f" Total trades:    {n}")
        print(f" Win rate:        {len(wins)/n*100:.1f}%")
        print(f" Total net P&L:   {_CURRENCY}{sum(pnls):,.0f}")
        print(f" Avg win:         {_CURRENCY}{(sum(wins)/len(wins) if wins else 0):,.0f}")
        print(f" Avg loss:        {_CURRENCY}{(sum(loss)/len(loss) if loss else 0):,.0f}")
        if loss:
            print(f" Profit factor:   {sum(wins)/abs(sum(loss)):.2f}")
        print(f" Capital:         {_CURRENCY}{self.capital:,.0f} (start: {_CURRENCY}{self.initial_cap:,.0f})")
        print(f"\n Strategy breakdown:")
        by_strat: dict[str, list] = {}
        for t in trades:
            by_strat.setdefault(t.strategy, []).append(t.net_pnl)
        for strat, spnls in sorted(by_strat.items()):
            wr = sum(1 for p in spnls if p > 0) / len(spnls) * 100
            print(f"   {strat:22s}: {len(spnls):3d} trades  WR={wr:.0f}%  PnL={_CURRENCY}{sum(spnls):,.0f}")

    def get_trades_df(self) -> pd.DataFrame:
        if not self._closed:
            return pd.DataFrame()
        return pd.DataFrame([vars(t) for t in self._closed])

    def save_trades(self, symbol: str = None) -> None:
        symbol = symbol or self.symbol
        df = self.get_trades_df()
        if df.empty:
            return
        PROC_DIR.mkdir(parents=True, exist_ok=True)
        path = PROC_DIR / f"options_paper_{symbol}_{datetime.now():%Y%m%d_%H%M}.csv"
        df.to_csv(path, index=False)
        logger.info(f"Options paper trades saved: {path}")
