"""
vwap_reversion.py — VWAP Mean Reversion Strategy

Trades mean reversion around the Volume Weighted Average Price.
Best for range-bound, choppy markets.

Logic:
  BUY:  Price < VWAP by > X% AND RSI < 35 AND near lower Bollinger Band
  SELL: Price > VWAP by > X% AND RSI > 65 AND near upper Bollinger Band
  EXIT: Price returns to VWAP +/- 0.1%
"""

import pandas as pd
import ta as ta_lib

from src.strategies.base_strategy import BaseStrategy, Signal, SignalType


class VWAPReversionStrategy(BaseStrategy):
    """Mean reversion strategy using VWAP, RSI, and Bollinger Bands."""

    def __init__(self, config: dict):
        super().__init__("vwap_reversion", config)
        self._rsi_oversold = config.get("rsi_oversold", 35)
        self._rsi_overbought = config.get("rsi_overbought", 65)
        self._vwap_deviation = config.get("vwap_deviation_pct", 0.5) / 100
        self._bb_length = 20
        self._bb_std = 2.0
        self._rsi_length = 14

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate VWAP, RSI, and Bollinger Bands."""
        if df.empty or len(df) < self._bb_length:
            return df

        # VWAP — requires volume
        if "volume" in df.columns:
            try:
                vwap_indicator = ta_lib.volume.VolumeWeightedAveragePrice(
                    high=df["high"], low=df["low"], close=df["close"], volume=df["volume"]
                )
                df["vwap"] = vwap_indicator.volume_weighted_average_price()
            except Exception:
                pass

        # RSI
        rsi_indicator = ta_lib.momentum.RSIIndicator(
            close=df["close"], window=self._rsi_length)
        df["rsi"] = rsi_indicator.rsi()

        # Bollinger Bands
        bb_indicator = ta_lib.volatility.BollingerBands(
            close=df["close"], window=self._bb_length, window_dev=self._bb_std
        )
        df["bb_upper"] = bb_indicator.bollinger_hband()
        df["bb_mid"] = bb_indicator.bollinger_mavg()
        df["bb_lower"] = bb_indicator.bollinger_lband()

        return df

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        """Generate VWAP mean reversion signal."""
        default = Signal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            strategy_name=self.name,
            confidence=0.0,
        )

        if df.empty or len(df) < self._bb_length + 5:
            return default

        # Get latest values
        price = self._safe_get(df["close"])
        rsi = self._safe_get(df.get("rsi", pd.Series()))
        vwap = self._safe_get(df.get("vwap", pd.Series()))
        bb_upper = self._safe_get(df.get("bb_upper", pd.Series()))
        bb_lower = self._safe_get(df.get("bb_lower", pd.Series()))

        if price == 0 or vwap == 0:
            return default

        # Calculate deviation from VWAP
        vwap_dev = (price - vwap) / vwap

        # Bollinger Band position (0 = lower band, 1 = upper band)
        bb_range = bb_upper - bb_lower if bb_upper != bb_lower else 1
        bb_position = (price - bb_lower) / bb_range if bb_range > 0 else 0.5

        indicators = {
            "price": price,
            "vwap": vwap,
            "vwap_deviation": vwap_dev,
            "rsi": rsi,
            "bb_position": bb_position,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
        }

        # --- BUY Signal: Price below VWAP + RSI oversold + near lower BB ---
        if vwap_dev < -self._vwap_deviation and rsi < self._rsi_oversold and bb_position < 0.2:
            confidence = self._calculate_buy_confidence(
                vwap_dev, rsi, bb_position)
            stop_loss = price * 0.99  # 1% below entry
            take_profit = vwap  # Target: return to VWAP

            return Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                strategy_name=self.name,
                confidence=confidence,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"VWAP reversion BUY: price {vwap_dev*100:.1f}% below VWAP, RSI={rsi:.0f}, BB_pos={bb_position:.2f}",
                indicators=indicators,
            )

        # --- SELL Signal: Price above VWAP + RSI overbought + near upper BB ---
        if vwap_dev > self._vwap_deviation and rsi > self._rsi_overbought and bb_position > 0.8:
            confidence = self._calculate_sell_confidence(
                vwap_dev, rsi, bb_position)
            stop_loss = price * 1.01  # 1% above entry
            take_profit = vwap

            return Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                strategy_name=self.name,
                confidence=confidence,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"VWAP reversion SELL: price {vwap_dev*100:.1f}% above VWAP, RSI={rsi:.0f}, BB_pos={bb_position:.2f}",
                indicators=indicators,
            )

        return default

    def _calculate_buy_confidence(self, vwap_dev: float, rsi: float, bb_pos: float) -> float:
        """Calculate confidence for a BUY signal (0-1)."""
        confidence = 0.0

        # Deeper deviation from VWAP = higher confidence
        confidence += min(abs(vwap_dev) / 0.02, 0.35)

        # Lower RSI = higher confidence
        confidence += max(0, (self._rsi_oversold - rsi) /
                          self._rsi_oversold) * 0.35

        # Closer to lower BB = higher confidence
        confidence += max(0, (0.2 - bb_pos) / 0.2) * 0.30

        return min(confidence, 1.0)

    def _calculate_sell_confidence(self, vwap_dev: float, rsi: float, bb_pos: float) -> float:
        """Calculate confidence for a SELL signal (0-1)."""
        confidence = 0.0

        confidence += min(abs(vwap_dev) / 0.02, 0.35)
        confidence += max(0, (rsi - self._rsi_overbought) /
                          (100 - self._rsi_overbought)) * 0.35
        confidence += max(0, (bb_pos - 0.8) / 0.2) * 0.30

        return min(confidence, 1.0)
