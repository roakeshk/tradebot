# ============================================================
#  tradebot / strategy / indicators.py
#  Vectorised indicator library.
#
#  CRITICAL RULE: Every indicator is computed on a DataFrame
#  where row N uses only rows 0..N. No peeking at future bars.
#  All functions return a pd.Series aligned to the input index.
#
#  All functions accept a DataFrame with columns:
#    open, high, low, close, volume
#  and return a Series (or DataFrame for multi-output indicators).
# ============================================================

import numpy as np
import pandas as pd
from typing import Tuple


# ── Trend indicators ─────────────────────────────────────────

def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return close.ewm(span=period, adjust=False).mean()


def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return close.rolling(period).mean()


def dema(close: pd.Series, period: int) -> pd.Series:
    """Double EMA — reduces lag vs single EMA."""
    e = ema(close, period)
    return 2 * e - ema(e, period)


def supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """
    Supertrend indicator.
    Returns DataFrame with columns: supertrend, direction (1=up, -1=down)
    """
    atr_val = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2

    upper = hl2 + multiplier * atr_val
    lower = hl2 - multiplier * atr_val

    supertrend_s = pd.Series(index=df.index, dtype=float)
    direction    = pd.Series(index=df.index, dtype=int)

    for i in range(1, len(df)):
        prev_upper = upper.iloc[i - 1]
        prev_lower = lower.iloc[i - 1]
        prev_close = df["close"].iloc[i - 1]
        curr_close = df["close"].iloc[i]

        # Adjust bands
        if lower.iloc[i] < prev_lower or prev_close < prev_lower:
            lower.iloc[i] = lower.iloc[i]
        else:
            lower.iloc[i] = prev_lower

        if upper.iloc[i] > prev_upper or prev_close > prev_upper:
            upper.iloc[i] = upper.iloc[i]
        else:
            upper.iloc[i] = prev_upper

        # Direction
        prev_st = supertrend_s.iloc[i - 1] if i > 1 else upper.iloc[i]
        if prev_st == prev_upper:
            direction.iloc[i] = 1 if curr_close > upper.iloc[i] else -1
        else:
            direction.iloc[i] = -1 if curr_close < lower.iloc[i] else 1

        supertrend_s.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]

    return pd.DataFrame({"supertrend": supertrend_s, "direction": direction})


# ── Momentum indicators ──────────────────────────────────────

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line, histogram."""
    fast_ema   = ema(close, fast)
    slow_ema   = ema(close, slow)
    macd_line  = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return pd.DataFrame({
        "macd":      macd_line,
        "signal":    signal_line,
        "histogram": histogram,
    })


def stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
) -> pd.DataFrame:
    """Stochastic oscillator %K and %D."""
    low_min  = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(d_period).mean()
    return pd.DataFrame({"k": k, "d": d})


# ── Volatility indicators ─────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — core of stop-loss sizing."""
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    return tr.ewm(com=period - 1, adjust=False).mean()


def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands: upper, middle, lower, bandwidth, %B."""
    mid   = sma(close, period)
    std   = close.rolling(period).std(ddof=0)
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    bw    = (upper - lower) / mid.replace(0, np.nan)          # bandwidth
    pctb  = (close - lower) / (upper - lower).replace(0, np.nan)  # %B
    return pd.DataFrame({
        "upper": upper, "mid": mid, "lower": lower,
        "bandwidth": bw, "pctb": pctb,
    })


def keltner_channels(
    df: pd.DataFrame,
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> pd.DataFrame:
    """Keltner Channels — used alongside BB for squeeze detection."""
    mid   = ema(df["close"], ema_period)
    atr_v = atr(df, atr_period)
    upper = mid + multiplier * atr_v
    lower = mid - multiplier * atr_v
    return pd.DataFrame({"upper": upper, "mid": mid, "lower": lower})


def bb_squeeze(df: pd.DataFrame) -> pd.Series:
    """
    TTM Squeeze: BB inside KC = low volatility coiling.
    Returns True when squeeze is active (Bollinger inside Keltner).
    Breakout from squeeze = high-probability trade setup.
    """
    bb = bollinger_bands(df["close"])
    kc = keltner_channels(df)
    return (bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])


# ── Volume indicators ────────────────────────────────────────

def vwap(df: pd.DataFrame) -> pd.Series:
    """
    VWAP — Volume Weighted Average Price.
    Resets at each trading day (09:15 IST).
    Price above VWAP = bullish bias. Below = bearish bias.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    tp_vol = typical_price * df["volume"]

    # Group by date to reset VWAP daily
    dates = df.index.normalize() if hasattr(df.index, "normalize") else pd.to_datetime(df.index).normalize()
    vwap_s = pd.Series(index=df.index, dtype=float)

    for date in dates.unique():
        mask = dates == date
        cum_tp_vol = tp_vol[mask].cumsum()
        cum_vol    = df["volume"][mask].cumsum()
        vwap_s[mask] = cum_tp_vol / cum_vol.replace(0, np.nan)

    return vwap_s


