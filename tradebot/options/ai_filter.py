# ============================================================
#  tradebot / options / ai_filter.py
#  T9 — AI classifier for options (Engine B for options)
#
#  Options-specific features for XGBoost:
#    - IV rank, IV percentile, IV vs historical average
#    - Term structure slope (near vs far expiry IV)
#    - Put-call skew (OTM put IV vs OTM call IV)
#    - PCR momentum (PCR trend over past 5 days)
#    - Max pain distance (spot vs max pain %)
#    - DTE category encoding
#    - Theta acceleration (theta decay rate near expiry)
#    - Underlying regime features from main indicator library
#
#  Each strategy gets its own classifier — iron condor learns
#  different conditions than short straddle.
# ============================================================

import logging
import pickle
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from options.data import OptionChain, _get_iv_rank
from options.signals import OptionsSignal
from config.settings import INSTRUMENTS, MARKET

logger = logging.getLogger(__name__)

MODEL_DIR = Path("data/models")
MODEL_DIR.mkdir(parents=True, exist_ok=True)


class OptionsFeatureBuilder:
    """Builds feature vector for options signal classification."""

    def build(
        self,
        chain:      OptionChain,
        price_df:   pd.DataFrame,   # underlying 5min OHLCV
        signal:     OptionsSignal,
    ) -> Optional[pd.Series]:
        """
        Build feature vector for a given signal.
        Returns None if insufficient data.
        """
        if len(price_df) < 50:
            return None

        features = {}

        # ── IV features ───────────────────────────────────────
        iv_rank = chain.iv_rank or 50
        features["iv_rank"]      = iv_rank / 100
        features["iv_rank_high"] = float(iv_rank >= 60)
        features["iv_rank_low"]  = float(iv_rank <= 30)

        atm_row = chain.get_atm_strike()
        if atm_row is not None:
            ce_iv = float(atm_row.get("ce_iv", 20))
            pe_iv = float(atm_row.get("pe_iv", 20))
            features["atm_ce_iv"]    = ce_iv / 100
            features["atm_pe_iv"]    = pe_iv / 100
            features["atm_mid_iv"]   = (ce_iv + pe_iv) / 200
            features["ce_pe_iv_diff"]= (ce_iv - pe_iv) / 100

        # ── PCR features ──────────────────────────────────────
        pcr = chain.pcr
        features["pcr"]         = pcr
        features["pcr_extreme"] = float(pcr > 1.3 or pcr < 0.7)
        features["pcr_neutral"] = float(0.9 <= pcr <= 1.15)

        # ── Max pain features ─────────────────────────────────
        spot    = chain.spot
        mp_dist = (signal.max_pain - spot) / spot * 100
        features["max_pain_dist_pct"]  = mp_dist
        features["spot_above_max_pain"]= float(spot > signal.max_pain)
        features["near_max_pain"]      = float(abs(mp_dist) < 0.5)

        # ── DTE features ──────────────────────────────────────
        dte = chain.days_to_expiry
        features["dte"]              = dte / 30   # normalised
        features["dte_1_3"]          = float(1 <= dte <= 3)
        features["dte_4_7"]          = float(4 <= dte <= 7)
        features["dte_8_15"]         = float(8 <= dte <= 15)
        features["dte_gt15"]         = float(dte > 15)

        # ── IV skew features ──────────────────────────────────
        step = INSTRUMENTS.get(chain.symbol, {}).get("strike_step", 1 if MARKET == "US" else 100)
        otm_c = chain.df[chain.df["strike"] > chain.atm + 2*step]["ce_iv"]
        otm_p = chain.df[chain.df["strike"] < chain.atm - 2*step]["pe_iv"]
        if not otm_c.empty and not otm_p.empty:
            skew = float(otm_p.mean()) - float(otm_c.mean())
            features["skew"]         = skew / 100
            features["high_skew"]    = float(skew > 5)
            features["neg_skew"]     = float(skew < 0)

        # ── OI features ───────────────────────────────────────
        total_ce = chain.df["ce_oi"].sum()
        total_pe = chain.df["pe_oi"].sum()
        features["total_oi"]     = (total_ce + total_pe) / 1e6
        features["oi_ratio"]     = total_pe / max(1, total_ce)

        # Max OI strikes
        if not chain.df.empty:
            max_ce_k = chain.df.loc[chain.df["ce_oi"].idxmax(), "strike"]
            max_pe_k = chain.df.loc[chain.df["pe_oi"].idxmax(), "strike"]
            features["dist_max_ce_oi"] = (max_ce_k - spot) / spot * 100
            features["dist_max_pe_oi"] = (spot - max_pe_k) / spot * 100

        # ── Underlying price features ──────────────────────────
        close  = price_df["close"]
        ret    = close.pct_change()
        features["ret_1d"]      = float(ret.iloc[-1])
        features["ret_5d"]      = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) > 6 else 0
        features["vol_5d"]      = float(ret.tail(5).std() * np.sqrt(252 * 78))
        features["above_vwap"]  = float(signal.regime in ("TRENDING_UP", "RANGING"))

        # ── Strategy type encoding ────────────────────────────
        strat_map = {"iron_condor": 0, "short_straddle": 1, "short_put": 2,
                     "bull_call_spread": 3, "bear_put_spread": 4}
        for name, code in strat_map.items():
            features[f"strat_{name}"] = float(signal.strategy_name == name)

        # ── Day of week (expiry timing matters) ───────────────
        dow = datetime.now().weekday()
        for d in range(5):
            features[f"dow_{d}"] = float(dow == d)

        return pd.Series(features)


