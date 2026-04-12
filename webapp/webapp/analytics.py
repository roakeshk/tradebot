from __future__ import annotations

import math
import random
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .config import DEFAULT_SIGNAL_THRESHOLD, INSTRUMENT_FUNDAMENTALS, SYMBOL_MARKET_MAP, WORLD_MARKETS


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))
    avg_gain = _mean(gains[-period:])
    avg_loss = _mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _stochastic(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> tuple[float, float]:
    if len(closes) < period:
        return 50.0, 50.0
    highest = max(highs[-period:])
    lowest = min(lows[-period:])
    denom = highest - lowest or 1.0
    k_value = ((closes[-1] - lowest) / denom) * 100
    k_values = []
    for index in range(period, len(closes) + 1):
        period_high = max(highs[index - period:index])
        period_low = min(lows[index - period:index])
        period_denom = period_high - period_low or 1.0
        k_values.append(((closes[index - 1] - period_low) / period_denom) * 100)
    d_value = _mean(k_values[-3:]) if k_values else k_value
    return k_value, d_value


def _true_ranges(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    ranges: list[float] = []
    for index in range(1, len(closes)):
        ranges.append(max(highs[index] - lows[index], abs(highs[index] - closes[index - 1]), abs(lows[index] - closes[index - 1])))
    return ranges


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    true_ranges = _true_ranges(highs, lows, closes)
    if not true_ranges:
        return 0.0
    return _mean(true_ranges[-period:])


def _macd(values: list[float]) -> tuple[float, float, float]:
    if len(values) < 26:
        return 0.0, 0.0, 0.0
    fast = _ema_series(values, 12)
    slow = _ema_series(values, 26)
    macd_line = [fast_value - slow_value for fast_value, slow_value in zip(fast[-len(slow):], slow)]
    signal = _ema_series(macd_line, 9)
    return macd_line[-1], signal[-1], macd_line[-1] - signal[-1]


def _bollinger(values: list[float], period: int = 20, width: float = 2.0) -> tuple[float, float, float, float]:
    if len(values) < period:
        last = values[-1] if values else 0.0
        return last, last, last, 0.0
    sample = values[-period:]
    middle = _mean(sample)
    variance = _mean([(value - middle) ** 2 for value in sample])
    deviation = math.sqrt(variance)
    upper = middle + width * deviation
    lower = middle - width * deviation
    bandwidth = ((upper - lower) / middle * 100) if middle else 0.0
    return upper, middle, lower, bandwidth


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if len(closes) <= period + 1:
        return 18.0
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    true_ranges = _true_ranges(highs, lows, closes)
    for index in range(1, len(highs)):
        up_move = highs[index] - highs[index - 1]
        down_move = lows[index - 1] - lows[index]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    atr = _mean(true_ranges[-period:]) or 1.0
    plus_di = 100 * (_mean(plus_dm[-period:]) / atr)
    minus_di = 100 * (_mean(minus_dm[-period:]) / atr)
    denom = plus_di + minus_di or 1.0
    dx = abs(plus_di - minus_di) / denom * 100
    return dx


@dataclass
class BufferSnapshot:
    symbol: str
    market_id: str
    bars: list[dict[str, Any]]


class PriceBuffer:
    def __init__(self, maxlen: int = 300) -> None:
        self.maxlen = maxlen
        self._buffers: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=self.maxlen))
        self._lock = threading.Lock()

    def seed_symbol(self, symbol: str, market_id: str, base_price: float, bars: int = 220) -> None:
        with self._lock:
            if self._buffers[symbol]:
                return
            price = base_price
            timestamp = datetime.now() - timedelta(minutes=bars * 5)
            volatility = 0.004 if WORLD_MARKETS[market_id]["type"] == "equity" else 0.002
            if WORLD_MARKETS[market_id]["type"] == "crypto":
                volatility = 0.012
            for _ in range(bars):
                open_price = price
                drift = random.gauss(0, volatility)
                close_price = max(0.0001, open_price * (1 + drift))
                high_price = max(open_price, close_price) * (1 + abs(random.gauss(0, volatility / 2)))
                low_price = min(open_price, close_price) * (1 - abs(random.gauss(0, volatility / 2)))
                volume = abs(random.gauss(100000, 35000))
                self._buffers[symbol].append(
                    {
                        "symbol": symbol,
                        "market_id": market_id,
                        "ts": timestamp.isoformat(),
                        "open": round(open_price, 6),
                        "high": round(high_price, 6),
                        "low": round(max(0.0001, low_price), 6),
                        "close": round(close_price, 6),
                        "volume": round(volume, 2),
                        "source": "seed",
                    }
                )
                price = close_price
                timestamp += timedelta(minutes=5)

    def append(self, bar: dict[str, Any]) -> None:
        with self._lock:
            self._buffers[bar["symbol"]].append(bar)

    def latest(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            buffer = self._buffers.get(symbol)
            return dict(buffer[-1]) if buffer else None

    def series(self, symbol: str, bars: int = 200) -> BufferSnapshot:
        with self._lock:
            buffer = list(self._buffers.get(symbol, []))[-bars:]
        market_id = SYMBOL_MARKET_MAP.get(symbol, "US")
        return BufferSnapshot(symbol=symbol, market_id=market_id, bars=buffer)


class TechnicalEngine:
    def compute_all(self, snapshot: BufferSnapshot) -> dict[str, Any]:
        bars = snapshot.bars
        if not bars:
            return {
                "trend": {},
                "momentum": {},
                "volatility": {},
                "volume": {},
                "scores": {"technical": 50, "trend": 50, "momentum": 50, "volatility": 50, "volume": 50},
            }
        closes = [float(bar["close"]) for bar in bars]
        highs = [float(bar["high"]) for bar in bars]
        lows = [float(bar["low"]) for bar in bars]
        volumes = [float(bar["volume"]) for bar in bars]
        ema9 = _ema_series(closes, 9)[-1]
        ema21 = _ema_series(closes, 21)[-1]
        ema50 = _ema_series(closes, 50)[-1]
        ema200 = _ema_series(closes, 200)[-1] if len(closes) >= 200 else _mean(closes)
        sma20 = _mean(closes[-20:])
        sma50 = _mean(closes[-50:])
        rsi = _rsi(closes)
        macd_line, macd_signal, macd_hist = _macd(closes)
        stoch_k, stoch_d = _stochastic(highs, lows, closes)
        atr = _atr(highs, lows, closes)
        adx = _adx(highs, lows, closes)
        bb_upper, bb_mid, bb_lower, bb_width = _bollinger(closes)
        typical_prices = [(high + low + close) / 3 for high, low, close in zip(highs[-20:], lows[-20:], closes[-20:])]
        vwap = sum(price * volume for price, volume in zip(typical_prices, volumes[-20:])) / (sum(volumes[-20:]) or 1.0)
        avg_volume = _mean(volumes[-20:])
        relative_volume = volumes[-1] / avg_volume if avg_volume else 1.0
        trend_score = _clamp(50 + (10 if ema9 > ema21 else -10) + (10 if ema21 > ema50 else -10) + (adx - 20), 0, 100)
        momentum_score = _clamp(50 + (rsi - 50) + macd_hist * 120 + (stoch_k - 50) * 0.4, 0, 100)
        volatility_score = _clamp(50 + min(bb_width, 25) - max(0, 12 - atr / (closes[-1] or 1) * 100 * 10), 0, 100)
        volume_score = _clamp(45 + relative_volume * 18, 0, 100)
        technical_score = round(_clamp((trend_score + momentum_score + volatility_score + volume_score) / 4, 0, 100), 1)
        return {
            "trend": {
                "ema9": round(ema9, 4),
                "ema21": round(ema21, 4),
                "ema50": round(ema50, 4),
                "ema200": round(ema200, 4),
                "sma20": round(sma20, 4),
                "sma50": round(sma50, 4),
                "adx": round(adx, 2),
                "vwap": round(vwap, 4),
                "supertrend": "bullish" if closes[-1] > ema21 else "bearish",
            },
            "momentum": {
                "rsi": round(rsi, 2),
                "macd": round(macd_line, 4),
                "macd_signal": round(macd_signal, 4),
                "macd_hist": round(macd_hist, 4),
                "stoch_k": round(stoch_k, 2),
                "stoch_d": round(stoch_d, 2),
            },
            "volatility": {
                "atr": round(atr, 4),
                "bb_upper": round(bb_upper, 4),
                "bb_mid": round(bb_mid, 4),
                "bb_lower": round(bb_lower, 4),
                "bb_width": round(bb_width, 2),
                "squeeze": bb_width < 4.5,
            },
            "volume": {
                "avg_volume": round(avg_volume, 2),
                "last_volume": round(volumes[-1], 2),
                "relative_volume": round(relative_volume, 2),
                "obv_bias": "accumulation" if closes[-1] >= closes[-2] else "distribution",
            },
            "scores": {
                "technical": technical_score,
                "trend": round(trend_score, 1),
                "momentum": round(momentum_score, 1),
                "volatility": round(volatility_score, 1),
                "volume": round(volume_score, 1),
            },
            "latest_price": round(closes[-1], 4),
        }


class FundamentalAnalyzer:
    def analyze(self, symbol: str) -> dict[str, Any]:
        base = dict(INSTRUMENT_FUNDAMENTALS[symbol])
        kind = base["asset_kind"]
        if kind == "equity":
            valuation = _clamp(100 - ((base["pe"] or 18) - 10) * 4 - ((base["pb"] or 2.0) - 1.5) * 8, 0, 100)
            profitability = _clamp(base["roe"] * 3 + base["profit_margin"] * 1.8, 0, 100)
            growth = _clamp(base["revenue_growth"] * 2 + base["earnings_growth"] * 2.2, 0, 100)
            health = _clamp(100 - base["debt_to_equity"] * 35 + base["current_ratio"] * 10, 0, 100)
        elif kind == "forex":
            valuation = 50.0
            profitability = 50.0
            growth = 62.0
            health = 64.0
        else:
            valuation = 58.0
            profitability = 52.0
            growth = _clamp(base["revenue_growth"] * 2.4, 0, 100)
            health = 61.0
        score = round((valuation + profitability + growth + health) / 4, 1)
        verdict = "STRONG" if score >= 75 else "FAVORABLE" if score >= 60 else "NEUTRAL" if score >= 45 else "WEAK"
        return {
            "details": base,
            "scores": {
                "valuation": round(valuation, 1),
                "profitability": round(profitability, 1),
                "growth": round(growth, 1),
                "health": round(health, 1),
                "fundamental": score,
            },
            "verdict": verdict,
        }


class SentimentEngine:
    def analyze(self, symbol: str, technicals: dict[str, Any], fundamentals: dict[str, Any]) -> dict[str, Any]:
        seed = int(datetime.now().strftime("%Y%m%d")) + sum(ord(char) for char in symbol)
        rng = random.Random(seed)
        technical_bias = technicals["scores"]["technical"] - 50
        fundamental_bias = fundamentals["scores"]["fundamental"] - 50
        sources = {
            "Technical Momentum": _clamp(technical_bias * 1.5 + rng.uniform(-8, 8), -100, 100),
            "Volume Flow": _clamp((technicals["scores"]["volume"] - 50) * 1.8 + rng.uniform(-10, 10), -100, 100),
            "Market Breadth": _clamp(technical_bias + rng.uniform(-14, 14), -100, 100),
            "Institutional Flow": _clamp(fundamental_bias * 1.4 + rng.uniform(-12, 12), -100, 100),
        }
        aggregate = round(_mean(list(sources.values())), 1)
        label = "Bullish" if aggregate >= 20 else "Bearish" if aggregate <= -20 else "Neutral"
        headlines = [
            {
                "symbol": symbol,
                "ts": datetime.now().isoformat(),
                "sentiment": aggregate,
                "source": "TradeBot News Engine",
                "headline": template.format(symbol=symbol),
                "market_id": SYMBOL_MARKET_MAP[symbol],
            }
            for template in self._headline_templates(symbol, label)
        ]
        return {"aggregate": aggregate, "label": label, "sources": sources, "headlines": headlines}

    def _headline_templates(self, symbol: str, label: str) -> list[str]:
        market_type = WORLD_MARKETS[SYMBOL_MARKET_MAP[symbol]]["type"]
        if market_type == "forex":
            bias = "hawkish" if label == "Bullish" else "dovish" if label == "Bearish" else "balanced"
            return [
                f"{{symbol}} reacts to {bias} central bank commentary",
                "Dealer desks report positioning shift in {symbol}",
            ]
        if market_type == "crypto":
            bias = "ETF inflow" if label == "Bullish" else "profit-taking" if label == "Bearish" else "range-bound flows"
            return [
                f"{{symbol}} sees {bias} across major venues",
                "On-chain activity update keeps {symbol} traders focused on momentum",
            ]
        return [
            "{symbol} sentiment improves after sector rotation update",
            "Desk chatter highlights institutional positioning in {symbol}",
        ]


class SignalGenerator:
    def generate(
        self,
        symbol: str,
        technicals: dict[str, Any],
        fundamentals: dict[str, Any],
        sentiment: dict[str, Any],
    ) -> dict[str, Any]:
        latest = technicals["latest_price"]
        trend = technicals["scores"]["trend"]
        momentum = technicals["scores"]["momentum"]
        volatility = technicals["scores"]["volatility"]
        volume = technicals["scores"]["volume"]
        fundamental = fundamentals["scores"]["fundamental"]
        sentiment_score = sentiment["aggregate"] + 50
        confidence = round(_clamp(trend * 0.26 + momentum * 0.22 + volatility * 0.16 + volume * 0.12 + fundamental * 0.14 + sentiment_score * 0.10, 0, 100), 1)
        direction = "LONG" if confidence >= 50 else "SHORT"
        atr = technicals["volatility"]["atr"] or latest * 0.01
        risk_unit = max(atr * 1.2, latest * 0.004)
        if direction == "LONG":
            stop_loss = latest - risk_unit
            target_price = latest + risk_unit * 2.1
        else:
            stop_loss = latest + risk_unit
            target_price = latest - risk_unit * 2.1
        rationale_parts = [
            f"trend {trend:.0f}",
            f"momentum {momentum:.0f}",
            f"fundamentals {fundamental:.0f}",
            f"sentiment {sentiment['aggregate']:.0f}",
        ]
        return {
            "symbol": symbol,
            "market_id": SYMBOL_MARKET_MAP[symbol],
            "ts": datetime.now().isoformat(),
            "direction": direction,
            "confidence": confidence,
            "entry_price": round(latest, 4),
            "stop_loss": round(stop_loss, 4),
            "target_price": round(target_price, 4),
            "risk_reward": round(abs(target_price - latest) / max(abs(latest - stop_loss), 0.0001), 2),
            "automation_ready": confidence >= DEFAULT_SIGNAL_THRESHOLD,
            "status": "watchlist" if confidence < DEFAULT_SIGNAL_THRESHOLD else "ready",
            "rationale": ", ".join(rationale_parts),
        }


class InstrumentAnalyzer:
    def __init__(self, buffer: PriceBuffer) -> None:
        self.buffer = buffer
        self.technical_engine = TechnicalEngine()
        self.fundamental_analyzer = FundamentalAnalyzer()
        self.sentiment_engine = SentimentEngine()
        self.signal_generator = SignalGenerator()

    def analyze(self, symbol: str) -> dict[str, Any]:
        snapshot = self.buffer.series(symbol)
        technicals = self.technical_engine.compute_all(snapshot)
        fundamentals = self.fundamental_analyzer.analyze(symbol)
        sentiment = self.sentiment_engine.analyze(symbol, technicals, fundamentals)
        signal = self.signal_generator.generate(symbol, technicals, fundamentals, sentiment)
        workflow = [
            {"step": "Instrument selected", "status": "done", "detail": symbol},
            {"step": "Sentiment analysis", "status": "done", "detail": sentiment["label"]},
            {"step": "Fundamental analysis", "status": "done", "detail": fundamentals["verdict"]},
            {"step": "Realtime news analysis", "status": "done", "detail": f"{len(sentiment['headlines'])} items"},
            {"step": "Trend chart analysis", "status": "done", "detail": f"technical {technicals['scores']['technical']}"},
            {"step": "Entry / exit prediction", "status": "done", "detail": f"{signal['direction']} {signal['confidence']}"},
            {"step": "Automated execution readiness", "status": "ready" if signal["automation_ready"] else "watch", "detail": signal["status"]},
        ]
        latest_bar = snapshot.bars[-1] if snapshot.bars else {}
        return {
            "symbol": symbol,
            "market_id": snapshot.market_id,
            "market_name": WORLD_MARKETS[snapshot.market_id]["name"],
            "latest_bar": latest_bar,
            "technicals": technicals,
            "fundamentals": fundamentals,
            "sentiment": sentiment,
            "signal": signal,
            "workflow": workflow,
            "price_history": snapshot.bars[-160:],
        }