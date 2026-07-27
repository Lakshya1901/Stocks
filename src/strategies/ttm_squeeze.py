"""
ttm_squeeze.py — News-Catalyzed Volatility Squeeze Strategy

Calculates Bollinger Bands and Keltner Channels. A squeeze fires when BB compresses inside KC,
and releases when BB expands outside KC on high volume and momentum.
"""

import pandas as pd
from ta.volatility import BollingerBands, KeltnerChannel
from loguru import logger

from src.strategies.base_strategy import BaseStrategy, Signal, SignalType


class TTMSqueezeStrategy(BaseStrategy):
    """
    TTM Squeeze Strategy combining volatility compression with momentum.
    """

    def __init__(self, config: dict):
        super().__init__("TTMSqueeze", config)
        self._bb_window = config.get("bb_window", 20)
        self._bb_std = config.get("bb_std", 2.0)
        self._kc_window = config.get("kc_window", 20)
        self._kc_atr_multiplier = config.get("kc_atr_multiplier", 1.5)

        # Momentum parameters
        self._momentum_period = config.get("momentum_period", 12)

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if len(df) < max(self._bb_window, self._kc_window) + 1:
            return df

        try:
            # Calculate Bollinger Bands
            bb = BollingerBands(
                close=df["close"], window=self._bb_window, window_dev=self._bb_std)
            df["bb_upper"] = bb.bollinger_hband()
            df["bb_lower"] = bb.bollinger_lband()

            # Calculate Keltner Channels
            kc = KeltnerChannel(
                high=df["high"],
                low=df["low"],
                close=df["close"],
                window=self._kc_window,
                window_atr=self._kc_atr_multiplier
            )
            df["kc_upper"] = kc.keltner_channel_hband()
            df["kc_lower"] = kc.keltner_channel_lband()

            # Squeeze condition (BB completely inside KC)
            df["squeeze_on"] = (df["bb_lower"] > df["kc_lower"]) & (
                df["bb_upper"] < df["kc_upper"])

            # Squeeze firing condition (BB breaks out of KC)
            df["squeeze_off"] = (df["bb_lower"] < df["kc_lower"]) | (
                df["bb_upper"] > df["kc_upper"])

            # Momentum for direction
            df["momentum"] = df["close"].diff(self._momentum_period)

            # Volume surge
            df["vol_sma"] = df["volume"].rolling(window=20).mean()
            df["vol_surge"] = df["volume"] > (df["vol_sma"] * 1.5)

        except Exception as e:
            logger.error("Error calculating TTM Squeeze indicators: {}", e)

        return df

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        if len(df) < 2 or "squeeze_on" not in df.columns:
            return Signal(SignalType.HOLD, symbol, self.name, 0.0)

        current = df.iloc[-1]
        previous = df.iloc[-2]
        price = float(current["close"])

        # We are looking for a transition from Squeeze ON (previous) to Squeeze OFF (current)
        # combined with a volume surge and strong momentum.
        if previous.get("squeeze_on", False) and current.get("squeeze_off", False) and current.get("vol_surge", False):
            momentum = current.get("momentum", 0)

            # Calculate ATR for dynamic stops
            high_low = current["high"] - current["low"]
            high_close = abs(current["high"] - previous["close"])
            low_close = abs(current["low"] - previous["close"])
            atr = max(high_low, high_close, low_close)

            if momentum > 0:
                # Bullish Breakout
                return Signal(
                    signal_type=SignalType.BUY,
                    symbol=symbol,
                    strategy_name=self.name,
                    confidence=0.85,
                    price=price,
                    stop_loss=price - (atr * 2),
                    take_profit=price + (atr * 4),
                    reason=f"Bullish Volatility Squeeze Breakout (Mom: {momentum:.2f})"
                )
            elif momentum < 0:
                # Bearish Breakdown
                return Signal(
                    signal_type=SignalType.SELL,
                    symbol=symbol,
                    strategy_name=self.name,
                    confidence=0.85,
                    price=price,
                    stop_loss=price + (atr * 2),
                    take_profit=price - (atr * 4),
                    reason=f"Bearish Volatility Squeeze Breakout (Mom: {momentum:.2f})"
                )

        return Signal(SignalType.HOLD, symbol, self.name, 0.0)