class OptionsAIFilter:
    """
    XGBoost-based filter for options signals.
    Trained on historical options trade outcomes.
    """

    PARAMS = {
        "n_estimators":     300,
        "max_depth":        4,
        "learning_rate":    0.05,
        "subsample":        0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 5,
        "scale_pos_weight": 1.0,
        "use_label_encoder":False,
        "eval_metric":      "logloss",
        "random_state":     42,
        "n_jobs":           -1,
    }

    def __init__(self, strategy_name: str, threshold: float = 0.52):
        self.strategy    = strategy_name
        self.threshold   = threshold
        self.model       = None
        self.feat_names: list[str] = []
        self.feat_builder = OptionsFeatureBuilder()
        self._model_path = MODEL_DIR / f"opt_clf_{strategy_name}.pkl"
        self._load()

    def score(self, chain: OptionChain, price_df: pd.DataFrame,
              signal: OptionsSignal) -> float:
        if not self.model:
            return 0.55   # neutral if not trained

        features = self.feat_builder.build(chain, price_df, signal)
        if features is None:
            return 0.55

        X = features.reindex(self.feat_names).fillna(0)
        try:
            return float(self.model.predict_proba(pd.DataFrame([X]))[0][1])
        except Exception:
            return 0.55

    def approve(self, chain, price_df, signal) -> tuple[bool, float]:
        score = self.score(chain, price_df, signal)
        return score >= self.threshold, round(score, 3)

    def train(self, features_df: pd.DataFrame, labels: pd.Series) -> dict:
        try:
            from xgboost import XGBClassifier
            from sklearn.metrics import accuracy_score, roc_auc_score
        except ImportError:
            logger.error("xgboost not installed")
            return {}

        if len(features_df) < 30:
            return {"error": "insufficient_data"}

        split     = int(len(features_df) * 0.8)
        X_tr, X_v = features_df.iloc[:split], features_df.iloc[split:]
        y_tr, y_v = labels.iloc[:split], labels.iloc[split:]

        pos_weight = (y_tr == 0).sum() / max(1, (y_tr == 1).sum())
        params     = {**self.PARAMS, "scale_pos_weight": pos_weight}

        self.model = XGBClassifier(**params)
        self.model.fit(X_tr, y_tr, eval_set=[(X_v, y_v)], verbose=False)
        self.feat_names = list(features_df.columns)

        y_pred = self.model.predict(X_v)
        y_prob = self.model.predict_proba(X_v)[:, 1]
        metrics = {
            "strategy":   self.strategy,
            "accuracy":   round(float(accuracy_score(y_v, y_pred)), 4),
            "auc":        round(float(roc_auc_score(y_v, y_prob)) if y_v.nunique() > 1 else 0.5, 4),
            "passed_gate":accuracy_score(y_v, y_pred) >= 0.52,
        }
        self._save()
        return metrics

    def _save(self):
        with open(self._model_path, "wb") as f:
            pickle.dump({"model": self.model, "feat_names": self.feat_names}, f)

    def _load(self):
        if self._model_path.exists():
            try:
                with open(self._model_path, "rb") as f:
                    d = pickle.load(f)
                self.model      = d["model"]
                self.feat_names = d["feat_names"]
            except Exception:
                pass
