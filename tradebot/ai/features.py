# ============================================================
#  tradebot / ai / features.py
#  Feature engineering for Engine B (AI layer).
#
#  Produces ~300 features per bar from raw OHLCV + indicators.
#  These feed into the XGBoost classifier which predicts
#  whether a signal is likely to be a winner or loser.
#
#  Design principle:
#    Features must be available at signal time — no lookahead.
#    All features are normalised / stationary (returns, ratios,
#    z-scores) so the model generalises across price levels.
#    Raw price values (e.g. close=48000) are NEVER features.
# ============================================================

import numpy as np
import pandas as pd
from typing import Optional

from strategy.indicators import (
    ema, rsi, atr, vwap, vwap_bands, macd,
    bollinger_bands, volume_delta, cvd, trend_strength,
    opening_range, stochastic, obv, keltner_channels, bb_squeeze
)


class FeatureEngine:
    """
    Builds the feature matrix for ML training and inference.

    Usage:
        fe = FeatureEngine()
        # Training: build full feature matrix from historical df
        X, meta = fe.build_features(df, label_lookahead=12)
        # Inference: features for the last complete bar only
        features = fe.get_current_features(df)
    """

    def __init__(self, lookahead_bars: int = 12):
        """
        lookahead_bars: how many future bars to use for label generation.
        At 5min bars, 12 bars = 60 minutes look-forward for outcome.
        """
        self.lookahead = lookahead_bars

    # ── Public API ────────────────────────────────────────────

    def build_features(
        self,
        df: pd.DataFrame,
        label_lookahead: Optional[int] = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Build full feature matrix from historical OHLCV dataframe.
        Returns (X, meta) where:
          X    = feature DataFrame (rows = bars, cols = features)
          meta = timestamp, close, signal_context columns
        Rows with NaN (warm-up period) are dropped.
        """
        lookahead = label_lookahead or self.lookahead
        feat = self._compute_all(df)

        # Drop warm-up rows (first 200 bars for longest indicator)
        feat = feat.dropna()

        meta = pd.DataFrame({
            "timestamp": feat.index,
            "close":     df["close"].reindex(feat.index),
        })

        return feat, meta

    def get_current_features(self, df: pd.DataFrame) -> Optional[pd.Series]:
        """
        Compute features for the last completed bar only.
        Used during live trading / paper trading for inference.
        Returns None if insufficient data.
        """
        if len(df) < 200:
            return None
        feat = self._compute_all(df)
        last = feat.iloc[-2]  # -2 = last completed bar
        if last.isna().sum() > 10:
            return None
        return last.fillna(0)

    # ── Feature computation ───────────────────────────────────

    def _compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Master feature builder. Every feature group is a method."""
        frames = [
            self._price_features(df),
            self._momentum_features(df),
            self._volatility_features(df),
            self._volume_features(df),
            self._vwap_features(df),
            self._session_features(df),
            self._candle_features(df),
            self._crossover_features(df),
            self._regime_features(df),
        ]
        return pd.concat(frames, axis=1)

    def _price_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Price momentum and trend features — all normalised."""
        c = df["close"]
        feats = {}

        # Returns at multiple horizons (stationary, scale-free)
        for n in [1, 2, 3, 5, 10, 20, 50]:
            feats[f"ret_{n}"] = c.pct_change(n)

        # EMA distances normalised by ATR
        atr14 = atr(df, 14)
        for p in [9, 21, 50, 200]:
            e = ema(c, p)
            feats[f"dist_ema{p}"] = (c - e) / atr14.replace(0, np.nan)
            feats[f"ema{p}_slope"] = e.diff(3) / atr14.replace(0, np.nan)

        # EMA crossover ratios
        e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
        feats["ema9_21_ratio"]  = (e9  - e21) / atr14.replace(0, np.nan)
        feats["ema21_50_ratio"] = (e21 - e50) / atr14.replace(0, np.nan)
        feats["ema9_50_ratio"]  = (e9  - e50) / atr14.replace(0, np.nan)

        # Price position relative to recent high/low (0-1 range)
        for n in [5, 10, 20]:
            high_n = df["high"].rolling(n).max()
            low_n  = df["low"].rolling(n).min()
            rng    = (high_n - low_n).replace(0, np.nan)
            feats[f"pct_range_{n}"] = (c - low_n) / rng

        return pd.DataFrame(feats, index=df.index)

    def _momentum_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """RSI, MACD, stochastic — all already 0-100 or normalised."""
        c = df["close"]
        feats = {}

        for p in [7, 14, 21]:
            feats[f"rsi_{p}"] = rsi(c, p) / 100   # normalise to 0-1

        rsi14 = rsi(c, 14)
        feats["rsi14_slope"]   = rsi14.diff(3)
        feats["rsi14_dist50"]  = (rsi14 - 50) / 50

        macd_df = macd(c, 12, 26, 9)
        atr14   = atr(df, 14)
        feats["macd_norm"]    = macd_df["macd"]      / atr14.replace(0, np.nan)
        feats["macd_sig_norm"]= macd_df["signal"]    / atr14.replace(0, np.nan)
        feats["macd_hist_norm"]= macd_df["histogram"]/ atr14.replace(0, np.nan)
        feats["macd_hist_slope"]= macd_df["histogram"].diff(3)

        stoch = stochastic(df, 14, 3)
        feats["stoch_k"]      = stoch["k"] / 100
        feats["stoch_d"]      = stoch["d"] / 100
        feats["stoch_kd_diff"]= (stoch["k"] - stoch["d"]) / 100

        return pd.DataFrame(feats, index=df.index)

    def _volatility_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """ATR ratios, Bollinger bandwidth — volatility regime features."""
        feats = {}
        c     = df["close"]

        atr14 = atr(df, 14)
        atr7  = atr(df, 7)
        atr28 = atr(df, 28)

        # ATR ratios (current vs longer-term average)
        feats["atr_ratio_7_14"]  = atr7  / atr14.replace(0, np.nan)
        feats["atr_ratio_14_28"] = atr14 / atr28.replace(0, np.nan)
        feats["atr_vs_20avg"]    = atr14 / atr14.rolling(20).mean().replace(0, np.nan)
        feats["atr_slope"]       = atr14.diff(5) / atr14.replace(0, np.nan)

        # Bollinger band features
        bb = bollinger_bands(c, 20, 2.0)
        feats["bb_pctb"]      = bb["pctb"]       # 0=lower band, 1=upper band
        feats["bb_bandwidth"] = bb["bandwidth"]
        feats["bb_bw_slope"]  = bb["bandwidth"].diff(5)

        # Keltner / BB squeeze
        kc = keltner_channels(df)
        feats["squeeze"] = bb_squeeze(df).astype(float)
        feats["kc_width"]= (kc["upper"] - kc["lower"]) / atr14.replace(0, np.nan)

        # Realised volatility (std of returns)
        ret = c.pct_change()
        for n in [5, 10, 20]:
            feats[f"realvol_{n}"] = ret.rolling(n).std() * np.sqrt(252 * 78)  # annualised

        return pd.DataFrame(feats, index=df.index)

    def _volume_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Volume ratios, OBV direction, CVD trend."""
        feats = {}
        v     = df["volume"].astype(float)

        vol_avg20 = v.rolling(20).mean().replace(0, np.nan)
        vol_avg5  = v.rolling(5).mean().replace(0, np.nan)

        feats["vol_ratio_20"]  = v / vol_avg20
        feats["vol_ratio_5"]   = v / vol_avg5
        feats["vol_spike"]     = (v > 2 * vol_avg20).astype(float)
        feats["vol_slope"]     = v.pct_change(5)

        vdelta = volume_delta(df)
        feats["vdelta_norm"]   = vdelta / v.replace(0, np.nan)
        feats["vdelta_slope"]  = vdelta.diff(3) / v.replace(0, np.nan)
        feats["vdelta_sign"]   = np.sign(vdelta)

        cvd_s = cvd(df)
        feats["cvd_slope_5"]   = cvd_s.diff(5)
        feats["cvd_slope_10"]  = cvd_s.diff(10)

        obv_s = obv(df)
        obv_ema = ema(obv_s, 20)
        feats["obv_vs_ema"]    = np.sign(obv_s - obv_ema)

        return pd.DataFrame(feats, index=df.index)

    def _vwap_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """VWAP distance, band position — institutional reference features."""
        feats = {}
        atr14 = atr(df, 14)
        vb    = vwap_bands(df, 1.0)
        c     = df["close"]

        feats["dist_vwap"]    = (c - vb["vwap"])   / atr14.replace(0, np.nan)
        feats["dist_vwap_u1"] = (c - vb["upper1"]) / atr14.replace(0, np.nan)
        feats["dist_vwap_l1"] = (c - vb["lower1"]) / atr14.replace(0, np.nan)
        feats["above_vwap"]   = (c > vb["vwap"]).astype(float)
        feats["vwap_band_pos"]= (c - vb["lower1"]) / (vb["upper1"] - vb["lower1"]).replace(0, np.nan)

        vwap_slope = vb["vwap"].diff(5)
        feats["vwap_slope"]   = vwap_slope / atr14.replace(0, np.nan)
        feats["vwap_trending"]= (vwap_slope.abs() > 0.3 * atr14).astype(float)

        return pd.DataFrame(feats, index=df.index)

    def _session_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Time-of-day features — encodes intraday seasonality."""
        feats = {}
        idx   = pd.to_datetime(df.index)

        # Minute of day (normalised 0-1)
        mins = idx.hour * 60 + idx.minute
        total_session = (15 * 60 + 30) - (9 * 60 + 15)
        feats["session_progress"] = (mins - (9 * 60 + 15)) / total_session

        # Cyclical encoding (handles wrap-around)
        feats["time_sin"] = np.sin(2 * np.pi * mins / (24 * 60))
        feats["time_cos"] = np.cos(2 * np.pi * mins / (24 * 60))

        # Session zones (one-hot)
        feats["zone_open"]    = ((mins >= 555) & (mins < 570)).astype(float)   # 09:15–09:30
        feats["zone_morning"] = ((mins >= 570) & (mins < 660)).astype(float)   # 09:30–11:00
        feats["zone_midday"]  = ((mins >= 660) & (mins < 780)).astype(float)   # 11:00–13:00
        feats["zone_afternoon"]= ((mins >= 780) & (mins < 870)).astype(float)  # 13:00–14:30
        feats["zone_close"]   = ((mins >= 870) & (mins <= 930)).astype(float)  # 14:30–15:30

        # Day of week
        dow = idx.dayofweek   # 0=Mon, 4=Fri
        feats["dow_sin"] = np.sin(2 * np.pi * dow / 5)
        feats["dow_cos"] = np.cos(2 * np.pi * dow / 5)
        feats["is_monday"] = (dow == 0).astype(float)
        feats["is_friday"] = (dow == 4).astype(float)

        # Opening range features
        try:
            or_df = opening_range(df)
            atr14 = atr(df, 14)
            c     = df["close"]
            or_range = (or_df["orh"] - or_df["orl"]).replace(0, np.nan)
            feats["or_range_atr"]  = or_range / atr14.replace(0, np.nan)
            feats["dist_orh"]      = (c - or_df["orh"]) / atr14.replace(0, np.nan)
            feats["dist_orl"]      = (c - or_df["orl"]) / atr14.replace(0, np.nan)
            feats["above_orh"]     = (c > or_df["orh"]).astype(float)
            feats["below_orl"]     = (c < or_df["orl"]).astype(float)
        except Exception:
            pass

        return pd.DataFrame(feats, index=df.index)

    def _candle_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Candle structure — body/wick ratios, directional bias."""
        feats = {}
        o, h, l, c = df["open"], df["high"], df["low"], df["close"]
        rng   = (h - l).replace(0, np.nan)
        body  = (c - o).abs()
        atr14 = atr(df, 14)

        feats["body_ratio"]    = body / rng           # 1=full body, 0=doji
        feats["body_norm"]     = (c - o) / atr14.replace(0, np.nan)  # signed
        feats["upper_wick"]    = (h - np.maximum(o, c)) / rng
        feats["lower_wick"]    = (np.minimum(o, c) - l) / rng
        feats["wick_ratio"]    = feats["upper_wick"] / (feats["lower_wick"] + 1e-6)

        feats["candle_green"]  = (c > o).astype(float)
        feats["candle_doji"]   = (body / rng < 0.1).astype(float)
        feats["candle_marubozu"]= (body / rng > 0.85).astype(float)

        # Consecutive green/red candles
        green = (c > o).astype(int)
        feats["consec_green"]  = green.rolling(3).sum()
        feats["consec_red"]    = (1 - green).rolling(3).sum()

        # Gap from previous close
        prev_close = c.shift(1)
        feats["gap_norm"] = (o - prev_close) / atr14.replace(0, np.nan)

        return pd.DataFrame(feats, index=df.index)

    def _crossover_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Binary crossover signals — useful for tree-based models."""
        feats = {}
        c = df["close"]

        e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
        rsi14 = rsi(c, 14)
        macd_df = macd(c)

        # EMA crossovers in last N bars
        for n in [1, 3, 5]:
            cross_up   = ((e9 > e21) & (e9.shift(n) <= e21.shift(n))).astype(float)
            cross_down = ((e9 < e21) & (e9.shift(n) >= e21.shift(n))).astype(float)
            feats[f"ema_xup_{n}"]   = cross_up
            feats[f"ema_xdown_{n}"] = cross_down

        # RSI threshold crossings
        feats["rsi_cross50_up"]   = ((rsi14 > 50) & (rsi14.shift(1) <= 50)).astype(float)
        feats["rsi_cross50_down"] = ((rsi14 < 50) & (rsi14.shift(1) >= 50)).astype(float)
        feats["rsi_oversold"]     = (rsi14 < 30).astype(float)
        feats["rsi_overbought"]   = (rsi14 > 70).astype(float)

        # MACD histogram sign change
        h = macd_df["histogram"]
        feats["macd_bull_cross"] = ((h > 0) & (h.shift(1) <= 0)).astype(float)
        feats["macd_bear_cross"] = ((h < 0) & (h.shift(1) >= 0)).astype(float)

        # Price vs EMA alignment
        feats["full_bull_align"] = ((c > e9) & (e9 > e21) & (e21 > e50)).astype(float)
        feats["full_bear_align"] = ((c < e9) & (e9 < e21) & (e21 < e50)).astype(float)

        return pd.DataFrame(feats, index=df.index)

    def _regime_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """ADX, trend direction, volatility regime."""
        feats = {}
        c     = df["close"]
        atr14 = atr(df, 14)

        adx = trend_strength(df, 14)
        feats["adx"]          = adx / 100
        feats["adx_trending"] = (adx > 25).astype(float)
        feats["adx_ranging"]  = (adx < 18).astype(float)
        feats["adx_slope"]    = adx.diff(5)

        # High volatility regime
        atr_avg = atr14.rolling(20).mean()
        feats["atr_regime"]   = atr14 / atr_avg.replace(0, np.nan)
        feats["high_vol"]     = (feats["atr_regime"] > 1.8).astype(float)

        return pd.DataFrame(feats, index=df.index)

    # ── Label generation ──────────────────────────────────────

    def generate_labels(
        self,
        df:          pd.DataFrame,
        features:    pd.DataFrame,
        cost_points: float = 15.0,
        lookahead:   int   = None,
    ) -> pd.Series:
        """
        Generate binary labels for supervised learning.
          1 = trade would have been profitable (net of costs)
          0 = trade would have lost or broken even

        Method: for each bar, look forward `lookahead` bars.
        A long "trade" profits if max(future high) - close > cost_points.
        A short "trade" profits if close - min(future low) > cost_points.
        We use the better of long/short (direction agnostic labelling).

        Args:
            cost_points: minimum move needed to cover costs (from cost model)
            lookahead:   bars to look forward (default = self.lookahead)
        """
        n = lookahead or self.lookahead
        c = df["close"].reindex(features.index)
        h = df["high"].reindex(features.index)
        l = df["low"].reindex(features.index)

        labels = pd.Series(0, index=features.index)
        for i in range(len(features) - n):
            close_i    = c.iloc[i]
            future_h   = h.iloc[i+1 : i+n+1].max()
            future_l   = l.iloc[i+1 : i+n+1].min()
            long_profit  = future_h - close_i
            short_profit = close_i - future_l
            if max(long_profit, short_profit) > cost_points:
                labels.iloc[i] = 1

        return labels
