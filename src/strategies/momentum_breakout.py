"""
momentum_breakout.py — Trend-Following Momentum Strategy

Captures strong directional moves using EMA crossovers,
MACD confirmation, and volume surges.

Logic:
  BUY:  EMA9 > EMA21 AND MACD histogram positive AND RSI > 50 AND volume > 1.5x avg
  SELL: EMA9 < EMA21 AND MACD histogram negative AND RSI < 50
  Requires 2/3 indicator confirmation
"""

import pandas as pd
import ta as ta_lib

from src.strategies.base_strategy import BaseStrategy, Signal, SignalType


class MomentumBreakoutStrategy(BaseStrategy):
    """Trend-following momentum strategy with multi-indicator confirmation."""

    def __init__(self, config: dict):
        super().__init__("momentum_breakout", config)
        self._ema_fast = config.get("ema_fast", 9)
        self._ema_slow = config.get("ema_slow", 21)
        self._macd_fast = config.get("macd_fast", 12)
        self._macd_slow = config.get("macd_slow", 26)
        self._macd_signal = config.get("macd_signal", 9)
        self._volume_multiplier = config.get("volume_multiplier", 1.5)
        self._rsi_length = 14
        self._min_confirmations = 2  # Need 2 out of 3 indicators

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMA crossover, MACD, RSI, and volume indicators."""
        if df.empty or len(df) < self._macd_slow + self._macd_signal:
            return df

        # EMAs
        ema_fast_ind = ta_lib.trend.EMAIndicator(
            close=df["close"], window=self._ema_fast)
        ema_slow_ind = ta_lib.trend.EMAIndicator(
            close=df["close"], window=self._ema_slow)
        df["ema_fast"] = ema_fast_ind.ema_indicator()
        df["ema_slow"] = ema_slow_ind.ema_indicator()

        # MACD
        macd_ind = ta_lib.trend.MACD(
            close=df["close"],
            window_fast=self._macd_fast,
            window_slow=self._macd_slow,
            window_sign=self._macd_signal,
        )
        df["macd_line"] = macd_ind.macd()
        df["macd_signal"] = macd_ind.macd_signal()
        df["macd_hist"] = macd_ind.macd_diff()

        # RSI
        rsi_ind = ta_lib.momentum.RSIIndicator(
            close=df["close"], window=self._rsi_length)
        df["rsi"] = rsi_ind.rsi()

        # Volume moving average
        if "volume" in df.columns:
            vol_sma = ta_lib.trend.SMAIndicator(
                close=df["volume"].astype(float), window=20)
            df["volume_ma"] = vol_sma.sma_indicator()
            df["volume_ratio"] = df["volume"] / df["volume_ma"]

        return df

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        """Generate momentum breakout signal with multi-indicator confirmation."""
        default = Signal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            strategy_name=self.name,
            confidence=0.0,
        )

        if df.empty or len(df) < self._macd_slow + self._macd_signal + 5:
            return default

        # Get latest values
        price = self._safe_get(df["close"])
        ema_fast = self._safe_get(df.get("ema_fast", pd.Series()))
        ema_slow = self._safe_get(df.get("ema_slow", pd.Series()))
        macd_hist = self._safe_get(df.get("macd_hist", pd.Series()))
        macd_hist_prev = self._safe_get(df.get("macd_hist", pd.Series()), -2)
        rsi = self._safe_get(df.get("rsi", pd.Series()))
        volume_ratio = self._safe_get(
            df.get("volume_ratio", pd.Series()), default=1.0)

        if price == 0 or ema_fast == 0 or ema_slow == 0:
            return default

        # Check EMA crossover (also check if it just crossed)
        ema_fast_prev = self._safe_get(df.get("ema_fast", pd.Series()), -2)
        ema_slow_prev = self._safe_get(df.get("ema_slow", pd.Series()), -2)

        ema_bullish = ema_fast > ema_slow
        ema_just_crossed_up = ema_fast > ema_slow and ema_fast_prev <= ema_slow_prev
        ema_bearish = ema_fast < ema_slow
        ema_just_crossed_down = ema_fast < ema_slow and ema_fast_prev >= ema_slow_prev

        indicators = {
            "price": price,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "macd_hist": macd_hist,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
        }

        # --- BUY Signal ---
        buy_confirmations = 0
        buy_reasons = []

        if ema_bullish:
            buy_confirmations += 1
            buy_reasons.append("EMA_BULL")
            if ema_just_crossed_up:
                buy_confirmations += 0.5  # Bonus for fresh crossover
                buy_reasons.append("EMA_CROSS_UP")

        if macd_hist > 0 and macd_hist > macd_hist_prev:
            buy_confirmations += 1
            buy_reasons.append("MACD_BULL")

        if rsi > 50:
            buy_confirmations += 1
            buy_reasons.append("RSI_BULL")

        has_volume = volume_ratio >= self._volume_multiplier
        if has_volume:
            buy_reasons.append("VOL_SURGE")

        if buy_confirmations >= self._min_confirmations:
            confidence = self._calculate_confidence(
                buy_confirmations, rsi, volume_ratio, macd_hist, is_buy=True
            )

            # Volume acts as a confirmation booster, not a requirement
            if has_volume:
                confidence = min(confidence * 1.2, 1.0)

            stop_loss = price * 0.99  # 1% stop
            take_profit = price * 1.02  # 2% target

            return Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                strategy_name=self.name,
                confidence=confidence,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"Momentum BUY: {', '.join(buy_reasons)}",
                indicators=indicators,
            )

        # --- SELL Signal ---
        sell_confirmations = 0
        sell_reasons = []

        if ema_bearish:
            sell_confirmations += 1
            sell_reasons.append("EMA_BEAR")
            if ema_just_crossed_down:
                sell_confirmations += 0.5
                sell_reasons.append("EMA_CROSS_DOWN")

        if macd_hist < 0 and macd_hist < macd_hist_prev:
            sell_confirmations += 1
            sell_reasons.append("MACD_BEAR")

        if rsi < 50:
            sell_confirmations += 1
            sell_reasons.append("RSI_BEAR")

        if sell_confirmations >= self._min_confirmations:
            confidence = self._calculate_confidence(
                sell_confirmations, rsi, volume_ratio, macd_hist, is_buy=False
            )

            if has_volume:
                confidence = min(confidence * 1.2, 1.0)

            stop_loss = price * 1.01
            take_profit = price * 0.98

            return Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                strategy_name=self.name,
                confidence=confidence,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"Momentum SELL: {', '.join(sell_reasons)}",
                indicators=indicators,
            )

        return default

    def _calculate_confidence(
        self, confirmations: float, rsi: float, vol_ratio: float,
        macd_hist: float, is_buy: bool,
    ) -> float:
        """Calculate signal confidence (0-1)."""
        confidence = 0.0

        # Confirmation count (0-0.4)
        confidence += min(confirmations / 4, 0.4)

        # RSI strength (0-0.25)
        if is_buy:
            rsi_strength = min((rsi - 50) / 30, 1.0) if rsi > 50 else 0
        else:
            rsi_strength = min((50 - rsi) / 30, 1.0) if rsi < 50 else 0
        confidence += rsi_strength * 0.25

        # MACD magnitude (0-0.2)
        confidence += min(abs(macd_hist) / 5, 0.2)

        # Volume (0-0.15)
        confidence += min((vol_ratio - 1) / 3, 0.15)

        return min(confidence, 1.0)
