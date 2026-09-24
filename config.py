# =============================================================================
# config.py — All tuneable parameters live here. Edit this file only.
# =============================================================================

import os
from dotenv import load_dotenv

load_dotenv()  # reads from a .env file in the same directory

# --- Groww API Credentials (store these in a .env file, never hardcode) ---
# Use the TOTP token flow (Generate TOTP token on Groww API keys page).
# This has no daily expiry, so the bot runs autonomously without manual approval.
GROWW_API_KEY         = os.getenv("GROWW_API_KEY", "")          # API key shown alongside the QR code
GROWW_TOTP_SECRET     = os.getenv("GROWW_TOTP_SECRET", "")      # The text secret key shown below the QR code

# --- Bot Behaviour ---
# Set DRY_RUN = True to simulate trades without placing real orders.
# ALWAYS test with DRY_RUN = True for at least one full trading day first.
DRY_RUN = True

# How often the main loop checks for new news (in seconds)
POLL_INTERVAL_SECONDS = 30

# --- Capital & Risk Management ---
# Total capital allocated to the bot for trading (in INR).
# In LIVE mode, the bot will also attempt to query your live Groww available margin,
# but uses this amount if Groww margin is 0 or unlinked.
TOTAL_ACCOUNT_CAPITAL = 15000.0

# How much of your total account capital to deploy on a single trade.
# 0.20 means 20% (e.g., ₹3000 if your capital is ₹15000, allowing up to 5 concurrent positions).
TRADE_ACCOUNT_PCT = 0.20

# Stop-loss: exit the trade if price falls this % below entry
STOP_LOSS_PCT = 0.02        # 2%

# Take-profit: exit the trade if price rises this % above entry
TAKE_PROFIT_PCT = 0.04      # 4%

# --- Sentiment Thresholds ---
# Minimum FinBERT confidence score to BUY on positive news.
# 0.60 = confident positive news triggers a trade.
# Lower = more trades, higher = fewer but higher-conviction trades.
SENTIMENT_THRESHOLD = 0.60

# Higher threshold for protective SELL on negative news (to reduce false positives).
# Only sell a held position if negative sentiment is very high confidence.
NEGATIVE_SENTIMENT_THRESHOLD = 0.75

# --- Technical Analysis Gate ---
# Composite score returned by market.get_technical_score().
# Range is -1.0 (very bearish) to 1.0 (very bullish).
# 0.1 means: allow slightly mixed technicals as long as they're not net bearish.
# Raise to 0.3+ if you want strict bullish confirmation before buying.
# Set to -1.0 to effectively disable the technical gate.
TECHNICAL_SCORE_THRESHOLD = 0.1

# --- Overbought Blocker ---
# Skip a BUY when RSI is above OVERBOUGHT_RSI (stock already ran up),
# unless news sentiment is at least OVERBOUGHT_OVERRIDE_SENTIMENT.
OVERBOUGHT_RSI = 80
OVERBOUGHT_OVERRIDE_SENTIMENT = 0.92

# --- Anti-chase Guard ---
# Skip a BUY if the stock is already up more than this today — the news is priced in.
MAX_DAY_GAIN_PCT = 0.03     # 3%

# --- Liquidity Guard ---
# Minimum average daily trading volume (in shares) for a stock to be tradeable.
# This prevents the bot from trying to buy extremely illiquid small-caps where
# a single market order could wildly move the price.
# Set to 0 to disable this check entirely (not recommended).
MIN_DAILY_VOLUME = 25_000

# --- Exchange ---
EXCHANGE = "NSE"

# --- Directory and File Paths ---
from pathlib import Path
BASE_DIR = Path(__file__).parent.resolve()

# --- NSE Equity Symbol List ---
NSE_SYMBOLS_CSV = str(BASE_DIR / "nse_equity_list.csv")

# --- Trade History & Position Files ---
TRADES_FILE = BASE_DIR / "trades.jsonl"
OPEN_POSITIONS_FILE = BASE_DIR / "open_positions.json"

# NSE trading holidays, cached from nseindia.com and refreshed weekly
NSE_HOLIDAYS_FILE = BASE_DIR / "nse_holidays.json"

# Minimum trade capital in INR (guards against micro-trades where fixed DP charges eat profits)
MIN_TRADE_CAPITAL = 1000.0

# --- Logging ---
LOG_FILE = str(BASE_DIR / "bot.log")

# Ignore articles published longer ago than this (some feeds serve years-old items)
MAX_ARTICLE_AGE_HOURS = 12

# --- RSS News Feeds ---
# Includes primary Indian financial and stock-specific market feeds
RSS_FEEDS = {
    "ET_Stocks":             "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "ET_Markets":            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Livemint_Markets":      "https://www.livemint.com/rss/markets",
    "Moneycontrol_Business": "https://www.moneycontrol.com/rss/business.xml",
    "Moneycontrol_Top":      "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "Moneycontrol_Markets":  "https://www.moneycontrol.com/rss/marketreports.xml",
}


# --- Logging: shared console + file handlers ---
import logging
import sys

_shared_ch: logging.StreamHandler | None = None
_shared_fh: logging.FileHandler | None = None


def _init_shared_handlers() -> tuple[logging.StreamHandler, logging.FileHandler]:
    global _shared_ch, _shared_fh
    if _shared_ch is None or _shared_fh is None:
        fmt = logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        _shared_fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
        _shared_fh.setLevel(logging.DEBUG)
        _shared_fh.setFormatter(fmt)

        _shared_ch = logging.StreamHandler(sys.stdout)
        _shared_ch.setLevel(logging.INFO)
        _shared_ch.setFormatter(fmt)
    return _shared_ch, _shared_fh


def get_logger(name: str) -> logging.Logger:
    """
    Returns a logger that writes to both the console and a single persistent log file.
    All module loggers share the same handlers to prevent file truncation and corruption.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    ch, fh = _init_shared_handlers()
    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger
