"""
opening_range_breakout.py — Opening Range Breakout (ORB) Strategy

Captures the breakout of the first 15-minute trading range.
One of the most popular intraday strategies.

Logic:
  - Record the high and low of 9:15 - 9:30 AM (first 15 minutes)
  - BUY: Price breaks above ORB high with volume confirmation
  - SELL: Price breaks below ORB low with volume confirmation
  - Timeout: If no breakout by 10:30 AM, go dormant
"""

import pandas as pd
import ta as ta_lib
from datetime import datetime, time
from loguru import logger

from src.strategies.base_strategy import BaseStrategy, Signal, SignalType


class OpeningRangeBreakoutStrategy(BaseStrategy):
    """Trades breakouts of the first 15-minute opening range."""

    def __init__(self, config: dict):
        super().__init__("opening_range_breakout", config)
        self._orb_window = config.get("orb_window_minutes", 15)
        self._timeout_hour = config.get("timeout_hour", 10)
        self._timeout_minute = config.get("timeout_minute", 30)

        # ORB state per symbol
        self._orb_ranges = {}  # symbol -> {"high": x, "low": x, "set": bool}
        self._breakout_triggered = {}  # symbol -> bool (prevent re-entry)

    def reset_daily(self):
        """Reset ORB state at the start of each day."""
        self._orb_ranges.clear()
        self._breakout_triggered.clear()
        logger.info("ORB strategy reset for new trading day")

    def set_opening_range(self, symbol: str, high: float, low: float):
        """
        Manually set the opening range for a symbol.
        Called after the first 15 minutes of trading data is available.
        """
        self._orb_ranges[symbol.upper()] = {
            "high": high,
            "low": low,
            "range": high - low,
            "mid": (high + low) / 2,
            "set": True,
        }
        logger.info(
            "ORB range set for {}: high={:.2f}, low={:.2f}, range={:.2f}",
            symbol, high, low, high - low,
        )

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate volume and ATR indicators for breakout confirmation."""
        if df.empty or len(df) < 20:
            return df

        # Volume moving average for confirmation
        if "volume" in df.columns:
            vol_sma = ta_lib.trend.SMAIndicator(
                close=df["volume"].astype(float), window=20)
            df["volume_ma"] = vol_sma.sma_indicator()
            df["volume_ratio"] = df["volume"] / df["volume_ma"]

        # ATR for position sizing
        atr_ind = ta_lib.volatility.AverageTrueRange(
            high=df["high"], low=df["low"], close=df["close"], window=14
        )
        df["atr"] = atr_ind.average_true_range()

        return df

    def detect_opening_range(self, df: pd.DataFrame, symbol: str) -> bool:
        """
        Auto-detect the opening range from intraday data.
        Looks at the first N minutes of candles (e.g., first 15 min of 1-min data).
        """
        symbol = symbol.upper()

        if symbol in self._orb_ranges and self._orb_ranges[symbol].get("set"):
            return True  # Already set

        if df.empty:
            return False

        # Try to find opening candles by index or time
        # Filter for today's data only
        today = datetime.now().date()
        today_df = df[df.index.date == today]

        if today_df.empty:
            return False

        # For 1-min data, take first 15 candles
        # For 5-min data, take first 3 candles (15 mins / 5 mins)
        orb_candles = 3 if len(today_df) > 1 and (
            today_df.index[1] - today_df.index[0]).seconds // 60 == 5 else 15

        orb_data = today_df.head(orb_candles)

        if len(orb_data) < 1:
            return False

        orb_high = float(orb_data["high"].max())
        orb_low = float(orb_data["low"].min())

        if orb_high > 0 and orb_low > 0 and orb_high != orb_low:
            self.set_opening_range(symbol, orb_high, orb_low)
            return True

        return False

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        """Generate ORB breakout signal."""
        symbol = symbol.upper()
        default = Signal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            strategy_name=self.name,
            confidence=0.0,
        )

        # Check timeout
        now = datetime.now().time()
        timeout = time(self._timeout_hour, self._timeout_minute)
        if now > timeout:
            return default

        # Check if ORB range is set
        orb = self._orb_ranges.get(symbol)
        if orb is None or not orb.get("set"):
            # Try to auto-detect from data
            self.detect_opening_range(df, symbol)
            orb = self._orb_ranges.get(symbol)
            if orb is None:
                return default

        # Already triggered a breakout for this symbol today
        if self._breakout_triggered.get(symbol):
            return default

        if df.empty or len(df) < 2:
            return default

        price = self._safe_get(df["close"])
        prev_price = self._safe_get(df["close"], -2)
        volume_ratio = self._safe_get(
            df.get("volume_ratio", pd.Series()), default=1.0)
        atr = self._safe_get(df.get("atr", pd.Series()), default=0)

        orb_high = orb["high"]
        orb_low = orb["low"]
        orb_range = orb["range"]

        if price == 0 or orb_range == 0:
            return default

        indicators = {
            "price": price,
            "orb_high": orb_high,
            "orb_low": orb_low,
            "orb_range": orb_range,
            "volume_ratio": volume_ratio,
        }

        # --- BULLISH BREAKOUT: Price crosses above ORB high ---
        if price > orb_high and prev_price <= orb_high:
            # Breakout strength
            breakout_pct = (price - orb_high) / orb_high

            confidence = self._calculate_breakout_confidence(
                breakout_pct, volume_ratio, orb_range, price, is_bullish=True
            )

            if confidence > 0.3:
                self._breakout_triggered[symbol] = True
                stop_loss = orb["mid"]  # Stop at ORB midpoint
                take_profit = price + orb_range  # Target: 1x the ORB range

                return Signal(
                    signal_type=SignalType.BUY,
                    symbol=symbol,
                    strategy_name=self.name,
                    confidence=confidence,
                    price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reason=f"ORB breakout UP: price={price:.2f} > ORB_high={orb_high:.2f}, vol_ratio={volume_ratio:.1f}",
                    indicators=indicators,
                )

        # --- BEARISH BREAKOUT: Price crosses below ORB low ---
        if price < orb_low and prev_price >= orb_low:
            breakout_pct = (orb_low - price) / orb_low

            confidence = self._calculate_breakout_confidence(
                breakout_pct, volume_ratio, orb_range, price, is_bullish=False
            )

            if confidence > 0.3:
                self._breakout_triggered[symbol] = True
                stop_loss = orb["mid"]
                take_profit = price - orb_range

                return Signal(
                    signal_type=SignalType.SELL,
                    symbol=symbol,
                    strategy_name=self.name,
                    confidence=confidence,
                    price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reason=f"ORB breakout DOWN: price={price:.2f} < ORB_low={orb_low:.2f}, vol_ratio={volume_ratio:.1f}",
                    indicators=indicators,
                )

        return default

    def _calculate_breakout_confidence(
        self, breakout_pct: float, vol_ratio: float, orb_range: float,
        price: float, is_bullish: bool,
    ) -> float:
        """Calculate breakout signal confidence."""
        confidence = 0.0

        # Breakout strength (0-0.35)
        confidence += min(breakout_pct / 0.01, 0.35)

        # Volume confirmation (0-0.35)
        if vol_ratio >= 2.0:
            confidence += 0.35
        elif vol_ratio >= 1.5:
            confidence += 0.25
        elif vol_ratio >= 1.3:
            confidence += 0.15

        # ORB range relative to price — tighter ranges break harder (0-0.3)
        range_pct = orb_range / price if price > 0 else 0
        if 0.005 < range_pct < 0.02:
            confidence += 0.3  # Sweet spot
        elif range_pct <= 0.005:
            confidence += 0.15  # Too tight — might be noise
        else:
            confidence += 0.1  # Too wide — harder to break convincingly

        return min(confidence, 1.0)
