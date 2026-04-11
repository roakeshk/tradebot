# ============================================================
#  tradebot / options / data.py
#  T1 — Options data pipeline
#
#  Responsibilities:
#    - Fetch live option chain from Angel One / Shoonya
#    - Parse and normalise all strikes and expiries
#    - Maintain rolling option chain cache (refreshed every 1 min)
#    - Provide clean interface: get_chain(), get_strike(), get_iv_surface()
#    - Manage weekly/monthly expiry calendar
#    - Calculate implied volatility from market prices
# ============================================================

import logging
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
import math

import pandas as pd
import numpy as np

from config.settings import DATA_DIR, INSTRUMENTS, SESSION, MARKET, RISK_FREE_RATE
from options.pricing import BSModel

logger = logging.getLogger(__name__)

OPT_DB = DATA_DIR / "options_chain.db"


class ExpiryManager:
    """
    Manages NSE weekly and monthly expiry dates.

    BankNifty: weekly expiry every Wednesday
    Nifty:     weekly expiry every Thursday
    Monthly:   last Thursday of the month

    We always prefer the nearest weekly expiry for short-term
    premium-selling strategies (theta decay is fastest here).
    """

    # Expiry weekday is now config-driven via INSTRUMENTS[symbol]["expiry_weekday"]
    # US options: Friday (4), India varies by symbol

    def get_expiries(self, symbol: str, n: int = 4) -> list[date]:
        """Return next N expiry dates for symbol."""
        inst = INSTRUMENTS.get(symbol, {})
        weekday = inst.get("expiry_weekday", 4 if MARKET == "US" else 3)
        expiries = []
        d = date.today()
        while len(expiries) < n:
            if d.weekday() == weekday and d >= date.today():
                expiries.append(d)
            d += timedelta(days=1)
        return expiries

    def nearest_expiry(self, symbol: str) -> date:
        return self.get_expiries(symbol, 1)[0]

    def days_to_expiry(self, expiry: date) -> int:
        return max(0, (expiry - date.today()).days)

    def time_to_expiry(self, expiry: date) -> float:
        """Fraction of year to expiry (used in Black-Scholes)."""
        days = self.days_to_expiry(expiry)
        hours_remaining = 0
        now = datetime.now()
        if days == 0:
            _ch, _cm = map(int, SESSION["market_close"].split(":"))
            close = datetime.now().replace(hour=_ch, minute=_cm, second=0)
            hours_remaining = max(0, (close - now).total_seconds() / 3600)
            _oh, _om = map(int, SESSION["market_open"].split(":"))
            trading_hours = (_ch * 60 + _cm - _oh * 60 - _om) / 60.0
            return hours_remaining / (252 * trading_hours)
        return days / 252.0

    def is_expiry_day(self, symbol: str) -> bool:
        inst = INSTRUMENTS.get(symbol, {})
        weekday = inst.get("expiry_weekday", 4 if MARKET == "US" else 3)
        return date.today().weekday() == weekday


class OptionChain:
    """
    Represents a complete option chain snapshot for one symbol + expiry.

    Attributes:
        symbol:   underlying symbol (BANKNIFTY / NIFTY)
        expiry:   expiry date
        spot:     current spot price of underlying
        df:       DataFrame with all strikes — columns:
                  strike, ce_ltp, ce_oi, ce_volume, ce_iv, ce_delta, ce_gamma,
                  ce_theta, ce_vega, pe_ltp, pe_oi, pe_volume, pe_iv, pe_delta,
                  pe_gamma, pe_theta, pe_vega
        atm:      at-the-money strike
        timestamp: when this chain was fetched
    """

    def __init__(self, symbol: str, expiry: date, spot: float, df: pd.DataFrame):
        self.symbol    = symbol
        self.expiry    = expiry
        self.spot      = spot
        self.df        = df
        self.timestamp = datetime.now()
        self.atm       = self._find_atm()

    def _find_atm(self) -> float:
        """ATM = strike closest to spot."""
        if self.df.empty:
            return self.spot
        diffs = (self.df["strike"] - self.spot).abs()
        return float(self.df.loc[diffs.idxmin(), "strike"])

    def get_strike(self, strike: float) -> Optional[pd.Series]:
        row = self.df[self.df["strike"] == strike]
        return row.iloc[0] if not row.empty else None

    def get_atm_strike(self) -> Optional[pd.Series]:
        return self.get_strike(self.atm)

    def get_otm_calls(self, n: int = 5) -> pd.DataFrame:
        """n strikes OTM on call side."""
        calls = self.df[self.df["strike"] > self.atm]
        return calls.head(n)

    def get_otm_puts(self, n: int = 5) -> pd.DataFrame:
        """n strikes OTM on put side."""
        puts = self.df[self.df["strike"] < self.atm].sort_values("strike", ascending=False)
        return puts.head(n)

    @property
    def pcr(self) -> float:
        """Put-call ratio by OI."""
        total_ce_oi = self.df["ce_oi"].sum()
        total_pe_oi = self.df["pe_oi"].sum()
        return round(total_pe_oi / max(1, total_ce_oi), 3)

    _iv_rank_override: Optional[float] = None

    @property
    def iv_rank(self) -> Optional[float]:
        """IV rank 0–100 using stored historical IVs."""
        if self._iv_rank_override is not None:
            return self._iv_rank_override
        return _get_iv_rank(self.symbol)

    @iv_rank.setter
    def iv_rank(self, value: float) -> None:
        self._iv_rank_override = value

    @property
    def days_to_expiry(self) -> int:
        return max(0, (self.expiry - date.today()).days)


