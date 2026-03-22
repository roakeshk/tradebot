# ============================================================
#  tradebot / data / pipeline.py
#  Data pipeline — fetch, store, clean, serve OHLCV data.
#
#  Sources (in priority order):
#    1. Local SQLite cache (instant)
#    2. Zerodha Kite historical API (when connected)
#    3. yfinance (free fallback, good for Nifty/BankNifty index)
#
#  Schema:
#    Table: ohlcv_{symbol}_{timeframe}
#    Columns: timestamp (PK), open, high, low, close, volume, oi
# ============================================================

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import pandas as pd

from config.settings import DATA, DATA_DIR, INSTRUMENTS, TIMEFRAMES

logger = logging.getLogger(__name__)

# yfinance symbol mapping (index proxies — not futures, but good for strategy dev)
YF_SYMBOL_MAP = {
    "BANKNIFTY": "^NSEBANK",
    "NIFTY":     "^NSEI",
    "CRUDEOIL":  "CL=F",     # NYMEX crude (not MCX, use for strategy shape)
}

INTERVAL_MAP_YF = {
    "1min":  "1m",
    "3min":  "3m",
    "5min":  "5m",
    "15min": "15m",
    "1hour": "1h",
    "1day":  "1d",
}


class DataPipeline:
    """
    Central data access layer.

    All strategy and backtest code fetches data through this class.
    Never access the database directly from strategy code.
    """

    def __init__(self, broker=None):
        self.broker = broker   # optional: live broker for real historical data
        self.db_path = DATA_DIR / DATA["db_filename"]
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info(f"DataPipeline ready | db={self.db_path}")

    # ── Database setup ────────────────────────────────────────

    def _init_db(self) -> None:
        """Create tables for each instrument × timeframe combination."""
        with self._conn() as conn:
            for symbol in INSTRUMENTS:
                for tf in TIMEFRAMES:
                    table = self._table_name(symbol, tf)
                    conn.execute(f"""
                        CREATE TABLE IF NOT EXISTS {table} (
                            timestamp  TEXT PRIMARY KEY,
                            open       REAL NOT NULL,
                            high       REAL NOT NULL,
                            low        REAL NOT NULL,
                            close      REAL NOT NULL,
                            volume     INTEGER,
                            oi         INTEGER DEFAULT 0
                        )
                    """)
                    conn.execute(f"""
                        CREATE INDEX IF NOT EXISTS idx_{table}_ts
                        ON {table}(timestamp)
                    """)
            # Metadata table — tracks last fetch time
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fetch_log (
                    symbol    TEXT,
                    timeframe TEXT,
                    last_fetch TEXT,
                    rows_total INTEGER,
                    PRIMARY KEY (symbol, timeframe)
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    @staticmethod
    def _table_name(symbol: str, timeframe: str) -> str:
        return f"ohlcv_{symbol.lower()}_{timeframe.replace('min','m').replace('hour','h').replace('day','d')}"

    # ── Fetch & store ─────────────────────────────────────────

    def fetch_and_store(
        self,
        symbol:     str,
        timeframe:  str = "5min",
        years_back: int = None,
        force:      bool = False,
    ) -> int:
        """
        Fetch historical data and store in local db.
        Returns number of new rows added.

        Set force=True to re-fetch even if cache is fresh.
        """
        years_back = years_back or DATA["history_years"]

        if not force and self._is_cache_fresh(symbol, timeframe):
            logger.info(f"Cache fresh for {symbol} {timeframe}, skipping fetch")
            return 0

        to_date   = datetime.now()
        from_date = to_date - timedelta(days=365 * years_back)

        df = self._fetch_from_source(symbol, timeframe, from_date, to_date)

        if df is None or df.empty:
            logger.warning(f"No data returned for {symbol} {timeframe}")
            return 0

        rows = self._store(symbol, timeframe, df)
        self._update_fetch_log(symbol, timeframe, rows)
        logger.info(f"Stored {rows} rows for {symbol} {timeframe}")
        return rows

    def _fetch_from_source(
        self,
        symbol:    str,
        timeframe: str,
        from_date: datetime,
        to_date:   datetime,
    ) -> Optional[pd.DataFrame]:
        """Try broker first, fall back to yfinance."""

        # ── Try broker ────────────────────────────────────────
        if self.broker is not None:
            try:
                df = self.broker.get_historical_data(
                    symbol=symbol,
                    exchange=INSTRUMENTS[symbol]["exchange"],
                    interval=timeframe,
                    from_date=from_date,
                    to_date=to_date,
                )
                if df is not None and not df.empty:
                    logger.info(f"Fetched {len(df)} rows from broker for {symbol}")
                    return self._clean(df)
            except Exception as e:
                logger.warning(f"Broker fetch failed for {symbol}: {e}. Falling back to yfinance.")

        # ── yfinance fallback ─────────────────────────────────
        return self._fetch_yfinance(symbol, timeframe, from_date, to_date)

    def _fetch_yfinance(
        self,
        symbol:    str,
        timeframe: str,
        from_date: datetime,
        to_date:   datetime,
    ) -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed. Run: pip install yfinance")
            return None

        yf_sym = YF_SYMBOL_MAP.get(symbol)
        if yf_sym is None:
            logger.error(f"No yfinance mapping for {symbol}")
            return None

        yf_interval = INTERVAL_MAP_YF.get(timeframe, "1d")

        # yfinance limits intraday history:
        # 1min → 7 days, 5min → 60 days, 1hour → 730 days
        # We chunk requests to get as much as possible
        chunks = self._date_chunks(from_date, to_date, timeframe)
        frames = []
        for start, end in chunks:
            try:
                ticker = yf.Ticker(yf_sym)
                df = ticker.history(
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    interval=yf_interval,
                    auto_adjust=True,
                )
                if not df.empty:
                    frames.append(df)
            except Exception as e:
                logger.warning(f"yfinance chunk {start}–{end} failed: {e}")

        if not frames:
            return None

        combined = pd.concat(frames)
        combined = combined[~combined.index.duplicated(keep="last")]
        combined = combined.sort_index()

        # Standardise column names
        combined = combined.rename(columns=str.lower)
        combined = combined[["open", "high", "low", "close", "volume"]]
        combined.index.name = "timestamp"
        combined = combined.reset_index()

        logger.info(f"yfinance returned {len(combined)} rows for {symbol} {timeframe}")
        return self._clean(combined)

    @staticmethod
    def _date_chunks(
        from_date: datetime,
        to_date:   datetime,
        timeframe: str,
    ) -> list[tuple]:
        """Split date range into chunks based on yfinance API limits."""
        limit_days = {
            "1min": 7, "3min": 60, "5min": 60, "15min": 60,
            "1hour": 730, "1day": 3650
        }
        max_days = limit_days.get(timeframe, 60)
        chunks = []
        current = from_date
        while current < to_date:
            chunk_end = min(current + timedelta(days=max_days), to_date)
            chunks.append((current, chunk_end))
            current = chunk_end
        return chunks

    # ── Data cleaning ─────────────────────────────────────────

    @staticmethod
    def _clean(df: pd.DataFrame) -> pd.DataFrame:
        """Standardise, validate, and clean OHLCV data."""
        df = df.copy()

        # Ensure timestamp column exists
        if "timestamp" not in df.columns and df.index.name == "timestamp":
            df = df.reset_index()

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")
        df = df.drop_duplicates(subset=["timestamp"], keep="last")

        # Drop rows with zero or null OHLC
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df[(df["open"] > 0) & (df["close"] > 0)]

        # Fix OHLC integrity (high must be max, low must be min)
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"]  = df[["open", "low",  "close"]].min(axis=1)

        # Round to 2dp
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].round(2)

        if "volume" not in df.columns:
            df["volume"] = 0
        df["volume"] = df["volume"].fillna(0).astype(int)

        if "oi" not in df.columns:
            df["oi"] = 0

        return df

    # ── Storage ───────────────────────────────────────────────

    def _store(self, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
        table = self._table_name(symbol, timeframe)
        df["timestamp"] = df["timestamp"].astype(str)

        with self._conn() as conn:
            # INSERT OR REPLACE to handle overlapping data
            rows_before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            df[["timestamp", "open", "high", "low", "close", "volume", "oi"]].to_sql(
                table, conn, if_exists="replace" if rows_before == 0 else "append",
                index=False, method="multi",
            )
            # Remove duplicates that slipped through
            conn.execute(f"""
                DELETE FROM {table}
                WHERE rowid NOT IN (
                    SELECT MIN(rowid) FROM {table} GROUP BY timestamp
                )
            """)
            rows_after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        return rows_after

    def _update_fetch_log(self, symbol: str, timeframe: str, rows: int) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO fetch_log (symbol, timeframe, last_fetch, rows_total)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe) DO UPDATE SET
                    last_fetch=excluded.last_fetch,
                    rows_total=excluded.rows_total
            """, (symbol, timeframe, datetime.now().isoformat(), rows))

    def _is_cache_fresh(self, symbol: str, timeframe: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT last_fetch FROM fetch_log WHERE symbol=? AND timeframe=?",
                (symbol, timeframe)
            ).fetchone()
        if not row:
            return False
        last = datetime.fromisoformat(row[0])
        return (datetime.now() - last).total_seconds() < DATA["cache_expiry_hours"] * 3600

    # ── Retrieval ─────────────────────────────────────────────

    def get(
        self,
        symbol:     str,
        timeframe:  str = "5min",
        from_date:  Optional[datetime] = None,
        to_date:    Optional[datetime] = None,
        n_bars:     Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Main data retrieval method.

        Usage:
            dp = DataPipeline()
            df = dp.get("BANKNIFTY", "5min", n_bars=500)
            df = dp.get("NIFTY", "1day", from_date=datetime(2022,1,1))
        """
        table = self._table_name(symbol, timeframe)

        query = f"SELECT * FROM {table}"
        params = []
        conditions = []

        if from_date:
            conditions.append("timestamp >= ?")
            params.append(from_date.isoformat())
        if to_date:
            conditions.append("timestamp <= ?")
            params.append(to_date.isoformat())
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY timestamp ASC"

        if n_bars:
            query = f"SELECT * FROM ({query}) ORDER BY timestamp DESC LIMIT {n_bars}"
            query = f"SELECT * FROM ({query}) ORDER BY timestamp ASC"

        with self._conn() as conn:
            df = pd.read_sql_query(query, conn, params=params or None)

        if df.empty:
            logger.warning(f"No data in cache for {symbol} {timeframe}. Run fetch_and_store() first.")
            return pd.DataFrame()

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")
        return df

    def get_latest_candle(self, symbol: str, timeframe: str = "5min") -> Optional[pd.Series]:
        """Returns the most recent completed candle."""
        table = self._table_name(symbol, timeframe)
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        cols = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
        return pd.Series(dict(zip(cols, row)))

    # ── Utility ───────────────────────────────────────────────

    def resample(self, df: pd.DataFrame, from_tf: str, to_tf: str) -> pd.DataFrame:
        """
        Resample 1min data to any higher timeframe.
        Useful for multi-timeframe analysis.
        """
        freq_map = {
            "1min": "1T", "3min": "3T", "5min": "5T",
            "15min": "15T", "1hour": "1H", "1day": "1D"
        }
        freq = freq_map.get(to_tf)
        if not freq:
            raise ValueError(f"Unknown timeframe: {to_tf}")

        return df.resample(freq, closed="left", label="left").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
            "oi":     "last",
        }).dropna(subset=["open"])

    def data_summary(self) -> pd.DataFrame:
        """Print summary of all data available in local cache."""
        rows = []
        with self._conn() as conn:
            for symbol in INSTRUMENTS:
                for tf in TIMEFRAMES:
                    table = self._table_name(symbol, tf)
                    try:
                        r = conn.execute(
                            f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {table}"
                        ).fetchone()
                        rows.append({
                            "symbol": symbol, "timeframe": tf,
                            "bars": r[0], "from": r[1], "to": r[2]
                        })
                    except Exception:
                        pass
        return pd.DataFrame(rows)


# ── Bootstrap script ──────────────────────────────────────────
if __name__ == "__main__":
    """
    Run this once to populate the local database with historical data.
    No broker account needed — uses yfinance as fallback.

    Usage:
        python -m data.pipeline
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dp = DataPipeline()

    # Fetch primary instruments and timeframes
    priority_instruments = ["BANKNIFTY", "NIFTY"]
    priority_timeframes  = ["5min", "15min", "1hour", "1day"]

    print("\n" + "=" * 60)
    print("INITIAL DATA FETCH")
    print("=" * 60)

    for sym in priority_instruments:
        for tf in priority_timeframes:
            print(f"\nFetching {sym} {tf}...")
            try:
                n = dp.fetch_and_store(sym, tf, years_back=DATA["history_years"], force=True)
                print(f"  → {n} rows stored")
            except Exception as e:
                print(f"  → ERROR: {e}")

    print("\n" + "=" * 60)
    print("DATA SUMMARY")
    print("=" * 60)
    summary = dp.data_summary()
    print(summary.to_string(index=False))

    # Show sample data
    print("\nSample BankNifty 5min (last 5 bars):")
    df = dp.get("BANKNIFTY", "5min", n_bars=5)
    print(df.to_string())
