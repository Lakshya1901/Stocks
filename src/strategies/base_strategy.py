"""
base_strategy.py — Abstract Strategy Interface

All trading strategies must implement this interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pandas as pd


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    """A trading signal produced by a strategy."""
    signal_type: SignalType
    symbol: str
    strategy_name: str
    confidence: float  # 0.0 to 1.0
    price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reason: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    indicators: dict = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.signal_type != SignalType.HOLD and self.confidence > 0.3


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self._config = config
        self._enabled = config.get("enabled", True)
        self._weight = config.get("weight", 0.33)

    @property
    def weight(self) -> float:
        return self._weight

    @property
    def enabled(self) -> bool:
        return self._enabled

    @abstractmethod
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate all technical indicators needed for this strategy.
        Appends indicator columns to the DataFrame.
        """

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        """
        Generate a trading signal based on current indicators.

        Args:
            df: OHLCV DataFrame with indicators already calculated
            symbol: The trading symbol

        Returns:
            Signal with type (BUY/SELL/HOLD), confidence, and metadata
        """

    def _safe_get(self, series: pd.Series, index: int = -1, default: float = 0.0) -> float:
        """Safely get a value from a pandas Series."""
        try:
            val = float(series.iloc[index])
            if pd.isna(val):
                return default
            return val
        except (IndexError, TypeError, ValueError):
            return default
