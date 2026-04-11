# ============================================================
#  tradebot / analysis / stock_analyzer.py
#  Stock analysis agent — fundamental + technical scoring.
#
#  Usage:
#    from analysis.stock_analyzer import StockAnalyzer
#    a = StockAnalyzer("AAPL")
#    report = a.full_report()
#    print(report["summary"])
#
#  Works with yfinance — zero API keys needed.
# ============================================================

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class AnalysisReport:
    symbol:             str
    timestamp:          datetime
    # Scores (0-100)
    technical_score:    float = 0.0
    fundamental_score:  float = 0.0
    overall_score:      float = 0.0
    # Verdict
    verdict:            str = "HOLD"   # STRONG_BUY | BUY | HOLD | SELL | STRONG_SELL
    # Components
    technicals:         dict = field(default_factory=dict)
    fundamentals:       dict = field(default_factory=dict)
    risk_metrics:       dict = field(default_factory=dict)
    summary:            str = ""


class StockAnalyzer:
    """
    Comprehensive stock analysis combining technical indicators
    and fundamental data from yfinance.

    Scoring system:
      - Technical score (0-100): trend, momentum, volatility, volume
      - Fundamental score (0-100): valuation, profitability, growth, health
      - Overall = 0.5 * technical + 0.5 * fundamental
      - Verdict: mapped from overall score
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self._ticker = None
        self._df: Optional[pd.DataFrame] = None
        self._info: dict = {}

    def _load_data(self) -> None:
        """Fetch price history and fundamentals via yfinance."""
        try:
            import yfinance as yf
        except ImportError:
            raise RuntimeError("yfinance required: pip install yfinance")

        self._ticker = yf.Ticker(self.symbol)
        self._df = self._ticker.history(period="1y", interval="1d")
        if self._df.empty:
            raise ValueError(f"No data returned for {self.symbol}")

        # Normalize column names to lowercase
        self._df.columns = [c.lower() for c in self._df.columns]

        try:
            self._info = self._ticker.info or {}
        except Exception:
            self._info = {}

    # ── Technical Analysis ──────────────────────────────────────

    def _compute_technicals(self) -> tuple[float, dict]:
        """
        Score 0-100 based on:
          - Trend (EMA alignment, price vs SMA 200): 30 pts
          - Momentum (RSI, MACD): 30 pts
          - Volatility (ATR compression, Bollinger width): 20 pts
          - Volume (OBV trend, volume vs avg): 20 pts
        """
        df = self._df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        details = {}
        score = 0.0

        # ── Trend (30 pts) ──────────────────────────────
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        sma200 = close.rolling(200).mean()

        last = close.iloc[-1]
        details["price"] = round(last, 2)
        details["ema20"] = round(ema20.iloc[-1], 2)
        details["ema50"] = round(ema50.iloc[-1], 2)
        details["sma200"] = round(sma200.iloc[-1], 2) if not pd.isna(sma200.iloc[-1]) else None

        # EMA alignment: 20 > 50 = bullish
        if ema20.iloc[-1] > ema50.iloc[-1]:
            score += 10
            details["ema_alignment"] = "BULLISH"
        else:
            details["ema_alignment"] = "BEARISH"

        # Price above SMA200
        if details["sma200"] and last > details["sma200"]:
            score += 10
            details["above_sma200"] = True
        else:
            details["above_sma200"] = False

        # Trend slope (EMA50 direction over last 10 bars)
        ema50_slope = (ema50.iloc[-1] - ema50.iloc[-10]) / ema50.iloc[-10] * 100
        details["ema50_slope_pct"] = round(ema50_slope, 2)
        if ema50_slope > 1:
            score += 10
        elif ema50_slope > 0:
            score += 5

        # ── Momentum (30 pts) ───────────────────────────
        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_val = (100 - 100 / (1 + rs)).iloc[-1]
        details["rsi14"] = round(rsi_val, 1)

        if 40 <= rsi_val <= 60:
            score += 10  # neutral momentum
        elif 30 <= rsi_val < 40:
            score += 15  # oversold bounce potential
        elif 60 < rsi_val <= 70:
            score += 12  # healthy momentum
        elif rsi_val < 30:
            score += 8   # deeply oversold (risky)
        elif rsi_val > 70:
            score += 5   # overbought risk

        # MACD
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        hist = macd_line - signal_line

        details["macd"] = round(macd_line.iloc[-1], 2)
        details["macd_signal"] = round(signal_line.iloc[-1], 2)
        details["macd_hist"] = round(hist.iloc[-1], 2)

        if hist.iloc[-1] > 0 and hist.iloc[-1] > hist.iloc[-2]:
            score += 15
            details["macd_state"] = "BULLISH_ACCELERATING"
        elif hist.iloc[-1] > 0:
            score += 10
            details["macd_state"] = "BULLISH"
        elif hist.iloc[-1] < 0 and hist.iloc[-1] < hist.iloc[-2]:
            score += 0
            details["macd_state"] = "BEARISH_ACCELERATING"
        else:
            score += 5
            details["macd_state"] = "BEARISH"

        # ── Volatility (20 pts) ─────────────────────────
        atr14 = (high - low).rolling(14).mean()
        atr_pct = (atr14.iloc[-1] / last) * 100
        details["atr_pct"] = round(atr_pct, 2)

        # Bollinger bandwidth
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_width = ((sma20 + 2 * std20 - (sma20 - 2 * std20)) / sma20 * 100).iloc[-1]
        details["bb_width_pct"] = round(bb_width, 2)

        # Low volatility = good for entries
        if atr_pct < 2:
            score += 15
            details["volatility"] = "LOW"
        elif atr_pct < 3.5:
            score += 10
            details["volatility"] = "MODERATE"
        else:
            score += 5
            details["volatility"] = "HIGH"

        # BB squeeze (compression = potential breakout)
        bb_hist = ((sma20 + 2 * std20 - (sma20 - 2 * std20)) / sma20 * 100)
        bb_avg = bb_hist.rolling(50).mean().iloc[-1]
        if bb_width < bb_avg * 0.8:
            score += 5
            details["bb_squeeze"] = True
        else:
            details["bb_squeeze"] = False

        # ── Volume (20 pts) ─────────────────────────────
        vol_avg = volume.rolling(20).mean()
        vol_ratio = volume.iloc[-1] / vol_avg.iloc[-1] if vol_avg.iloc[-1] > 0 else 1
        details["volume_ratio"] = round(vol_ratio, 2)

        # OBV trend
        obv = (np.sign(close.diff()) * volume).cumsum()
        obv_slope = (obv.iloc[-1] - obv.iloc[-10]) / abs(obv.iloc[-10]) * 100 if obv.iloc[-10] != 0 else 0
        details["obv_slope_pct"] = round(obv_slope, 2)

        if obv_slope > 5 and vol_ratio > 1:
            score += 20
            details["volume_trend"] = "STRONG_ACCUMULATION"
        elif obv_slope > 0:
            score += 12
            details["volume_trend"] = "ACCUMULATION"
        elif obv_slope < -5:
            score += 2
            details["volume_trend"] = "DISTRIBUTION"
        else:
            score += 7
            details["volume_trend"] = "NEUTRAL"

        return min(100, score), details

    # ── Fundamental Analysis ────────────────────────────────────

    def _compute_fundamentals(self) -> tuple[float, dict]:
        """
        Score 0-100 based on:
          - Valuation (P/E, P/B, PEG): 25 pts
          - Profitability (margins, ROE): 25 pts
          - Growth (revenue, earnings growth): 25 pts
          - Financial health (debt/equity, current ratio): 25 pts
        """
        info = self._info
        details = {}
        score = 0.0

        # ── Valuation (25 pts) ──────────────────────────
        pe = info.get("forwardPE") or info.get("trailingPE")
        pb = info.get("priceToBook")
        peg = info.get("pegRatio")

        details["pe_ratio"] = round(pe, 2) if pe else None
        details["pb_ratio"] = round(pb, 2) if pb else None
        details["peg_ratio"] = round(peg, 2) if peg else None

        if pe:
            if pe < 15:
                score += 10
            elif pe < 25:
                score += 7
            elif pe < 40:
                score += 4
            else:
                score += 1

        if peg:
            if peg < 1:
                score += 10
            elif peg < 1.5:
                score += 7
            elif peg < 2.5:
                score += 4
            else:
                score += 1
        elif pb:
            if pb < 3:
                score += 5
            else:
                score += 2

        # ── Profitability (25 pts) ──────────────────────
        profit_margin = info.get("profitMargins")
        roe = info.get("returnOnEquity")
        op_margin = info.get("operatingMargins")

        details["profit_margin"] = f"{profit_margin*100:.1f}%" if profit_margin else None
        details["roe"] = f"{roe*100:.1f}%" if roe else None
        details["op_margin"] = f"{op_margin*100:.1f}%" if op_margin else None

        if profit_margin:
            if profit_margin > 0.20:
                score += 12
            elif profit_margin > 0.10:
                score += 8
            elif profit_margin > 0:
                score += 4

        if roe:
            if roe > 0.20:
                score += 13
            elif roe > 0.10:
                score += 9
            elif roe > 0:
                score += 4

        # ── Growth (25 pts) ─────────────────────────────
        rev_growth = info.get("revenueGrowth")
        earn_growth = info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")

        details["revenue_growth"] = f"{rev_growth*100:.1f}%" if rev_growth else None
        details["earnings_growth"] = f"{earn_growth*100:.1f}%" if earn_growth else None

        if rev_growth:
            if rev_growth > 0.25:
                score += 13
            elif rev_growth > 0.10:
                score += 9
            elif rev_growth > 0:
                score += 5
            else:
                score += 1

        if earn_growth:
            if earn_growth > 0.25:
                score += 12
            elif earn_growth > 0.10:
                score += 8
            elif earn_growth > 0:
                score += 4
            else:
                score += 1

        # ── Financial Health (25 pts) ───────────────────
        de = info.get("debtToEquity")
        current = info.get("currentRatio")
        fcf = info.get("freeCashflow")

        details["debt_to_equity"] = round(de, 2) if de else None
        details["current_ratio"] = round(current, 2) if current else None
        details["free_cashflow"] = fcf

        if de is not None:
            if de < 50:
                score += 10
            elif de < 100:
                score += 7
            elif de < 200:
                score += 3
            else:
                score += 1

        if current:
            if current > 2:
                score += 8
            elif current > 1.5:
                score += 6
            elif current > 1:
                score += 3

        if fcf and fcf > 0:
            score += 7
            details["fcf_positive"] = True
        else:
            details["fcf_positive"] = False

        return min(100, score), details

    # ── Risk Metrics ────────────────────────────────────────────

    def _compute_risk(self) -> dict:
        """Compute risk metrics from price history."""
        close = self._df["close"]
        returns = close.pct_change().dropna()

        # Annualized volatility
        ann_vol = returns.std() * np.sqrt(252) * 100

        # Max drawdown
        cummax = close.cummax()
        drawdown = (close - cummax) / cummax
        max_dd = drawdown.min() * 100

        # Sharpe (assuming 5% risk-free)
        excess = returns.mean() * 252 - 0.05
        sharpe = excess / (returns.std() * np.sqrt(252)) if returns.std() > 0 else 0

        # Beta (vs SPY if available)
        beta = None
        try:
            import yfinance as yf
            spy = yf.Ticker("SPY").history(period="1y", interval="1d")
            if not spy.empty:
                spy.columns = [c.lower() for c in spy.columns]
                spy_ret = spy["close"].pct_change().dropna()
                # Align dates
                common = returns.index.intersection(spy_ret.index)
                if len(common) > 50:
                    cov = np.cov(returns.loc[common], spy_ret.loc[common])
                    beta = round(cov[0][1] / cov[1][1], 2) if cov[1][1] > 0 else None
        except Exception:
            pass

        return {
            "annual_volatility_pct": round(ann_vol, 1),
            "max_drawdown_pct": round(max_dd, 1),
            "sharpe_ratio": round(sharpe, 2),
            "beta": beta,
            "daily_returns_std": round(returns.std() * 100, 3),
        }

    # ── Full Report ─────────────────────────────────────────────

    def full_report(self) -> AnalysisReport:
        """Run complete analysis and return structured report."""
        self._load_data()

        tech_score, tech_details = self._compute_technicals()
        fund_score, fund_details = self._compute_fundamentals()
        risk_metrics = self._compute_risk()

        overall = 0.5 * tech_score + 0.5 * fund_score

        # Verdict
        if overall >= 80:
            verdict = "STRONG_BUY"
        elif overall >= 65:
            verdict = "BUY"
        elif overall >= 45:
            verdict = "HOLD"
        elif overall >= 30:
            verdict = "SELL"
        else:
            verdict = "STRONG_SELL"

        # Summary
        name = self._info.get("shortName", self.symbol)
        sector = self._info.get("sector", "N/A")
        mktcap = self._info.get("marketCap")
        mktcap_str = f"${mktcap/1e9:.1f}B" if mktcap and mktcap >= 1e9 else (
            f"${mktcap/1e6:.0f}M" if mktcap else "N/A")

        summary = (
            f"=== {name} ({self.symbol}) ===\n"
            f"Sector: {sector} | Market Cap: {mktcap_str}\n"
            f"Price: ${tech_details.get('price', 0):.2f}\n\n"
            f"Technical Score:    {tech_score:.0f}/100\n"
            f"  Trend:      EMA {tech_details.get('ema_alignment', '?')} | SMA200: {'Above' if tech_details.get('above_sma200') else 'Below'}\n"
            f"  Momentum:   RSI={tech_details.get('rsi14', '?')} | MACD: {tech_details.get('macd_state', '?')}\n"
            f"  Volatility: {tech_details.get('volatility', '?')} (ATR {tech_details.get('atr_pct', '?')}%)\n"
            f"  Volume:     {tech_details.get('volume_trend', '?')} (ratio={tech_details.get('volume_ratio', '?')}x)\n\n"
            f"Fundamental Score:  {fund_score:.0f}/100\n"
            f"  P/E: {fund_details.get('pe_ratio', 'N/A')} | PEG: {fund_details.get('peg_ratio', 'N/A')}\n"
            f"  ROE: {fund_details.get('roe', 'N/A')} | Margin: {fund_details.get('profit_margin', 'N/A')}\n"
            f"  Rev Growth: {fund_details.get('revenue_growth', 'N/A')}\n"
            f"  D/E: {fund_details.get('debt_to_equity', 'N/A')} | FCF+: {fund_details.get('fcf_positive', 'N/A')}\n\n"
            f"Risk Metrics:\n"
            f"  Volatility: {risk_metrics.get('annual_volatility_pct', '?')}% ann.\n"
            f"  Max Drawdown: {risk_metrics.get('max_drawdown_pct', '?')}%\n"
            f"  Sharpe: {risk_metrics.get('sharpe_ratio', '?')} | Beta: {risk_metrics.get('beta', 'N/A')}\n\n"
            f"Overall Score: {overall:.0f}/100\n"
            f"Verdict: {verdict}\n"
        )

        return AnalysisReport(
            symbol=self.symbol,
            timestamp=datetime.now(),
            technical_score=round(tech_score, 1),
            fundamental_score=round(fund_score, 1),
            overall_score=round(overall, 1),
            verdict=verdict,
            technicals=tech_details,
            fundamentals=fund_details,
            risk_metrics=risk_metrics,
            summary=summary,
        )


# ── Multi-stock screener ────────────────────────────────────────

def screen_stocks(symbols: list[str]) -> pd.DataFrame:
    """
    Run analysis on multiple stocks and return a ranked DataFrame.

    Usage:
        from analysis.stock_analyzer import screen_stocks
        df = screen_stocks(["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"])
        print(df.to_string())
    """
    rows = []
    for sym in symbols:
        try:
            report = StockAnalyzer(sym).full_report()
            rows.append({
                "symbol":      sym,
                "price":       report.technicals.get("price", 0),
                "tech_score":  report.technical_score,
                "fund_score":  report.fundamental_score,
                "overall":     report.overall_score,
                "verdict":     report.verdict,
                "rsi":         report.technicals.get("rsi14", 0),
                "pe":          report.fundamentals.get("pe_ratio", None),
                "roe":         report.fundamentals.get("roe", None),
                "sharpe":      report.risk_metrics.get("sharpe_ratio", 0),
                "max_dd":      report.risk_metrics.get("max_drawdown_pct", 0),
            })
        except Exception as e:
            logger.warning(f"Analysis failed for {sym}: {e}")
            rows.append({"symbol": sym, "overall": 0, "verdict": "ERROR"})

    df = pd.DataFrame(rows).sort_values("overall", ascending=False).reset_index(drop=True)
    return df


# ── CLI entry point ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]

    if len(symbols) == 1:
        report = StockAnalyzer(symbols[0]).full_report()
        print(report.summary)
    else:
        print(f"Screening {len(symbols)} stocks...\n")
        df = screen_stocks(symbols)
        print(df.to_string(index=False))
