"""
instruments.py — Groww Instrument Catalog

Downloads and parses the full Groww instrument CSV,
providing fast lookup for all NSE equity symbols.
"""

import os
import time
import pandas as pd
import requests
from loguru import logger

INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
CACHE_DIR = os.path.join(os.path.dirname(
    __file__), "..", "..", "data", "instruments")
CACHE_FILE = os.path.join(CACHE_DIR, "instruments.csv")
CACHE_MAX_AGE_SECONDS = 86400  # 24 hours


class InstrumentCatalog:
    """Manages the full NSE instrument universe from Groww's CSV."""

    # Price tier boundaries
    TIER_PENNY_MAX = 50
    TIER_SMALL_MAX = 500
    TIER_MID_MAX = 2000

    def __init__(self):
        self._instruments_df = None
        self._symbol_map = {}
        self._token_map = {}

    def load(self) -> pd.DataFrame:
        """Load instruments — download fresh CSV if cache is stale."""
        os.makedirs(CACHE_DIR, exist_ok=True)

        if self._should_refresh_cache():
            self._download_csv()

        self._parse_csv()
        logger.info(
            "Instrument catalog loaded: {} total instruments, {} NSE equities",
            len(self._instruments_df),
            len(self._symbol_map),
        )
        return self._instruments_df

    def _should_refresh_cache(self) -> bool:
        """Check if cached CSV is older than 24 hours."""
        if not os.path.exists(CACHE_FILE):
            return True
        age = time.time() - os.path.getmtime(CACHE_FILE)
        return age > CACHE_MAX_AGE_SECONDS

    def _download_csv(self):
        """Download the latest instrument CSV from Groww."""
        logger.info("Downloading instrument CSV from Groww...")
        try:
            resp = requests.get(INSTRUMENT_CSV_URL, timeout=30)
            resp.raise_for_status()
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                f.write(resp.text)
            logger.info(
                "Instrument CSV downloaded and cached at {}", CACHE_FILE)
        except requests.RequestException as e:
            logger.error("Failed to download instrument CSV: {}", e)
            if not os.path.exists(CACHE_FILE):
                raise RuntimeError(
                    "No cached instrument CSV available and download failed"
                ) from e
            logger.warning("Using stale cached CSV as fallback")

    def _parse_csv(self):
        """Parse the CSV and build lookup maps for NSE CASH equities."""
        try:
            df = pd.read_csv(CACHE_FILE)
        except Exception as e:
            raise RuntimeError(f"Failed to parse instrument CSV: {e}") from e

        # Normalize column names (Groww CSV may vary)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        # Filter for NSE equity (CASH segment) and EQ series only
        # EQ filters out bonds, NCDs, and SME stocks that Yahoo Finance doesn't support
        nse_mask = df["exchange"].str.upper() == "NSE"
        cash_mask = df["segment"].str.upper() == "CASH"
        series_mask = df["series"].str.upper() == "EQ"
        equity_mask = nse_mask & cash_mask & series_mask

        self._instruments_df = df[equity_mask].copy()
        self._instruments_df.reset_index(drop=True, inplace=True)

        # Build fast lookup maps
        for _, row in self._instruments_df.iterrows():
            symbol = str(row.get("trading_symbol",
                         row.get("tradingsymbol", ""))).strip()
            token = str(row.get("exchange_token", row.get(
                "instrument_token", ""))).strip()
            if symbol:
                self._symbol_map[symbol.upper()] = row.to_dict()
            if token:
                self._token_map[token] = row.to_dict()

    def get_all_symbols(self) -> list:
        """Return a list of all NSE equity trading symbols."""
        return list(self._symbol_map.keys())

    def get_by_symbol(self, symbol: str) -> dict | None:
        """Look up instrument details by trading symbol."""
        return self._symbol_map.get(symbol.upper())

    def get_by_token(self, token: str) -> dict | None:
        """Look up instrument details by exchange token."""
        return self._token_map.get(str(token))

    def classify_price_tier(self, price: float) -> str:
        """Classify a stock into a price tier."""
        if price < self.TIER_PENNY_MAX:
            return "penny"
        elif price < self.TIER_SMALL_MAX:
            return "small"
        elif price < self.TIER_MID_MAX:
            return "mid"
        else:
            return "large"

    def get_dataframe(self) -> pd.DataFrame:
        """Return the full instruments DataFrame."""
        if self._instruments_df is None:
            self.load()
        return self._instruments_df.copy()
