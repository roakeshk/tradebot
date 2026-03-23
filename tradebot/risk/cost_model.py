# ============================================================
#  tradebot / risk / cost_model.py
#  Standalone cost calculator.
#  Used by: backtester, paper broker, live execution, reporting.
#
#  WHY THIS MATTERS:
#  On BankNifty futures at ₹48,000 with 1 lot (15 units):
#    Notional per trade = ₹48,000 × 15 = ₹7,20,000
#    Round-trip cost (buy + sell) ≈ ₹160–200
#    If your edge is ₹100 per trade, you're LOSING money.
#  Every backtest must go through this model. No exceptions.
# ============================================================

from dataclasses import dataclass
from config.settings import COST_MODEL, INSTRUMENTS


@dataclass
class CostBreakdown:
    brokerage:       float   # flat fee per order
    stt:             float   # securities transaction tax
    exchange_charge: float   # NSE/MCX transaction charge
    sebi_charge:     float   # SEBI turnover fee
    stamp_duty:      float   # state stamp duty
    gst:             float   # 18% GST on brokerage + exchange charge
    slippage:        float   # estimated adverse fill cost
    total:           float   # sum of all above

    def __str__(self) -> str:
        return (
            f"Brokerage:    ₹{self.brokerage:>8.2f}\n"
            f"STT:          ₹{self.stt:>8.4f}\n"
            f"Exchange:     ₹{self.exchange_charge:>8.4f}\n"
            f"SEBI:         ₹{self.sebi_charge:>8.6f}\n"
            f"Stamp duty:   ₹{self.stamp_duty:>8.4f}\n"
            f"GST:          ₹{self.gst:>8.4f}\n"
            f"Slippage:     ₹{self.slippage:>8.2f}\n"
            f"{'─'*28}\n"
            f"TOTAL:        ₹{self.total:>8.2f}"
        )


class CostModel:
    """
    Calculates true round-trip cost for a futures trade.

    Usage:
        cm = CostModel(broker="zerodha")
        cost = cm.round_trip("BANKNIFTY", lots=1, entry_price=48000, exit_price=48150)
        print(cost)
    """

    def __init__(self, broker: str = "zerodha"):
        assert broker in ("zerodha", "shoonya"), f"Unknown broker: {broker}"
        self.broker = broker
        self.c      = COST_MODEL[broker]
        self.s_ticks = COST_MODEL["slippage_ticks"]

    def _single_leg(
        self,
        notional:  float,
        is_buy:    bool,
        tick_size: float,
        quantity:  int,       # actual units (lots × lot_size)
    ) -> CostBreakdown:
        c = self.c
        brokerage       = c["brokerage_per_order"]
        stt             = notional * c["stt_pct_sell"] if not is_buy else 0.0
        exchange_charge = notional * c["exchange_txn_charge_pct"]
        sebi_charge     = notional * c["sebi_charges_pct"]
        stamp_duty      = notional * c["stamp_duty_pct_buy"] if is_buy else 0.0
        gst             = (brokerage + exchange_charge) * c["gst_pct"]
        slippage        = self.s_ticks * tick_size * quantity   # adverse ticks × units

        total = brokerage + stt + exchange_charge + sebi_charge + stamp_duty + gst + slippage

        return CostBreakdown(
            brokerage=round(brokerage, 4),
            stt=round(stt, 4),
            exchange_charge=round(exchange_charge, 4),
            sebi_charge=round(sebi_charge, 6),
            stamp_duty=round(stamp_duty, 4),
            gst=round(gst, 4),
            slippage=round(slippage, 4),
            total=round(total, 4),
        )

    def single_leg_cost(
        self,
        symbol:      str,
        lots:        int,
        price:       float,
        is_buy:      bool,
    ) -> CostBreakdown:
        """Cost for one side of a trade (entry OR exit, not both)."""
        inst     = INSTRUMENTS.get(symbol, {})
        lot_size = inst.get("lot_size", 1)
        tick_sz  = inst.get("tick_size", 0.05)
        quantity = lots * lot_size
        notional = price * quantity
        return self._single_leg(notional, is_buy, tick_sz, quantity)

    def round_trip_cost(
        self,
        symbol:      str,
        lots:        int,
        entry_price: float,
        exit_price:  float,
        is_long:     bool = True,    # True = buy entry, sell exit
    ) -> tuple[CostBreakdown, CostBreakdown, float]:
        """
        Full round-trip cost: entry leg + exit leg.
        Returns (entry_cost, exit_cost, total_cost_inr)
        """
        entry_cost = self.single_leg_cost(symbol, lots, entry_price, is_buy=is_long)
        exit_cost  = self.single_leg_cost(symbol, lots, exit_price,  is_buy=(not is_long))
        total      = round(entry_cost.total + exit_cost.total, 2)
        return entry_cost, exit_cost, total

    def min_points_to_breakeven(
        self,
        symbol: str,
        lots:   int,
        price:  float,
    ) -> float:
        """
        How many index points does the trade need to move
        just to cover all costs (including slippage)?
        This is the minimum edge your signal must generate.
        """
        inst     = INSTRUMENTS.get(symbol, {})
        lot_size = inst.get("lot_size", 1)
        quantity = lots * lot_size

        entry = self.single_leg_cost(symbol, lots, price, is_buy=True)
        exit_ = self.single_leg_cost(symbol, lots, price, is_buy=False)
        total_cost = entry.total + exit_.total

        # points_needed × lot_size × lots = total_cost
        points = total_cost / quantity
        return round(points, 2)

    def annual_cost_estimate(
        self,
        symbol:         str,
        lots:           int,
        avg_price:      float,
        trades_per_day: int,
        trading_days:   int = 250,
    ) -> dict:
        """
        Projects annual cost burden. Use this to sanity-check your
        strategy's expected edge against its expected cost.
        """
        _, _, per_trade = self.round_trip_cost(symbol, lots, avg_price, avg_price)
        daily_cost  = per_trade * trades_per_day
        annual_cost = daily_cost * trading_days

        inst     = INSTRUMENTS.get(symbol, {})
        lot_size = inst.get("lot_size", 1)
        notional = avg_price * lots * lot_size

        return {
            "cost_per_round_trip_inr":  round(per_trade, 2),
            "daily_cost_inr":           round(daily_cost, 2),
            "annual_cost_inr":          round(annual_cost, 2),
            "annual_cost_pct_notional": round(annual_cost / notional * 100, 4),
            "breakeven_points":         self.min_points_to_breakeven(symbol, lots, avg_price),
        }


# ── Quick sanity-check  ───────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("COST MODEL SANITY CHECK")
    print("=" * 50)

    for broker in ("zerodha", "shoonya"):
        cm = CostModel(broker=broker)
        print(f"\n--- {broker.upper()} | BANKNIFTY | 1 lot | entry ₹48,000 ---")
        entry, exit_, total = cm.round_trip_cost("BANKNIFTY", lots=1, entry_price=48000, exit_price=48150)
        print(f"Entry leg:\n{entry}")
        print(f"\nExit leg:\n{exit_}")
        print(f"\nTotal round-trip cost: ₹{total}")
        print(f"Breakeven points needed: {cm.min_points_to_breakeven('BANKNIFTY', 1, 48000)}")

        print(f"\nAnnual projection (5 trades/day, 250 days):")
        proj = cm.annual_cost_estimate("BANKNIFTY", 1, 48000, trades_per_day=5)
        for k, v in proj.items():
            print(f"  {k}: {v}")
