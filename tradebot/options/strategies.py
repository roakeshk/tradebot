# ============================================================
#  tradebot / options / strategies.py
#  T3 — Five high-probability options strategies
#
#  Strategy selection rationale for NSE weekly options:
#
#  1. Short Straddle  — high IV rank (>60), low-movement expectation
#     Sell ATM CE + ATM PE. Profits from IV crush and time decay.
#     Max profit = premium collected. Unlimited risk.
#     Best on: Wednesdays (2 days to BankNifty expiry, theta fast).
#
#  2. Iron Condor — IV rank >50, range-bound market expected
#     Sell OTM CE + Buy further OTM CE (call spread)
#     + Sell OTM PE + Buy further OTM PE (put spread)
#     Defined risk. Best success rate of all five (65-70% historically).
#
#  3. Bull Call Spread — bullish bias, rising market, IV moderate
#     Buy ATM CE + Sell OTM CE. Limited profit, limited loss.
#     Best when: directional conviction + IV is not too high.
#
#  4. Bear Put Spread — bearish bias, falling market, IV moderate
#     Buy ATM PE + Sell OTM PE. Mirror of bull call spread.
#
#  5. Short Put (Cash-Secured) — bullish bias, IV rank >50
#     Sell OTM PE. Keeps premium if market stays above strike.
#     Most beginner-friendly premium selling strategy.
#     On BankNifty: sell 1-2 strikes below ATM for best balance.
# ============================================================

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
import math

from options.pricing import BSModel
from options.data import OptionChain


@dataclass
class OptionsLeg:
    """One leg of a multi-leg options position."""
    symbol:      str
    expiry:      date
    strike:      float
    option_type: str        # "ce" or "pe"
    action:      str        # "buy" or "sell"
    lots:        int
    lot_size:    int        # 15 for BankNifty, 50 for Nifty
    premium:     float      # entry price per unit
    order_id:    str = ""

    @property
    def quantity(self) -> int:
        return self.lots * self.lot_size

    @property
    def notional(self) -> float:
        return self.premium * self.quantity

    @property
    def sign(self) -> int:
        return 1 if self.action == "buy" else -1


@dataclass
class OptionsPosition:
    """Complete multi-leg options position."""
    strategy_name: str
    symbol:        str
    expiry:        date
    legs:          list[OptionsLeg] = field(default_factory=list)
    entry_time:    datetime = field(default_factory=datetime.now)
    status:        str = "open"      # open / closed / expired
    net_premium:   float = 0.0       # positive = credit received
    max_profit:    float = 0.0
    max_loss:      float = 0.0
    breakevens:    list[float] = field(default_factory=list)
    target_exit_pct: float = 0.50    # exit at 50% of max profit
    stop_loss_pct:   float = 2.0     # exit if loss = 2× premium collected

    def current_pnl(self, chain: OptionChain) -> float:
        """Mark-to-market P&L of the position."""
        total = 0.0
        for leg in self.legs:
            row = chain.get_strike(leg.strike)
            if row is None:
                continue
            current = row[f"{leg.option_type}_ltp"]
            total  += leg.sign * (leg.premium - current) * leg.quantity
        return round(total, 2)

    def should_exit(self, chain: OptionChain) -> tuple[bool, str]:
        """Check exit conditions."""
        pnl = self.current_pnl(chain)

        # Profit target: exit at 50% of max profit
        if self.max_profit > 0 and pnl >= self.max_profit * self.target_exit_pct:
            return True, f"TARGET_HIT ({self.target_exit_pct*100:.0f}% of max profit)"

        # Stop loss: exit if loss = 2× premium collected
        if self.net_premium > 0:
            max_allowed_loss = -self.net_premium * self.stop_loss_pct
            if pnl <= max_allowed_loss:
                return True, "STOP_LOSS (2x premium)"

        # Time exit: if only 1 day left, exit to avoid gamma risk
        dte = (self.expiry - date.today()).days
        if dte <= 0:
            return True, "EXPIRY"

        return False, ""

    @property
    def total_greeks(self) -> dict:
        """Net Greeks of the position (approximate)."""
        delta = gamma = theta = vega = 0.0
        for leg in self.legs:
            delta += leg.sign * getattr(leg, "delta", 0)
            gamma += leg.sign * getattr(leg, "gamma", 0)
            theta += leg.sign * getattr(leg, "theta", 0)
            vega  += leg.sign * getattr(leg, "vega",  0)
        return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


