# ============================================================
#  tradebot / ai / classifier.py
#  XGBoost signal classifier — Engine B.
#
#  Role in the system:
#    Engine A (algo) generates a signal → Engine B scores it.
#    If score < confidence_threshold → signal is suppressed.
#    If score >= threshold → signal fires with scaled position.
#
#  The classifier is NOT a price predictor. It answers:
#    "Given current market conditions, is this signal type
#     historically likely to work or fail?"
#
#  Training pipeline:
#    1. Run walk-forward backtest → collect all trades
#    2. Join each trade to its bar's features
#    3. Label: 1 = net profitable, 0 = net loss
#    4. Train XGBoost per strategy type
#    5. Evaluate on OOS (out-of-sample) portion only
#    6. Save model; load at runtime for inference
#
#  Retraining:
#    Run retrain_classifier.py every Sunday evening.
#    Or trigger when OOS accuracy drops below 52%.
# ============================================================

import logging
import pickle
from pathlib import Path
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from ai.features import FeatureEngine

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent.parent / "data" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


class SignalClassifier:
    """
    XGBoost-based signal filter.

    One model per strategy type. Each model is trained
    independently so VWAP reversion learns different
    market conditions than EMA trend-follow.

    Usage:
        clf = SignalClassifier("vwap_reversion")
        clf.train(df, trades_df)        # training phase
        score = clf.score(df)           # inference: 0.0–1.0
        approved = score >= 0.55        # threshold gate
    """

    XGBOOST_PARAMS = {
        "n_estimators":      400,
        "max_depth":         5,
        "learning_rate":     0.05,
        "subsample":         0.8,
        "colsample_bytree":  0.8,
        "min_child_weight":  5,
        "gamma":             1.0,
        "reg_alpha":         0.1,
        "reg_lambda":        1.0,
        "scale_pos_weight":  1.0,   # set to neg/pos ratio if imbalanced
        "eval_metric":       "logloss",
        "use_label_encoder": False,
        "random_state":      42,
        "n_jobs":            -1,
    }

    def __init__(
        self,
        strategy_name:        str,
        confidence_threshold: float = 0.55,
    ):
        self.strategy        = strategy_name
        self.threshold       = confidence_threshold
        self.model           = None
        self.feature_names:  list[str] = []
        self.feature_engine  = FeatureEngine()
        self._model_path     = MODEL_DIR / f"clf_{strategy_name}.pkl"
        self._meta_path      = MODEL_DIR / f"clf_{strategy_name}_meta.json"
        self.is_trained      = False

        # Try loading existing model
        self._load()

    # ── Training ──────────────────────────────────────────────

    def train(
        self,
        df:          pd.DataFrame,
        trades_df:   pd.DataFrame,
        eval_split:  float = 0.2,
    ) -> dict:
        """
        Train the classifier.

        Args:
            df:         OHLCV dataframe (full history)
            trades_df:  DataFrame of backtest trades with columns:
                        entry_time, net_pnl, strategy
            eval_split: fraction of data held out for evaluation

        Returns dict of training metrics.
        """
        try:
            from xgboost import XGBClassifier
        except ImportError:
            logger.error("xgboost not installed. Run: pip install xgboost")
            return {}

        # ── Build feature matrix ──────────────────────────────
        logger.info(f"[{self.strategy}] Building features for {len(df)} bars...")
        X, meta = self.feature_engine.build_features(df)

        # ── Generate labels from trade results ────────────────
        # Label each bar 1 if a trade entered around that bar was profitable
        y = pd.Series(0, index=X.index)
        strat_trades = trades_df[trades_df["strategy"] == self.strategy].copy()

        if len(strat_trades) < 50:
            logger.warning(f"[{self.strategy}] Only {len(strat_trades)} trades — need 50+ to train")
            return {"error": "insufficient_trades"}

        strat_trades["entry_time"] = pd.to_datetime(strat_trades["entry_time"])
        for _, trade in strat_trades.iterrows():
            et = trade["entry_time"]
            label = 1 if trade["net_pnl"] > 0 else 0
            # Find closest bar index
            idx_pos = X.index.searchsorted(et)
            if 0 <= idx_pos < len(X):
                y.iloc[idx_pos] = label

        # Keep only labelled rows
        labelled_mask = (
            pd.Series(False, index=X.index)
        )
        strat_trades["entry_time"] = pd.to_datetime(strat_trades["entry_time"])
        for _, trade in strat_trades.iterrows():
            pos = X.index.searchsorted(trade["entry_time"])
            if 0 <= pos < len(X):
                labelled_mask.iloc[pos] = True

        X_lab = X[labelled_mask].fillna(0)
        y_lab = y[labelled_mask]

        if len(X_lab) < 30:
            logger.warning(f"[{self.strategy}] Not enough labelled bars for training")
            return {"error": "insufficient_labelled"}

        # ── Train/eval split (time-based, not random) ─────────
        split_idx  = int(len(X_lab) * (1 - eval_split))
        X_train, X_eval = X_lab.iloc[:split_idx], X_lab.iloc[split_idx:]
        y_train, y_eval = y_lab.iloc[:split_idx], y_lab.iloc[split_idx:]

        pos_weight = max(1.0, (y_train == 0).sum() / max(1, (y_train == 1).sum()))
        params = {**self.XGBOOST_PARAMS, "scale_pos_weight": pos_weight}

        logger.info(f"[{self.strategy}] Training XGBoost | train={len(X_train)} eval={len(X_eval)} | pos_weight={pos_weight:.2f}")

        self.model = XGBClassifier(**params)
        self.model.fit(
            X_train, y_train,
            eval_set=[(X_eval, y_eval)],
            verbose=False,
        )
        self.feature_names = list(X_lab.columns)
        self.is_trained    = True

        # ── Evaluate ──────────────────────────────────────────
        from sklearn.metrics import accuracy_score, roc_auc_score, precision_score, recall_score
        y_pred     = self.model.predict(X_eval)
        y_prob     = self.model.predict_proba(X_eval)[:, 1]
        accuracy   = accuracy_score(y_eval, y_pred)
        auc        = roc_auc_score(y_eval, y_prob) if y_eval.nunique() > 1 else 0.5
        precision  = precision_score(y_eval, y_pred, zero_division=0)
        recall     = recall_score(y_eval, y_pred, zero_division=0)

        # Feature importance (top 15)
        importance = pd.Series(
            self.model.feature_importances_,
            index=self.feature_names
        ).sort_values(ascending=False).head(15)

        metrics = {
            "strategy":    self.strategy,
            "train_bars":  len(X_train),
            "eval_bars":   len(X_eval),
            "accuracy":    round(accuracy, 4),
            "auc":         round(auc, 4),
            "precision":   round(precision, 4),
            "recall":      round(recall, 4),
            "top_features": importance.to_dict(),
            "trained_at":  datetime.now().isoformat(),
            "passed_gate": accuracy >= 0.52 and auc >= 0.55,
        }

        logger.info(
            f"[{self.strategy}] Training complete | "
            f"acc={accuracy:.3f} auc={auc:.3f} prec={precision:.3f} rec={recall:.3f}"
        )

        self._save(metrics)
        return metrics

    # ── Inference ─────────────────────────────────────────────

    def score(self, df: pd.DataFrame) -> float:
        """
        Score current bar (last completed candle).
        Returns probability 0.0–1.0 that a signal here is a winner.
        Returns 0.5 (neutral) if model not trained or features unavailable.
        """
        if not self.is_trained or self.model is None:
            return 0.5

        features = self.feature_engine.get_current_features(df)
        if features is None:
            return 0.5

        # Align features to training columns
        feat_aligned = features.reindex(self.feature_names).fillna(0)
        X = pd.DataFrame([feat_aligned])

        try:
            prob = self.model.predict_proba(X)[0][1]
            return float(np.clip(prob, 0.0, 1.0))
        except Exception as e:
            logger.warning(f"[{self.strategy}] Score error: {e}")
            return 0.5

    def approve(self, df: pd.DataFrame) -> tuple[bool, float]:
        """
        Returns (approved, score).
        approved = True if score >= confidence_threshold.
        """
        score    = self.score(df)
        approved = score >= self.threshold
        return approved, round(score, 4)

    # ── Feature importance ────────────────────────────────────

    def top_features(self, n: int = 20) -> pd.Series:
        if self.model is None:
            return pd.Series(dtype=float)
        return pd.Series(
            self.model.feature_importances_,
            index=self.feature_names
        ).sort_values(ascending=False).head(n)

    # ── Persistence ───────────────────────────────────────────

    def _save(self, meta: dict) -> None:
        import json
        with open(self._model_path, "wb") as f:
            pickle.dump({
                "model":         self.model,
                "feature_names": self.feature_names,
                "threshold":     self.threshold,
            }, f)
        with open(self._meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        logger.info(f"[{self.strategy}] Model saved → {self._model_path}")

    def _load(self) -> None:
        if not self._model_path.exists():
            return
        try:
            with open(self._model_path, "rb") as f:
                data = pickle.load(f)
            self.model         = data["model"]
            self.feature_names = data["feature_names"]
            self.threshold     = data.get("threshold", self.threshold)
            self.is_trained    = True
            logger.info(f"[{self.strategy}] Model loaded from {self._model_path}")
        except Exception as e:
            logger.warning(f"[{self.strategy}] Could not load model: {e}")

    def model_info(self) -> dict:
        import json
        if self._meta_path.exists():
            return json.loads(self._meta_path.read_text())
        return {"trained": False}


class MultiStrategyClassifier:
    """
    Manages one SignalClassifier per strategy.
    Single entry point for the execution engine.
    """

    def __init__(self, strategy_names: list[str], threshold: float = 0.55):
        self.classifiers = {
            name: SignalClassifier(name, threshold)
            for name in strategy_names
        }

    def train_all(self, df: pd.DataFrame, trades_df: pd.DataFrame) -> dict:
        results = {}
        for name, clf in self.classifiers.items():
            logger.info(f"Training classifier: {name}")
            results[name] = clf.train(df, trades_df)
        return results

    def approve_signal(self, strategy_name: str, df: pd.DataFrame) -> tuple[bool, float]:
        clf = self.classifiers.get(strategy_name)
        if clf is None:
            return True, 0.5   # no classifier = always approve
        return clf.approve(df)

    def all_trained(self) -> bool:
        return all(c.is_trained for c in self.classifiers.values())

    def status(self) -> dict:
        return {name: clf.model_info() for name, clf in self.classifiers.items()}