def vwap_bands(df: pd.DataFrame, std_multiplier: float = 1.0) -> pd.DataFrame:
    """VWAP with standard deviation bands (VWAP ± 1σ, ± 2σ)."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    vwap_v = vwap(df)

    dates = df.index.normalize() if hasattr(df.index, "normalize") else pd.to_datetime(df.index).normalize()
    std_s = pd.Series(index=df.index, dtype=float)

    for date in dates.unique():
        mask = dates == date
        tp_day = typical_price[mask]
        vw_day = vwap_v[mask]
        variance = ((tp_day - vw_day) ** 2 * df["volume"][mask]).cumsum() / \
                    df["volume"][mask].cumsum().replace(0, np.nan)
        std_s[mask] = np.sqrt(variance)

    return pd.DataFrame({
        "vwap":    vwap_v,
        "upper1":  vwap_v + 1 * std_s,
        "lower1":  vwap_v - 1 * std_s,
        "upper2":  vwap_v + 2 * std_s,
        "lower2":  vwap_v - 2 * std_s,
    })


def obv(df: pd.DataFrame) -> pd.Series:
    """On Balance Volume — cumulative volume direction."""
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def volume_delta(df: pd.DataFrame) -> pd.Series:
    """
    Approximate buy/sell volume delta per candle.
    Uses close position within the candle as proxy.
    Positive = buying pressure. Negative = selling pressure.
    """
    candle_range = (df["high"] - df["low"]).replace(0, np.nan)
    buy_ratio = (df["close"] - df["low"]) / candle_range
    buy_vol   = buy_ratio * df["volume"]
    sell_vol  = (1 - buy_ratio) * df["volume"]
    return (buy_vol - sell_vol).fillna(0)


def cvd(df: pd.DataFrame) -> pd.Series:
    """Cumulative Volume Delta — running sum of volume delta."""
    return volume_delta(df).cumsum()


# ── Price structure ──────────────────────────────────────────

def pivot_points(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standard pivot points (previous day's H/L/C).
    Used as support / resistance levels.
    Computed on daily bars, then forward-filled to intraday.
    """
    pivot = (df["high"] + df["low"] + df["close"]) / 3
    r1 = 2 * pivot - df["low"]
    s1 = 2 * pivot - df["high"]
    r2 = pivot + (df["high"] - df["low"])
    s2 = pivot - (df["high"] - df["low"])
    return pd.DataFrame({"pivot": pivot, "r1": r1, "s1": s1, "r2": r2, "s2": s2})


def opening_range(
    df: pd.DataFrame,
    open_time: str = "09:15",
    range_minutes: int = 15,
) -> pd.DataFrame:
    """
    Opening Range High / Low for Opening Range Breakout strategy.
    Returns ORH and ORL for each day, forward-filled across the session.
    """
    df = df.copy()
    df.index = pd.to_datetime(df.index)

    orh = pd.Series(index=df.index, dtype=float)
    orl = pd.Series(index=df.index, dtype=float)

    for date in df.index.normalize().unique():
        session_start = pd.Timestamp(f"{date.date()} {open_time}")
        session_end   = session_start + pd.Timedelta(minutes=range_minutes)

        or_mask   = (df.index >= session_start) & (df.index < session_end)
        day_mask  = df.index.normalize() == date

        or_data = df[or_mask]
        if or_data.empty:
            continue

        orh[day_mask] = or_data["high"].max()
        orl[day_mask] = or_data["low"].min()

    return pd.DataFrame({"orh": orh.ffill(), "orl": orl.ffill()})


def session_high_low(df: pd.DataFrame) -> pd.DataFrame:
    """Running session high / low — updates with each bar."""
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    sess_high = pd.Series(index=df.index, dtype=float)
    sess_low  = pd.Series(index=df.index, dtype=float)

    for date in df.index.normalize().unique():
        mask = df.index.normalize() == date
        day  = df[mask]
        sess_high[mask] = day["high"].cummax()
        sess_low[mask]  = day["low"].cummin()

    return pd.DataFrame({"sess_high": sess_high, "sess_low": sess_low})


# ── Composite / derived ──────────────────────────────────────

def trend_strength(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    ADX-like trend strength 0–100.
    > 25 = trending. < 20 = ranging.
    """
    up_move   = df["high"].diff()
    down_move = -(df["low"].diff())

    plus_dm  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr_v    = atr(df, period)
    plus_di  = 100 * ema(plus_dm, period)  / atr_v.replace(0, np.nan)
    minus_di = 100 * ema(minus_dm, period) / atr_v.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return ema(dx, period)   # ADX


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convenience function: compute all indicators and add as columns.
    Used in backtester and feature engineering.
    """
    df = df.copy()

    df["ema9"]    = ema(df["close"], 9)
    df["ema21"]   = ema(df["close"], 21)
    df["ema50"]   = ema(df["close"], 50)
    df["ema200"]  = ema(df["close"], 200)

    df["rsi14"]   = rsi(df["close"], 14)
    df["atr14"]   = atr(df, 14)
    df["adx14"]   = trend_strength(df, 14)
    df["vwap"]    = vwap(df)

    bb = bollinger_bands(df["close"])
    df["bb_upper"]  = bb["upper"]
    df["bb_mid"]    = bb["mid"]
    df["bb_lower"]  = bb["lower"]
    df["bb_pctb"]   = bb["pctb"]
    df["bb_bw"]     = bb["bandwidth"]

    macd_df = macd(df["close"])
    df["macd"]        = macd_df["macd"]
    df["macd_signal"] = macd_df["signal"]
    df["macd_hist"]   = macd_df["histogram"]

    df["volume_delta"] = volume_delta(df)
    df["cvd"]          = cvd(df)
    df["obv"]          = obv(df)
    df["squeeze"]      = bb_squeeze(df)

    or_df = opening_range(df)
    df["orh"] = or_df["orh"]
    df["orl"] = or_df["orl"]

    vb = vwap_bands(df)
    df["vwap_upper1"] = vb["upper1"]
    df["vwap_lower1"] = vb["lower1"]
    df["vwap_upper2"] = vb["upper2"]
    df["vwap_lower2"] = vb["lower2"]

    return df
