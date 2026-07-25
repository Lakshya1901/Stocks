"""
data_fetcher.py — Historical & Intraday Data Fetcher

Fetches historical candle data for indicator warmup and backtesting.
Uses yfinance as a fallback for historical data.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from loguru import logger

try:
    import yfinance as yf
except ImportError:
    yf = None
    logger.warning("yfinance not installed — historical data fallback unavailable")


class DataFetcher:
    """Fetches historical OHLCV data for NSE stocks."""

    # yfinance requires .NS suffix for NSE stocks
    NSE_SUFFIX = ".NS"

    def __init__(self, groww_api=None):
        self._groww = groww_api
        self._cache = {}  # (symbol, interval, days) -> DataFrame

    def get_historical_data(
        self,
        symbol: str,
        interval: str = "5m",
        days: int = 5,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Get historical OHLCV candle data for a symbol.

        Args:
            symbol: Trading symbol (e.g., "SBIN")
            interval: Candle interval — "1m", "5m", "15m", "1d"
            days: Number of days of history
            use_cache: Whether to use cached data

        Returns:
            DataFrame with columns: open, high, low, close, volume
        """
        cache_key = (symbol.upper(), interval, days)

        if use_cache and cache_key in self._cache:
            cached_df, cached_time = self._cache[cache_key]
            # Cache valid for 5 minutes for intraday, 1 hour for daily
            max_age = 300 if interval != "1d" else 3600
            if (datetime.now() - cached_time).total_seconds() < max_age:
                return cached_df.copy()

        # Try Groww API first (if available and has historical endpoint)
        df = self._fetch_from_groww(symbol, interval, days)

        # Fallback to yfinance
        if df is None or df.empty:
            df = self._fetch_from_yfinance(symbol, interval, days)

        if df is not None and not df.empty:
            self._cache[cache_key] = (df, datetime.now())
            return df.copy()

        logger.warning("No historical data available for {} ({})", symbol, interval)
        return pd.DataFrame()

    def _fetch_from_groww(self, symbol: str, interval: str, days: int) -> pd.DataFrame | None:
        """Try fetching historical data from Groww API."""
        if self._groww is None:
            return None

        try:
            # Groww historical candle endpoint (if available)
            data = self._groww.get_historical_candles(
                trading_symbol=symbol,
                interval=interval,
                from_date=(datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"),
                to_date=datetime.now().strftime("%Y-%m-%d"),
            )
            if data:
                df = pd.DataFrame(data)
                df.columns = [c.lower() for c in df.columns]
                return self._normalize_dataframe(df)
        except Exception as e:
            logger.debug("Groww historical fetch failed for {}: {}", symbol, e)

        return None

    def _fetch_from_yfinance(self, symbol: str, interval: str, days: int) -> pd.DataFrame | None:
        """Fetch historical data from yfinance as fallback."""
        if yf is None:
            return None

        yf_symbol = symbol.upper() + self.NSE_SUFFIX
        logger.debug("Fetching {} from yfinance ({}, {} days)", yf_symbol, interval, days)

        try:
            # yfinance limits: 1m data = max 7 days, 5m = 60 days
            ticker = yf.Ticker(yf_symbol)

            # Map interval strings
            interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1d": "1d"}
            yf_interval = interval_map.get(interval, "5m")

            # For intraday data, yfinance has period limits
            if yf_interval in ("1m",):
                period = f"{min(days, 7)}d"
            elif yf_interval in ("5m", "15m"):
                period = f"{min(days, 60)}d"
            else:
                period = f"{days}d"

            df = ticker.history(period=period, interval=yf_interval)

            if df.empty:
                return None

            return self._normalize_dataframe(df)

        except Exception as e:
            logger.error("yfinance fetch failed for {}: {}", yf_symbol, e)
            return None

    def _normalize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize DataFrame to standard OHLCV columns."""
        # Standardize column names
        col_mapping = {
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "Adj Close": "adj_close",
        }

        df = df.rename(columns=col_mapping)

        # Ensure required columns exist
        required = ["open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                # Try case-insensitive match
                for c in df.columns:
                    if c.lower() == col:
                        df[col] = df[c]
                        break

        # Keep only OHLCV + any extras
        available = [c for c in required if c in df.columns]
        df = df[available].copy()

        # Clean data
        df.replace([np.inf, -np.inf], np.nan, inplace=True)
        df.dropna(subset=["open", "high", "low", "close"], inplace=True)

        # Ensure numeric types
        for col in available:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def get_latest_close(self, symbol: str) -> float | None:
        """Get the previous day's closing price."""
        df = self.get_historical_data(symbol, interval="1d", days=5)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
        return None

    def get_average_volume(self, symbol: str, days: int = 20) -> float | None:
        """Get the average daily volume over N days."""
        df = self.get_historical_data(symbol, interval="1d", days=days)
        if df is not None and not df.empty and "volume" in df.columns:
            return float(df["volume"].mean())
        return None

    def get_intraday_data(self, symbol: str, interval: str = "5m") -> pd.DataFrame:
        """Get today's intraday data."""
        return self.get_historical_data(symbol, interval=interval, days=1)

    def clear_cache(self):
        """Clear the data cache."""
        self._cache.clear()
        logger.debug("Data cache cleared")
