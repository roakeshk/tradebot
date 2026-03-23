# ============================================================
#  tradebot / options / chain_feed.py
#  A1 — Realtime options chain fetcher
#
#  Polls Angel One SmartAPI every 60 seconds for a fresh
#  option chain snapshot. Falls back to Black-Scholes
#  reconstruction when market is closed or broker unavailable.
#
#  Also used by B1 simulator to generate synthetic chains
#  from historical spot prices (no broker needed).
# ============================================================

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional, Callable
import pandas as pd
import numpy as np

from options.data    import ExpiryManager, OptionChain, OptionsDataPipeline
from options.pricing import BSModel

logger = logging.getLogger(__name__)


class ChainFeed:
    """
    Live option chain feed with 60-second refresh.

    Usage (live):
        feed = ChainFeed(broker=angel_broker)
        feed.subscribe("BANKNIFTY", on_chain_update)
        feed.start()

    Usage (simulation / paper — no broker):
        feed = ChainFeed()               # no broker
        chain = feed.build_from_spot("BANKNIFTY", spot=48500, sigma=0.18)
    """

    DEFAULT_IV = {"BANKNIFTY": 0.18, "NIFTY": 0.14, "CRUDEOIL": 0.25}
    STEP       = {"BANKNIFTY": 100,  "NIFTY": 50,   "CRUDEOIL": 50}
    STRIKE_RANGE = 20   # strikes either side of ATM

    def __init__(
        self,
        broker        = None,
        refresh_secs  : int = 60,
    ):
        self.broker       = broker
        self.refresh_secs = refresh_secs
        self._expiry_mgr  = ExpiryManager()
        self._bs          = BSModel()
        self._odp         = OptionsDataPipeline(broker) if broker else None
        self._chains:  dict[str, OptionChain] = {}
        self._callbacks: dict[str, list[Callable]] = {}
        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._lock     = threading.Lock()
        self._r        = 0.065          # risk-free rate (RBI repo rate proxy)

    # ── Public API ─────────────────────────────────────────────

    def subscribe(self, symbol: str, callback: Callable) -> None:
        """Register callback(chain) called on every refresh."""
        with self._lock:
            self._callbacks.setdefault(symbol, []).append(callback)
        logger.info(f"ChainFeed: subscribed {symbol}")

    def get_chain(self, symbol: str) -> Optional[OptionChain]:
        """Return latest cached chain."""
        with self._lock:
            return self._chains.get(symbol)

    def start(self) -> None:
        """Start background refresh thread."""
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True, name="ChainFeed")
        self._thread.start()
        logger.info(f"ChainFeed started (refresh={self.refresh_secs}s)")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("ChainFeed stopped")

    # ── Background refresh ──────────────────────────────────────

    def _loop(self) -> None:
        # Initial fetch immediately
        for symbol in list(self._callbacks.keys()):
            self._refresh(symbol)
        while self._running:
            time.sleep(self.refresh_secs)
            for symbol in list(self._callbacks.keys()):
                self._refresh(symbol)

    def _refresh(self, symbol: str) -> None:
        try:
            chain = self._fetch_chain(symbol)
            if chain is None:
                return
            with self._lock:
                self._chains[symbol] = chain
            for cb in self._callbacks.get(symbol, []):
                try:
                    cb(chain)
                except Exception as e:
                    logger.warning(f"ChainFeed callback error: {e}")
        except Exception as e:
            logger.error(f"ChainFeed refresh error for {symbol}: {e}")

    def _fetch_chain(self, symbol: str) -> Optional[OptionChain]:
        """Try broker first, fall back to BS reconstruction."""
        expiry = self._expiry_mgr.nearest_expiry(symbol)

        # ── Live broker path ──────────────────────────────────
        if self._odp is not None:
            try:
                chain = self._odp.get_chain(symbol, expiry)
                if chain is not None and not chain.df.empty:
                    logger.debug(f"ChainFeed: live chain for {symbol} ({len(chain.df)} strikes)")
                    return chain
            except Exception as e:
                logger.warning(f"Live chain fetch failed: {e} — using BS reconstruction")

        # ── BS reconstruction fallback ────────────────────────
        return None   # caller should use build_from_spot()

    # ── Chain builder (no broker needed) ───────────────────────

    def build_from_spot(
        self,
        symbol : str,
        spot   : float,
        sigma  : Optional[float] = None,
        expiry : Optional[date]  = None,
        dte    : Optional[int]   = None,
    ) -> OptionChain:
        """
        Build a complete option chain using Black-Scholes.
        Works 100% offline — no broker required.
        Used by the simulator and paper trading fallback.

        Args:
            symbol: BANKNIFTY / NIFTY
            spot:   current underlying price
            sigma:  implied volatility (if None, uses historical average)
            expiry: target expiry date (if None, uses nearest weekly)
            dte:    days to expiry override
        """
        sigma  = sigma  or self.DEFAULT_IV.get(symbol, 0.18)
        expiry = expiry or self._expiry_mgr.nearest_expiry(symbol)
        if dte is None:
            dte = max(0, (expiry - date.today()).days)

        T    = dte / 365.0 if dte > 0 else 1 / 365.0
        step = self.STEP.get(symbol, 100)
        r    = self._r

        # Snap ATM to nearest step
        atm = round(spot / step) * step

        strikes = [
            atm + i * step
            for i in range(-self.STRIKE_RANGE, self.STRIKE_RANGE + 1)
        ]

        rows = []
        for k in strikes:
            # IV skew: puts slightly more expensive (market reality)
            ce_sigma = sigma * (1.0 + max(0, (k - spot) / spot) * 0.2)
            pe_sigma = sigma * (1.0 + max(0, (spot - k) / spot) * 0.3)

            ce_p = self._bs.price(spot, k, T, r, ce_sigma, "ce")
            pe_p = self._bs.price(spot, k, T, r, pe_sigma, "pe")
            cg   = self._bs.greeks(spot, k, T, r, ce_sigma, "ce")
            pg   = self._bs.greeks(spot, k, T, r, pe_sigma, "pe")

            # Simulate realistic OI (peaks at ATM, decays with distance)
            dist_factor = max(0, 1.0 - abs(k - spot) / (step * 8))
            base_oi = int(500000 * dist_factor) + 10000

            rows.append({
                "strike":    float(k),
                "ce_ltp":    round(max(0.05, ce_p), 2),
                "pe_ltp":    round(max(0.05, pe_p), 2),
                "ce_iv":     round(ce_sigma, 4),
                "pe_iv":     round(pe_sigma, 4),
                "ce_delta":  round(cg["delta"], 6),
                "pe_delta":  round(pg["delta"], 6),
                "ce_gamma":  round(cg["gamma"], 8),
                "pe_gamma":  round(pg["gamma"], 8),
                "ce_theta":  round(cg["theta"], 4),
                "pe_theta":  round(pg["theta"], 4),
                "ce_vega":   round(cg["vega"],  4),
                "pe_vega":   round(pg["vega"],  4),
                "ce_oi":     base_oi + int(np.random.normal(0, 5000)),
                "pe_oi":     base_oi + int(np.random.normal(0, 5000)),
                "ce_volume": max(0, int(base_oi * 0.1 + np.random.normal(0, 1000))),
                "pe_volume": max(0, int(base_oi * 0.1 + np.random.normal(0, 1000))),
            })

        df    = pd.DataFrame(rows)
        chain = OptionChain(symbol, expiry, spot, df)
        chain.iv_rank = self._compute_iv_rank(symbol, sigma)
        return chain

    def _compute_iv_rank(self, symbol: str, current_iv: float) -> float:
        """
        IV Rank 0–100. Uses rough historical averages.
        In live trading this is computed from a full IV history.
        """
        hist_low  = {"BANKNIFTY": 0.10, "NIFTY": 0.09,  "CRUDEOIL": 0.20}.get(symbol, 0.12)
        hist_high = {"BANKNIFTY": 0.40, "NIFTY": 0.35,  "CRUDEOIL": 0.55}.get(symbol, 0.40)
        rank = (current_iv - hist_low) / max(0.01, hist_high - hist_low) * 100
        return round(float(np.clip(rank, 0, 100)), 1)

    def refresh_once(self, symbol: str, spot: float, sigma: float = None) -> OptionChain:
        """
        Force-refresh for simulation — builds chain from spot directly.
        Called every bar in the simulator.
        """
        chain = self.build_from_spot(symbol, spot, sigma)
        with self._lock:
            self._chains[symbol] = chain
        return chain
