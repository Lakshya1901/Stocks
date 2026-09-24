"""
market.py — Live NSE market data (Yahoo Finance) + technical indicators (RSI, MACD, Bollinger).

Fetches real-time price, average daily volume, and historical closes using
an authenticated session with Yahoo Finance (cookie + crumb flow) to prevent
rate limits and ensure 100% real live market prices.

Quotes are cached briefly to eliminate redundant network roundtrips within each cycle.
If data cannot be fetched from external APIs, it returns None.
It NEVER generates synthetic or simulated prices.
"""

from config import get_logger

import time
import requests
import yfinance as yf

logger = get_logger(__name__)

# Brief cache to prevent duplicate fetches for the same symbol within a short window
# symbol -> (timestamp, data_dict)
_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 20.0


class _YahooSessionManager:
    """Manages an authenticated session with Yahoo Finance cookies and crumbs."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        self.crumb: str | None = None
        self._crumb_time: float = 0.0

    def refresh_crumb(self) -> str | None:
        """Obtains fresh cookies from fc.yahoo.com and a crumb from Yahoo Finance."""
        try:
            self.session.get("https://fc.yahoo.com", timeout=6)
            for host in ["query2.finance.yahoo.com", "query1.finance.yahoo.com"]:
                try:
                    resp = self.session.get(f"https://{host}/v1/test/getcrumb", timeout=6)
                    if resp.status_code == 200 and resp.text.strip():
                        self.crumb = resp.text.strip()
                        self._crumb_time = time.time()
                        logger.debug("Successfully refreshed Yahoo Finance crumb")
                        return self.crumb
                except Exception:
                    continue
        except Exception as exc:
            logger.debug(f"Failed to refresh Yahoo Finance crumb: {exc}")
        return None

    def get_crumb(self) -> str | None:
        """Returns the cached crumb or refreshes it if older than 1 hour."""
        now = time.time()
        if not self.crumb or (now - self._crumb_time > 3600):
            return self.refresh_crumb()
        return self.crumb


_yahoo_mgr = _YahooSessionManager()


def get_quote(symbol: str) -> dict | None:
    """
    Fetches real market quote for the given NSE symbol.
    Returns a dictionary with:
        {
            "symbol": str,
            "price": float,
            "avg_volume": float,
            "closes": list[float],
        }
    or None on failure.
    """
    now = time.time()
    clean_sym = symbol.strip().upper()

    # Return cached data if fresh
    if clean_sym in _QUOTE_CACHE:
        cached_time, cached_data = _QUOTE_CACHE[clean_sym]
        if now - cached_time < _CACHE_TTL_SECONDS:
            return cached_data

    ticker = f"{clean_sym}.NS"
    crumb = _yahoo_mgr.get_crumb()

    # Primary method: Yahoo Finance chart API with crumb
    hosts = ["query2.finance.yahoo.com", "query1.finance.yahoo.com"]
    for attempt in range(2):
        for host in hosts:
            url = f"https://{host}/v8/finance/chart/{ticker}?interval=1d&range=3mo"
            if crumb:
                url += f"&crumb={crumb}"
            try:
                resp = _yahoo_mgr.session.get(url, timeout=7)
                if resp.status_code in (401, 403, 429) and attempt == 0:
                    crumb = _yahoo_mgr.refresh_crumb()
                    break

                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("chart", {}).get("result")
                    if results:
                        res = results[0]
                        meta = res.get("meta", {})
                        quotes = res.get("indicators", {}).get("quote", [{}])[0]

                        raw_closes = quotes.get("close", [])
                        closes = [float(c) for c in raw_closes if c is not None]

                        # Previous session close: the daily chart's last candle is today's while trading
                        stamps = [t for t, c in zip(res.get("timestamp", []), raw_closes) if c is not None]
                        last_is_today = bool(stamps) and time.strftime("%Y-%m-%d", time.gmtime(stamps[-1] + 19800)) == \
                            time.strftime("%Y-%m-%d", time.gmtime(time.time() + 19800))  # IST = UTC+5:30
                        prev_close = closes[-2] if last_is_today and len(closes) >= 2 else (closes[-1] if closes else None)

                        price = meta.get("regularMarketPrice")
                        if price is None or (price is not None and float(price) <= 0):
                            if closes:
                                price = closes[-1]
                            else:
                                price = meta.get("chartPreviousClose") or meta.get("previousClose")
                        if price is None:
                            price = 0

                        raw_volumes = quotes.get("volume", [])
                        volumes = [float(v) for v in raw_volumes if v is not None]
                        if volumes:
                            avg_volume = float(sum(volumes) / len(volumes))
                        else:
                            avg_volume = float(meta.get("regularMarketVolume", 0.0))

                        if price and float(price) > 0:
                            result = {
                                "symbol": clean_sym,
                                "price": round(float(price), 2),
                                "avg_volume": avg_volume,
                                "closes": closes,
                                "day_change_pct": (float(price) / prev_close - 1.0) if prev_close else None,
                            }
                            _QUOTE_CACHE[clean_sym] = (now, result)
                            return result
            except Exception as exc:
                logger.debug(f"Direct quote fetch failed via {host} for {ticker}: {exc}")

    # Fallback method: yfinance Ticker fast_info
    try:
        info = yf.Ticker(ticker).fast_info
        price = float(info.last_price or 0.0)
        volume = float(info.three_month_average_volume or 0.0)
        if price > 0:
            result = {
                "symbol": clean_sym,
                "price": round(price, 2),
                "avg_volume": volume,
                "closes": [],
                "day_change_pct": None,
            }
            _QUOTE_CACHE[clean_sym] = (now, result)
            return result
    except Exception as exc:
        logger.debug(f"Fallback quote fetch failed for {ticker}: {exc}")

    # Never invent or simulate market prices. Return None on failure.
    logger.warning(f"Unable to retrieve live market data for {clean_sym}")
    return None


def get_current_price(symbol: str) -> float | None:
    """Returns the current price in INR for the given NSE symbol, or None."""
    quote = get_quote(symbol)
    return quote["price"] if quote else None


def get_price_and_volume(symbol: str) -> tuple[float, float] | None:
    """Returns (current_price, avg_daily_volume) for the given NSE symbol, or None."""
    quote = get_quote(symbol)
    if quote and quote["price"] is not None and quote["price"] > 0:
        return quote["price"], quote["avg_volume"]
    return None

# =============================================================================
# Mathematical trading signals as a second gate before buying.
# =============================================================================
import pandas as pd

logger = get_logger(__name__)




def _rsi(close: pd.Series, period: int = 14) -> float:
    """
    RSI (Relative Strength Index).
    < 30 = oversold (good to buy), > 70 = overbought (risky to buy).
    Returns the latest RSI value.
    """
    delta   = close.diff()
    gain    = delta.clip(lower=0)
    loss    = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs      = avg_gain / avg_loss.replace(0, float("inf"))
    rsi_series = 100 - (100 / (1 + rs))
    return float(rsi_series.iloc[-1])


def _macd(close: pd.Series, fast=12, slow=26, signal=9) -> tuple[float, float, float]:
    """
    MACD line, Signal line, Histogram.
    MACD crossing above signal = bullish, below = bearish.
    """
    ema_fast   = close.ewm(span=fast, adjust=False).mean()
    ema_slow   = close.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])


def _bollinger(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> tuple[float, float, float]:
    """
    Bollinger Bands. Returns (upper, middle, lower).
    Price near lower band = potentially oversold, near upper = overbought.
    """
    middle = close.rolling(window=period).mean()
    std    = close.rolling(window=period).std()
    upper  = middle + std_dev * std
    lower  = middle - std_dev * std
    return float(upper.iloc[-1]), float(middle.iloc[-1]), float(lower.iloc[-1])


def _rsi_score(rsi: float) -> float:
    """Maps RSI to a score component in range [-1, 1]."""
    if rsi < 30:    return 1.0   # oversold — strong buy zone
    if rsi < 45:    return 0.6   # healthy buy zone
    if rsi < 60:    return 0.2   # mild caution
    if rsi < 70:    return -0.2  # approaching overbought
    return -1.0                  # overbought — avoid buying here


def _macd_score(macd: float, signal: float, hist: float) -> float:
    """Maps MACD to a score component in range [-1, 1]."""
    if hist > 0:
        return 0.8 if macd > 0 else 0.4    # bullish histogram above zero line = strong
    else:
        return -0.8 if macd < 0 else -0.4  # bearish histogram below zero line = weak


def _bollinger_score(price: float, upper: float, middle: float, lower: float) -> float:
    """Maps Bollinger Band position to a score component in range [-1, 1]."""
    band_width = upper - lower
    if band_width <= 0:
        return 0.0
    # Normalise price position within the band: 0 = lower, 0.5 = middle, 1 = upper
    position = (price - lower) / band_width
    if position < 0.2:   return 1.0    # very close to lower band — oversold bounce likely
    if position < 0.4:   return 0.5    # below middle — mild bullish
    if position < 0.6:   return 0.1    # around middle — neutral
    if position < 0.8:   return -0.3   # above middle — some caution
    return -0.8                        # near upper band — likely to mean-revert downward




def get_technical_score(symbol: str) -> tuple[float, dict]:
    """
    Returns (composite_score, details_dict).

    composite_score is a weighted average of RSI, MACD, and Bollinger signals:
        > 0.3  = net bullish — good technical environment to buy
          0.0  = mixed or neutral — proceed with caution
        < 0.0  = net bearish — technicals warn against buying

    details_dict contains the raw indicator values for display in logs.
    """
    quote = get_quote(symbol)
    if not quote or not quote.get("closes") or len(quote["closes"]) < 20:
        logger.warning(f"{symbol}: Insufficient price history — skipping technical check (score=0.0)")
        return 0.0, {"error": "insufficient history", "rsi": 50.0}

    close = pd.Series(quote["closes"])
    price = quote.get("price") or float(close.iloc[-1])

    rsi_val  = _rsi(close)
    macd_val, sig_val, hist_val = _macd(close)
    bb_upper, bb_mid, bb_lower  = _bollinger(close)

    r_score = _rsi_score(rsi_val)
    m_score = _macd_score(macd_val, sig_val, hist_val)
    b_score = _bollinger_score(price, bb_upper, bb_mid, bb_lower)

    # Weighted composite: RSI 40%, MACD 40%, Bollinger 20%
    composite = round(0.40 * r_score + 0.40 * m_score + 0.20 * b_score, 3)

    details = {
        "price":         round(price, 2),
        "rsi":           round(rsi_val, 1),
        "macd":          round(macd_val, 4),
        "macd_signal":   round(sig_val, 4),
        "macd_hist":     round(hist_val, 4),
        "bb_upper":      round(bb_upper, 2),
        "bb_mid":        round(bb_mid, 2),
        "bb_lower":      round(bb_lower, 2),
        "score_rsi":     r_score,
        "score_macd":    m_score,
        "score_bb":      b_score,
        "composite":     composite,
    }

    verdict = "BULLISH" if composite > 0.2 else ("BEARISH" if composite < -0.2 else "NEUTRAL")
    band_width = bb_upper - bb_lower
    bb_pos_str = f"{(price - bb_lower) / band_width:.0%}" if band_width > 0 else "N/A"
    logger.info(
        f"Technical [{symbol}]: RSI={rsi_val:.1f} MACD_hist={hist_val:.4f} "
        f"BB_pos={bb_pos_str} → score={composite} [{verdict}]"
    )
    return composite, details
