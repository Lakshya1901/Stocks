"""
data_fetcher.py — Historical & Intraday Data Fetcher

Fetches historical candle data for indicator warmup and backtesting.
Uses official Groww API for historical data.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from loguru import logger


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

        # Try Groww API
        df = self._fetch_from_groww(symbol, interval, days)

        if df is not None and not df.empty:
            self._cache[cache_key] = (df, datetime.now())
            return df.copy()

        logger.warning(
            "No historical data available for {} ({})", symbol, interval)
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
                from_date=(datetime.now() - timedelta(days=days)
                           ).strftime("%Y-%m-%d"),
                to_date=datetime.now().strftime("%Y-%m-%d"),
            )
            if data:
                df = pd.DataFrame(data)
                df.columns = [c.lower() for c in df.columns]
                return self._normalize_dataframe(df)
        except Exception as e:
            logger.debug("Groww historical fetch failed for {}: {}", symbol, e)

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
        """Get today's intraday data plus historical context for indicators."""
        return self.get_historical_data(symbol, interval=interval, days=5)

    def get_news_sentiment(self, symbol: str) -> float:
        """
        Fetch latest news for a symbol using Yahoo Finance RSS feed and analyze sentiment.
        Returns a score between -1.0 (negative) and 1.0 (positive).
        """
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            import requests
            import xml.etree.ElementTree as ET

            analyzer = SentimentIntensityAnalyzer()
        except ImportError:
            logger.warning(
                "vaderSentiment or requests not installed — skipping news sentiment")
            return 0.0

        try:
            yf_symbol = symbol.upper() + self.NSE_SUFFIX
            url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={yf_symbol}&region=IN&lang=en-IN"

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            response = requests.get(url, headers=headers, timeout=5)

            if response.status_code != 200:
                return 0.0

            root = ET.fromstring(response.content)

            total_compound = 0.0
            count = 0

            # Find all <item> tags in the RSS feed
            for item in root.findall('.//item'):
                title = item.find('title')
                desc = item.find('description')

                text = ""
                if title is not None and title.text:
                    text += title.text + ". "
                if desc is not None and desc.text:
                    text += desc.text

                if text:
                    scores = analyzer.polarity_scores(text)
                    total_compound += scores["compound"]
                    count += 1

            if count > 0:
                return total_compound / count
            return 0.0

        except Exception as e:
            logger.debug(
                "Failed to fetch news sentiment for {}: {}", symbol, e)
            return 0.0

    def clear_cache(self):
        """Clear the data cache."""
        self._cache.clear()
        logger.debug("Data cache cleared")