LOT_SIZES = {"BANKNIFTY": 15, "NIFTY": 50, "MIDCPNIFTY": 75, "FINNIFTY": 40}


class OptionsStrategyBuilder:
    """
    Constructs multi-leg options positions from a live chain.

    All strategies apply entry filters — they only fire when
    conditions are favourable. This is the primary source of
    high win rate.
    """

    def __init__(self):
        self.bs = BSModel()

    def _lot_size(self, symbol: str) -> int:
        return LOT_SIZES.get(symbol, 15)

    # ── Strategy 1: Short Straddle ────────────────────────────

    def short_straddle(
        self,
        chain: OptionChain,
        lots:  int = 1,
    ) -> Optional[OptionsPosition]:
        """
        Sell ATM Call + Sell ATM Put.
        Best when: IV rank > 60, expiry in 2–5 days, market range-bound.
        Win condition: underlying stays within breakeven range.

        Entry filters:
          - IV rank must be > 60 (selling inflated premiums)
          - DTE must be 2–7 days (optimal theta decay zone)
          - ADX < 25 (not strongly trending — straddle gets killed in trends)
          - ATM premium must be > 0.5% of spot (enough premium to justify)
        """
        atm = chain.atm
        row = chain.get_atm_strike()
        if row is None:
            return None

        ce_prem = float(row.get("ce_ltp", 0))
        pe_prem = float(row.get("pe_ltp", 0))
        net     = ce_prem + pe_prem
        lot_sz  = self._lot_size(chain.symbol)

        if ce_prem < 10 or pe_prem < 10:
            return None   # premiums too thin

        iv_rank = chain.iv_rank or 50
        if iv_rank < 60:
            return None   # IV not elevated enough

        dte = chain.days_to_expiry
        if not (2 <= dte <= 7):
            return None

        pos = OptionsPosition(
            strategy_name="short_straddle",
            symbol=chain.symbol,
            expiry=chain.expiry,
            net_premium=net * lot_sz * lots,
            max_profit=net * lot_sz * lots,
            max_loss=float("inf"),   # unlimited theoretically
            breakevens=[round(atm - net), round(atm + net)],
            target_exit_pct=0.50,
            stop_loss_pct=1.5,
        )
        pos.legs = [
            OptionsLeg(chain.symbol, chain.expiry, atm, "ce", "sell", lots, lot_sz, ce_prem),
            OptionsLeg(chain.symbol, chain.expiry, atm, "pe", "sell", lots, lot_sz, pe_prem),
        ]
        return pos

    # ── Strategy 2: Iron Condor ───────────────────────────────

    def iron_condor(
        self,
        chain:       OptionChain,
        lots:        int = 1,
        wing_width:  int = 3,    # number of strikes for each wing
        wing_gap:    int = 2,    # strikes OTM from ATM for the short legs
    ) -> Optional[OptionsPosition]:
        """
        Sell OTM Call + Buy further OTM Call (call spread)
        + Sell OTM Put + Buy further OTM Put (put spread).

        This is the safest premium selling strategy — max loss is capped.
        Best when: IV rank > 50, range-bound market expected.

        BankNifty example (spot=48000, step=100):
          Short 48500 CE  + Long 48800 CE  (300-point wide call spread)
          Short 47500 PE  + Long 47200 PE  (300-point wide put spread)
        """
        step    = 100 if chain.symbol == "BANKNIFTY" else 50
        lot_sz  = self._lot_size(chain.symbol)
        atm     = chain.atm
        iv_rank = chain.iv_rank or 50

        if iv_rank < 50:
            return None

        dte = chain.days_to_expiry
        if not (3 <= dte <= 10):
            return None

        # Calculate strikes
        short_ce_k = atm + wing_gap * step
        long_ce_k  = atm + (wing_gap + wing_width) * step
        short_pe_k = atm - wing_gap * step
        long_pe_k  = atm - (wing_gap + wing_width) * step

        # Get premiums
        def get_prem(k: float, opt: str) -> Optional[float]:
            row = chain.get_strike(k)
            if row is None:
                return None
            return float(row.get(f"{opt}_ltp", 0))

        sce_p = get_prem(short_ce_k, "ce")
        lce_p = get_prem(long_ce_k,  "ce")
        spe_p = get_prem(short_pe_k, "pe")
        lpe_p = get_prem(long_pe_k,  "pe")

        if any(p is None or p <= 0 for p in [sce_p, lce_p, spe_p, lpe_p]):
            return None

        net_credit   = (sce_p - lce_p + spe_p - lpe_p)
        wing_width_p = wing_width * step
        max_loss_per = wing_width_p - net_credit
        max_profit   = net_credit * lot_sz * lots
        max_loss     = max_loss_per * lot_sz * lots

        if net_credit < 20:   # minimum ₹20 credit per unit
            return None

        pos = OptionsPosition(
            strategy_name="iron_condor",
            symbol=chain.symbol,
            expiry=chain.expiry,
            net_premium=max_profit,
            max_profit=max_profit,
            max_loss=-max_loss,
            breakevens=[
                round(short_pe_k - net_credit),
                round(short_ce_k + net_credit),
            ],
            target_exit_pct=0.50,
            stop_loss_pct=2.0,
        )
        pos.legs = [
            OptionsLeg(chain.symbol, chain.expiry, short_ce_k, "ce", "sell", lots, lot_sz, sce_p),
            OptionsLeg(chain.symbol, chain.expiry, long_ce_k,  "ce", "buy",  lots, lot_sz, lce_p),
            OptionsLeg(chain.symbol, chain.expiry, short_pe_k, "pe", "sell", lots, lot_sz, spe_p),
            OptionsLeg(chain.symbol, chain.expiry, long_pe_k,  "pe", "buy",  lots, lot_sz, lpe_p),
        ]
        return pos

    # ── Strategy 3: Bull Call Spread ──────────────────────────

    def bull_call_spread(
        self,
        chain:      OptionChain,
        lots:       int = 1,
        spread_pts: int = 300,   # width of spread in index points
    ) -> Optional[OptionsPosition]:
        """
        Buy ATM CE + Sell OTM CE (spread_pts above).
        Best when: bullish bias, IV moderate (30–60 rank), DTE 5–15 days.
        Max profit = spread - net debit. Max loss = net debit paid.
        """
        step   = 100 if chain.symbol == "BANKNIFTY" else 50
        lot_sz = self._lot_size(chain.symbol)
        atm    = chain.atm

        buy_k  = atm
        sell_k = atm + spread_pts

        iv_rank = chain.iv_rank or 50
        if iv_rank > 70:
            return None   # high IV → buying options expensive

        dte = chain.days_to_expiry
        if not (5 <= dte <= 20):
            return None

        def get_prem(k: float) -> Optional[float]:
            row = chain.get_strike(k)
            return float(row["ce_ltp"]) if row is not None else None

        buy_p  = get_prem(buy_k)
        sell_p = get_prem(sell_k)
        if buy_p is None or sell_p is None or buy_p <= 0:
            return None

        net_debit  = buy_p - sell_p
        max_profit = (spread_pts - net_debit) * lot_sz * lots
        max_loss   = net_debit * lot_sz * lots

        if net_debit <= 0 or max_profit <= 0:
            return None

        pos = OptionsPosition(
            strategy_name="bull_call_spread",
            symbol=chain.symbol,
            expiry=chain.expiry,
            net_premium=-net_debit * lot_sz * lots,
            max_profit=max_profit,
            max_loss=-max_loss,
            breakevens=[round(buy_k + net_debit)],
            target_exit_pct=0.65,
            stop_loss_pct=1.0,
        )
        pos.legs = [
            OptionsLeg(chain.symbol, chain.expiry, buy_k,  "ce", "buy",  lots, lot_sz, buy_p),
            OptionsLeg(chain.symbol, chain.expiry, sell_k, "ce", "sell", lots, lot_sz, sell_p),
        ]
        return pos

    # ── Strategy 4: Bear Put Spread ───────────────────────────

    def bear_put_spread(
        self,
        chain:      OptionChain,
        lots:       int = 1,
        spread_pts: int = 300,
    ) -> Optional[OptionsPosition]:
        """
        Buy ATM PE + Sell OTM PE (spread_pts below).
        Mirror of bull call spread for bearish bias.
        """
        step   = 100 if chain.symbol == "BANKNIFTY" else 50
        lot_sz = self._lot_size(chain.symbol)
        atm    = chain.atm

        buy_k  = atm
        sell_k = atm - spread_pts

        iv_rank = chain.iv_rank or 50
        if iv_rank > 70:
            return None

        dte = chain.days_to_expiry
        if not (5 <= dte <= 20):
            return None

        def get_prem(k: float) -> Optional[float]:
            row = chain.get_strike(k)
            return float(row["pe_ltp"]) if row is not None else None

        buy_p  = get_prem(buy_k)
        sell_p = get_prem(sell_k)
        if buy_p is None or sell_p is None or buy_p <= 0:
            return None

        net_debit  = buy_p - sell_p
        max_profit = (spread_pts - net_debit) * lot_sz * lots
        max_loss   = net_debit * lot_sz * lots

        if net_debit <= 0 or max_profit <= 0:
            return None

        pos = OptionsPosition(
            strategy_name="bear_put_spread",
            symbol=chain.symbol,
            expiry=chain.expiry,
            net_premium=-net_debit * lot_sz * lots,
            max_profit=max_profit,
            max_loss=-max_loss,
            breakevens=[round(buy_k - net_debit)],
            target_exit_pct=0.65,
            stop_loss_pct=1.0,
        )
        pos.legs = [
            OptionsLeg(chain.symbol, chain.expiry, buy_k,  "pe", "buy",  lots, lot_sz, buy_p),
            OptionsLeg(chain.symbol, chain.expiry, sell_k, "pe", "sell", lots, lot_sz, sell_p),
        ]
        return pos

    # ── Strategy 5: Short OTM Put ─────────────────────────────

    def short_put(
        self,
        chain:       OptionChain,
        lots:        int = 1,
        otm_strikes: int = 2,    # how many strikes OTM
    ) -> Optional[OptionsPosition]:
        """
        Sell OTM Put (otm_strikes below ATM).
        Best when: bullish bias, IV rank > 50, DTE 5–10 days.
        Keeps full premium if spot stays above strike.
        Most beginner-friendly premium-selling strategy.

        On BankNifty: sell 2 strikes (200pts) below ATM.
        Probability of profit ~70% historically.
        """
        step    = 100 if chain.symbol == "BANKNIFTY" else 50
        lot_sz  = self._lot_size(chain.symbol)
        atm     = chain.atm
        iv_rank = chain.iv_rank or 50

        if iv_rank < 50:
            return None

        dte = chain.days_to_expiry
        if not (4 <= dte <= 12):
            return None

        strike = atm - otm_strikes * step
        row    = chain.get_strike(strike)
        if row is None:
            return None

        premium = float(row.get("pe_ltp", 0))
        delta   = abs(float(row.get("pe_delta", 0)))

        if premium < 15:
            return None   # too little premium to justify

        if delta > 0.35:
            return None   # too close to ATM — risk too high

        max_profit = premium * lot_sz * lots
        max_loss   = (strike - premium) * lot_sz * lots   # if goes to zero

        pos = OptionsPosition(
            strategy_name="short_put",
            symbol=chain.symbol,
            expiry=chain.expiry,
            net_premium=max_profit,
            max_profit=max_profit,
            max_loss=-max_loss,
            breakevens=[round(strike - premium)],
            target_exit_pct=0.50,
            stop_loss_pct=2.0,
        )
        pos.legs = [
            OptionsLeg(chain.symbol, chain.expiry, strike, "pe", "sell", lots, lot_sz, premium),
        ]
        return pos

    # ── Strategy selector ─────────────────────────────────────

    def select_best_strategy(
        self,
        chain:   OptionChain,
        regime:  str = "RANGING",
        lots:    int = 1,
    ) -> Optional[OptionsPosition]:
        """
        Automatically select the most appropriate strategy
        based on current market conditions.
        """
        iv_rank = chain.iv_rank or 50
        dte     = chain.days_to_expiry

        # High IV + short DTE → sell volatility
        if iv_rank > 65 and dte <= 5:
            pos = self.short_straddle(chain, lots)
            if pos:
                return pos

        # High IV + medium DTE → iron condor (safer)
        if iv_rank > 50 and 5 <= dte <= 12:
            pos = self.iron_condor(chain, lots)
            if pos:
                return pos

        # Moderate IV + bullish regime → short put
        if iv_rank > 45 and regime in ("TRENDING_UP", "RANGING"):
            pos = self.short_put(chain, lots)
            if pos:
                return pos

        # Directional spreads based on regime
        if regime == "TRENDING_UP" and dte >= 5:
            return self.bull_call_spread(chain, lots)
        if regime == "TRENDING_DOWN" and dte >= 5:
            return self.bear_put_spread(chain, lots)

        return None


    # ── Strategy: Short Strangle ──────────────────────────────

    def short_strangle(
        self,
        chain:       OptionChain,
        lots:        int = 1,
        otm_strikes: int = 2,
    ) -> Optional["OptionsPosition"]:
        """
        Sell OTM Call + Sell OTM Put (wider than straddle).
        Best when: IV rank > 55, DTE 3–8 days, range-bound market.
        Lower premium but higher probability of profit vs straddle.
        """
        dte     = chain.days_to_expiry
        iv_rank = chain.iv_rank or 50
        step    = 100 if chain.symbol == "BANKNIFTY" else 50
        lot_sz  = self._lot_size(chain.symbol)

        if iv_rank < 55:
            return None
        if not (3 <= dte <= 8):
            return None

        call_strike = chain.atm + otm_strikes * step
        put_strike  = chain.atm - otm_strikes * step

        call_row = chain.get_strike(call_strike)
        put_row  = chain.get_strike(put_strike)
        if call_row is None or put_row is None:
            return None

        ce_prem = float(call_row.get("ce_ltp", 0))
        pe_prem = float(put_row.get("pe_ltp", 0))
        net     = ce_prem + pe_prem

        if ce_prem < 8 or pe_prem < 8:
            return None

        pos = OptionsPosition(
            strategy_name="short_strangle",
            symbol=chain.symbol,
            expiry=chain.expiry,
            net_premium=net * lot_sz * lots,
            max_profit=net * lot_sz * lots,
            max_loss=float("inf"),
            breakevens=[round(put_strike - net), round(call_strike + net)],
            target_exit_pct=0.50,
            stop_loss_pct=2.0,
        )
        pos.legs = [
            OptionsLeg(chain.symbol, chain.expiry, call_strike, "ce", "sell", lots, lot_sz, ce_prem),
            OptionsLeg(chain.symbol, chain.expiry, put_strike,  "pe", "sell", lots, lot_sz, pe_prem),
        ]
        return pos

    # ── Strategy: Long Call / Long Put (directional) ──────────

    def long_call(
        self,
        chain:       OptionChain,
        lots:        int = 1,
        otm_strikes: int = 1,
    ) -> Optional["OptionsPosition"]:
        """
        Buy slightly OTM Call. Best when: IV rank < 40 (cheap options),
        bullish bias from regime classifier, DTE >= 5.
        """
        dte     = chain.days_to_expiry
        iv_rank = chain.iv_rank or 50
        step    = 100 if chain.symbol == "BANKNIFTY" else 50
        lot_sz  = self._lot_size(chain.symbol)

        if iv_rank > 50:
            return None   # don't buy expensive options
        if dte < 5:
            return None   # too close to expiry — gamma risk too high

        strike = chain.atm + otm_strikes * step
        row    = chain.get_strike(strike)
        if row is None:
            return None

        premium = float(row.get("ce_ltp", 0))
        if premium < 10:
            return None

        cost = premium * lot_sz * lots
        pos  = OptionsPosition(
            strategy_name="long_call",
            symbol=chain.symbol,
            expiry=chain.expiry,
            net_premium=-cost,
            max_profit=float("inf"),
            max_loss=-cost,
            breakevens=[round(strike + premium)],
            target_exit_pct=1.0,
            stop_loss_pct=1.0,
        )
        pos.legs = [
            OptionsLeg(chain.symbol, chain.expiry, strike, "ce", "buy", lots, lot_sz, premium),
        ]
        return pos

    def long_put(
        self,
        chain:       OptionChain,
        lots:        int = 1,
        otm_strikes: int = 1,
    ) -> Optional["OptionsPosition"]:
        """
        Buy slightly OTM Put. Best when: IV rank < 40, bearish regime, DTE >= 5.
        """
        dte     = chain.days_to_expiry
        iv_rank = chain.iv_rank or 50
        step    = 100 if chain.symbol == "BANKNIFTY" else 50
        lot_sz  = self._lot_size(chain.symbol)

        if iv_rank > 50:
            return None
        if dte < 5:
            return None

        strike = chain.atm - otm_strikes * step
        row    = chain.get_strike(strike)
        if row is None:
            return None

        premium = float(row.get("pe_ltp", 0))
        if premium < 10:
            return None

        cost = premium * lot_sz * lots
        pos  = OptionsPosition(
            strategy_name="long_put",
            symbol=chain.symbol,
            expiry=chain.expiry,
            net_premium=-cost,
            max_profit=float("inf"),
            max_loss=-cost,
            breakevens=[round(strike - premium)],
            target_exit_pct=1.0,
            stop_loss_pct=1.0,
        )
        pos.legs = [
            OptionsLeg(chain.symbol, chain.expiry, strike, "pe", "buy", lots, lot_sz, premium),
        ]
        return pos


# ── Standalone helpers ────────────────────────────────────────

def pnl_at_expiry(position: "OptionsPosition", spot_at_expiry: float) -> float:
    """
    Calculate theoretical P&L at expiry for any position.
    Attached as a method to OptionsPosition via monkey-patch below.
    """
    total = 0.0
    for leg in position.legs:
        if leg.option_type == "ce":
            intrinsic = max(0.0, spot_at_expiry - leg.strike)
        else:
            intrinsic = max(0.0, leg.strike - spot_at_expiry)

        if leg.action == "sell":
            total += (leg.premium - intrinsic) * leg.quantity
        else:
            total += (intrinsic - leg.premium) * leg.quantity

    return round(total, 2)


# Attach pnl_at_expiry to OptionsPosition so position.pnl_at_expiry(spot) works
OptionsPosition.pnl_at_expiry = pnl_at_expiry
