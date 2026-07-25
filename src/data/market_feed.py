"""
market_feed.py — Real-time Market Data Feed

Wraps GrowwFeed for live LTP, OHLC, and market depth subscriptions
with auto-reconnect and event-driven price updates.
"""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from loguru import logger

try:
    from growwapi import GrowwAPI, GrowwFeed
except ImportError:
    GrowwFeed = None
    logger.warning("growwapi not installed — market feed will run in offline mode")


@dataclass
class Tick:
    """A single price tick."""
    symbol: str
    ltp: float  # last traded price
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    bid: float = 0.0
    ask: float = 0.0


class MarketFeed:
    """
    Real-time market data manager.
    Subscribes to live feeds and maintains tick buffers per symbol.
    """

    def __init__(self, access_token: str):
        self._access_token = access_token
        self._feed = None
        self._subscribed_symbols = set()
        self._tick_buffers = defaultdict(list)  # symbol -> [Tick, ...]
        self._latest_ticks = {}  # symbol -> Tick
        self._listeners = []  # List of callback functions
        self._lock = threading.Lock()
        self._connected = False
        self._reconnect_attempts = 0
        self._max_buffer_size = 500  # ticks per symbol

    def connect(self):
        """Initialize and connect the feed."""
        if GrowwFeed is None:
            logger.warning("GrowwFeed not available — running in offline/paper mode")
            self._connected = False
            return

        try:
            self._feed = GrowwFeed(self._access_token)
            self._connected = True
            self._reconnect_attempts = 0
            logger.info("Market feed connected successfully")
        except Exception as e:
            logger.error("Failed to connect market feed: {}", e)
            self._connected = False

    def subscribe(self, symbols: list):
        """Subscribe to live data for a list of symbols."""
        for symbol in symbols:
            symbol = symbol.upper()
            if symbol in self._subscribed_symbols:
                continue

            try:
                if self._feed is not None:
                    self._feed.subscribe_live_data(GrowwAPI.SEGMENT_CASH, symbol)
                self._subscribed_symbols.add(symbol)
                logger.debug("Subscribed to live feed: {}", symbol)
            except Exception as e:
                logger.error("Failed to subscribe to {}: {}", symbol, e)

        logger.info("Subscribed to {} symbols", len(self._subscribed_symbols))

    def unsubscribe(self, symbols: list):
        """Unsubscribe from live data for given symbols."""
        for symbol in symbols:
            symbol = symbol.upper()
            if symbol not in self._subscribed_symbols:
                continue

            try:
                if self._feed is not None:
                    self._feed.unsubscribe_live_data(GrowwAPI.SEGMENT_CASH, symbol)
                self._subscribed_symbols.discard(symbol)
                logger.debug("Unsubscribed from: {}", symbol)
            except Exception as e:
                logger.error("Failed to unsubscribe from {}: {}", symbol, e)

    def unsubscribe_all(self):
        """Unsubscribe from all symbols."""
        self.unsubscribe(list(self._subscribed_symbols))

    def get_ltp(self, symbol: str) -> float | None:
        """Get the latest traded price for a symbol."""
        symbol = symbol.upper()

        # Try live feed first
        if self._feed is not None and self._connected:
            try:
                ltp = self._feed.get_stocks_ltp(symbol)
                if ltp is not None:
                    self._update_tick(symbol, ltp=float(ltp))
                    return float(ltp)
            except Exception as e:
                logger.debug("LTP fetch failed for {}: {}", symbol, e)

        # Fallback to cached latest tick
        tick = self._latest_ticks.get(symbol)
        return tick.ltp if tick else None

    def get_quote(self, symbol: str) -> dict | None:
        """Get full quote (OHLCV + depth) for a symbol."""
        symbol = symbol.upper()

        if self._feed is not None and self._connected:
            try:
                quote = self._feed.get_stocks_ohlc(symbol)
                if quote:
                    return quote
            except Exception as e:
                logger.debug("Quote fetch failed for {}: {}", symbol, e)

        tick = self._latest_ticks.get(symbol)
        if tick:
            return {
                "symbol": tick.symbol,
                "ltp": tick.ltp,
                "open": tick.open,
                "high": tick.high,
                "low": tick.low,
                "close": tick.close,
                "volume": tick.volume,
            }
        return None

    def get_latest_tick(self, symbol: str) -> Tick | None:
        """Get the most recent Tick object for a symbol."""
        return self._latest_ticks.get(symbol.upper())

    def get_tick_buffer(self, symbol: str) -> list:
        """Get the tick history buffer for a symbol."""
        return list(self._tick_buffers.get(symbol.upper(), []))

    def _update_tick(self, symbol: str, ltp: float = 0, **kwargs):
        """Update the tick buffer and notify listeners."""
        symbol = symbol.upper()
        tick = Tick(
            symbol=symbol,
            ltp=ltp,
            open=kwargs.get("open", 0),
            high=kwargs.get("high", 0),
            low=kwargs.get("low", 0),
            close=kwargs.get("close", 0),
            volume=kwargs.get("volume", 0),
            timestamp=datetime.now(),
        )

        with self._lock:
            self._latest_ticks[symbol] = tick
            buffer = self._tick_buffers[symbol]
            buffer.append(tick)
            if len(buffer) > self._max_buffer_size:
                self._tick_buffers[symbol] = buffer[-self._max_buffer_size:]

        # Notify listeners
        for callback in self._listeners:
            try:
                callback(tick)
            except Exception as e:
                logger.error("Tick listener error: {}", e)

    def on_tick(self, callback: Callable):
        """Register a callback for tick updates. callback(tick: Tick)"""
        self._listeners.append(callback)

    def poll_prices(self, symbols: list = None):
        """
        Manually poll prices for subscribed symbols.
        Useful as a fallback when WebSocket isn't available.
        """
        targets = symbols or list(self._subscribed_symbols)
        for symbol in targets:
            self.get_ltp(symbol)

    def is_connected(self) -> bool:
        return self._connected

    def reconnect(self):
        """Attempt to reconnect with exponential backoff."""
        self._reconnect_attempts += 1
        wait_time = min(2 ** self._reconnect_attempts, 60)  # Max 60s
        logger.info(
            "Reconnecting market feed in {}s (attempt #{})",
            wait_time,
            self._reconnect_attempts,
        )
        time.sleep(wait_time)
        self.connect()

    def disconnect(self):
        """Disconnect the feed."""
        self.unsubscribe_all()
        self._connected = False
        logger.info("Market feed disconnected")