class OptionsDataPipeline:
    """
    Central data access layer for options.

    Usage:
        odp = OptionsDataPipeline(broker)
        chain = odp.get_chain("BANKNIFTY")           # nearest expiry
        chain = odp.get_chain("NIFTY", expiry_date)  # specific expiry
        iv_rank = odp.get_iv_rank("BANKNIFTY")
    """

    def __init__(self, broker=None):
        self.broker  = broker
        self.expiry  = ExpiryManager()
        self._cache: dict[str, OptionChain] = {}
        self._cache_ts: dict[str, datetime] = {}
        OPT_DB.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(OPT_DB)) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS iv_history (
                date TEXT, symbol TEXT, iv REAL,
                PRIMARY KEY(date, symbol))""")
            c.execute("""CREATE TABLE IF NOT EXISTS chain_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, expiry TEXT, spot REAL,
                strike REAL, ce_ltp REAL, ce_oi INTEGER, ce_iv REAL,
                pe_ltp REAL, pe_oi INTEGER, pe_iv REAL,
                ce_delta REAL, pe_delta REAL,
                ce_theta REAL, pe_theta REAL,
                ce_vega REAL, pe_vega REAL,
                ce_gamma REAL, pe_gamma REAL,
                timestamp TEXT)""")

    # ── Main interface ────────────────────────────────────────

    def get_chain(
        self,
        symbol:  str,
        expiry:  Optional[date] = None,
        force:   bool = False,
    ) -> Optional[OptionChain]:
        """
        Get current option chain. Caches for 60 seconds.
        Falls back to synthetic chain if broker not connected.
        """
        expiry = expiry or self.expiry.nearest_expiry(symbol)
        cache_key = f"{symbol}_{expiry}"

        # Return cache if fresh
        if not force and cache_key in self._cache:
            age = (datetime.now() - self._cache_ts[cache_key]).seconds
            if age < 60:
                return self._cache[cache_key]

        # Try live fetch
        chain = self._fetch_live(symbol, expiry)

        if chain is None:
            logger.warning(f"Live chain unavailable for {symbol} — using synthetic")
            chain = self._synthetic_chain(symbol, expiry)

        if chain:
            self._cache[cache_key] = chain
            self._cache_ts[cache_key] = datetime.now()
            self._store_chain(chain)

        return chain

    def get_iv_rank(self, symbol: str, lookback_days: int = 252) -> float:
        """
        IV Rank = (current IV - 52w low) / (52w high - 52w low) × 100
        Above 50 = elevated IV → premium selling strategies work well.
        Below 30 = low IV → premium buying (buying options) may work.
        """
        return _get_iv_rank(symbol, lookback_days)

    def get_iv_surface(self, symbol: str, expiry: date) -> pd.DataFrame:
        """
        IV surface: rows = strikes, columns = expiries (term structure).
        Used to identify IV skew and term structure anomalies.
        """
        chain = self.get_chain(symbol, expiry)
        if chain is None or chain.df.empty:
            return pd.DataFrame()

        surface = chain.df[["strike", "ce_iv", "pe_iv"]].copy()
        surface["mid_iv"] = (surface["ce_iv"] + surface["pe_iv"]) / 2
        surface = surface.set_index("strike")
        return surface

    # ── Live fetch ────────────────────────────────────────────

    def _fetch_live(self, symbol: str, expiry: date) -> Optional[OptionChain]:
        if self.broker is None:
            return None
        try:
            exchange = INSTRUMENTS.get(symbol, {}).get("exchange", "NSE")
            spot = self.broker.get_ltp(symbol, exchange)
            if spot <= 0:
                return None

            # Angel One / Zerodha return option chain data
            # Format varies — normalise here
            raw = self._fetch_from_broker(symbol, expiry, spot)
            if raw is None or raw.empty:
                return None

            return OptionChain(symbol, expiry, spot, raw)
        except Exception as e:
            logger.warning(f"Live chain fetch error for {symbol}: {e}")
            return None

    def _fetch_from_broker(
        self, symbol: str, expiry: date, spot: float
    ) -> Optional[pd.DataFrame]:
        """Fetch raw chain from broker and normalise columns."""
        try:
            # Angel One SmartAPI option chain endpoint
            if hasattr(self.broker, "_api"):
                exp_str = expiry.strftime("%d%b%Y").upper()
                data = self.broker._api.optionChain(
                    name=symbol, expiry=exp_str, strikecount=20
                )
                if data and data.get("status"):
                    return self._normalise_angel_chain(data["data"], spot)
        except Exception as e:
            logger.debug(f"Broker chain fetch: {e}")
        return None

    def _normalise_angel_chain(
        self, raw_data: list, spot: float
    ) -> pd.DataFrame:
        rows = []
        bs   = BSModel()
        for item in raw_data:
            strike = float(item.get("strikePrice", 0))
            ce     = item.get("CE", {})
            pe     = item.get("PE", {})
            if not strike:
                continue
            row = {
                "strike":    strike,
                "ce_ltp":    float(ce.get("lastPrice", 0)),
                "ce_oi":     int(ce.get("openInterest", 0)),
                "ce_volume": int(ce.get("totalTradedVolume", 0)),
                "pe_ltp":    float(pe.get("lastPrice", 0)),
                "pe_oi":     int(pe.get("openInterest", 0)),
                "pe_volume": int(pe.get("totalTradedVolume", 0)),
            }
            rows.append(row)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)

        # Calculate Greeks for each strike
        for idx, row in df.iterrows():
            for opt_type, ltp_col in [("ce", "ce_ltp"), ("pe", "pe_ltp")]:
                ltp = row[ltp_col]
                if ltp > 0:
                    iv = bs.implied_volatility(
                        market_price=ltp, S=spot, K=row["strike"],
                        T=0.02, r=RISK_FREE_RATE, option_type=opt_type
                    )
                    g = bs.greeks(
                        S=spot, K=row["strike"], T=0.02,
                        r=RISK_FREE_RATE, sigma=iv, option_type=opt_type
                    )
                    df.at[idx, f"{opt_type}_iv"]    = round(iv * 100, 2)
                    df.at[idx, f"{opt_type}_delta"] = round(g["delta"], 4)
                    df.at[idx, f"{opt_type}_gamma"] = round(g["gamma"], 6)
                    df.at[idx, f"{opt_type}_theta"] = round(g["theta"], 2)
                    df.at[idx, f"{opt_type}_vega"]  = round(g["vega"], 2)
                else:
                    for greek in ["iv","delta","gamma","theta","vega"]:
                        df.at[idx, f"{opt_type}_{greek}"] = 0.0

        return df

    # ── Synthetic chain (for testing without broker) ──────────

    def _synthetic_chain(self, symbol: str, expiry: date) -> OptionChain:
        """
        Generate a realistic synthetic option chain for testing.
        Uses Black-Scholes with instrument-specific default IV.
        """
        inst   = INSTRUMENTS.get(symbol, {})
        spot   = inst.get("default_spot", 500)
        step   = inst.get("strike_step", 1 if MARKET == "US" else 100)
        tte    = ExpiryManager().time_to_expiry(expiry)
        bs     = BSModel()
        iv_atm = inst.get("default_iv", 0.18)
        r      = RISK_FREE_RATE

        # Determine range (wider for higher-priced underlyings)
        strike_range = int(step * 20)
        strikes = range(
            int(spot - strike_range),
            int(spot + strike_range + step),
            max(1, int(step))
        )

        rows = []
        for k in strikes:
            # IV smile: higher IV for OTM options
            moneyness  = abs(k - spot) / spot
            iv_smile   = iv_atm * (1 + 0.5 * moneyness)
            ce_price   = max(0.05, bs.price(spot, k, tte, r, iv_smile, "ce"))
            pe_price   = max(0.05, bs.price(spot, k, tte, r, iv_smile, "pe"))
            ce_greeks  = bs.greeks(spot, k, tte, r, iv_smile, "ce")
            pe_greeks  = bs.greeks(spot, k, tte, r, iv_smile, "pe")

            rows.append({
                "strike":    float(k),
                "ce_ltp":    round(ce_price, 2),
                "ce_oi":     int(np.random.randint(100, 50000)),
                "ce_volume": int(np.random.randint(10, 5000)),
                "ce_iv":     round(iv_smile * 100, 2),
                "ce_delta":  round(ce_greeks["delta"], 4),
                "ce_gamma":  round(ce_greeks["gamma"], 6),
                "ce_theta":  round(ce_greeks["theta"], 2),
                "ce_vega":   round(ce_greeks["vega"], 2),
                "pe_ltp":    round(pe_price, 2),
                "pe_oi":     int(np.random.randint(100, 50000)),
                "pe_volume": int(np.random.randint(10, 5000)),
                "pe_iv":     round(iv_smile * 100, 2),
                "pe_delta":  round(pe_greeks["delta"], 4),
                "pe_gamma":  round(pe_greeks["gamma"], 6),
                "pe_theta":  round(pe_greeks["theta"], 2),
                "pe_vega":   round(pe_greeks["vega"], 2),
            })

        df = pd.DataFrame(rows)
        return OptionChain(symbol, expiry, float(spot), df)

    # ── Storage ───────────────────────────────────────────────

    def _store_chain(self, chain: OptionChain) -> None:
        try:
            with sqlite3.connect(str(OPT_DB)) as conn:
                for _, row in chain.df.iterrows():
                    conn.execute("""
                        INSERT INTO chain_snapshot
                        (symbol,expiry,spot,strike,ce_ltp,ce_oi,ce_iv,
                         pe_ltp,pe_oi,pe_iv,ce_delta,pe_delta,
                         ce_theta,pe_theta,ce_vega,pe_vega,ce_gamma,pe_gamma,timestamp)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        chain.symbol, str(chain.expiry), chain.spot,
                        row.get("strike"), row.get("ce_ltp"), row.get("ce_oi"), row.get("ce_iv"),
                        row.get("pe_ltp"), row.get("pe_oi"), row.get("pe_iv"),
                        row.get("ce_delta"), row.get("pe_delta"),
                        row.get("ce_theta"), row.get("pe_theta"),
                        row.get("ce_vega"), row.get("pe_vega"),
                        row.get("ce_gamma"), row.get("pe_gamma"),
                        chain.timestamp.isoformat()
                    ))
                # Store ATM IV in history
                atm_row = chain.get_atm_strike()
                if atm_row is not None:
                    mid_iv = (atm_row.get("ce_iv", 0) + atm_row.get("pe_iv", 0)) / 2
                    conn.execute("""
                        INSERT OR REPLACE INTO iv_history(date, symbol, iv)
                        VALUES(?,?,?)
                    """, (str(date.today()), chain.symbol, round(mid_iv, 2)))
        except Exception as e:
            logger.debug(f"Chain store error: {e}")


def _get_iv_rank(symbol: str, lookback: int = 252) -> float:
    """Calculate IV rank from stored history."""
    try:
        with sqlite3.connect(str(OPT_DB)) as c:
            rows = c.execute(
                "SELECT iv FROM iv_history WHERE symbol=? ORDER BY date DESC LIMIT ?",
                (symbol, lookback)
            ).fetchall()
        if len(rows) < 20:
            return 50.0   # not enough history — assume mid
        ivs     = [r[0] for r in rows]
        current = ivs[0]
        iv_min  = min(ivs)
        iv_max  = max(ivs)
        if iv_max == iv_min:
            return 50.0
        return round((current - iv_min) / (iv_max - iv_min) * 100, 1)
    except Exception:
        return 50.0
