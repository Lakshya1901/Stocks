"""
stock_scanner.py — Full NSE Universe Stock Scanner

Scans all 1800+ NSE equities every morning and picks the top 50
most likely to move profitably during the trading session.
"""

import pandas as pd
import ta as ta_lib
from datetime import datetime
from loguru import logger
from src.data.instruments import InstrumentCatalog
from src.data.data_fetcher import DataFetcher


class StockScanner:
    """
    Multi-stage filtering pipeline that scans the entire NSE universe
    and selects the best intraday trading candidates.
    """

    # Known sector mappings for diversification
    SECTOR_MAP = {
        # Banking
        "SBIN": "banking", "ICICIBANK": "banking", "HDFCBANK": "banking",
        "AXISBANK": "banking", "KOTAKBANK": "banking", "BANKBARODA": "banking",
        "CANBK": "banking", "PNB": "banking", "IDFCFIRSTB": "banking",
        "INDUSINDBK": "banking", "FEDERALBNK": "banking", "BANDHANBNK": "banking",
        # IT
        "TCS": "it", "INFY": "it", "HCLTECH": "it", "WIPRO": "it",
        "TECHM": "it", "LTIM": "it", "MPHASIS": "it", "COFORGE": "it",
        # Auto
        "TATAMOTORS": "auto", "M&M": "auto", "BAJAJ-AUTO": "auto",
        "MARUTI": "auto", "HEROMOTOCO": "auto", "EICHERMOT": "auto",
        "TVSMOTOR": "auto", "ASHOKLEY": "auto",
        # Energy
        "RELIANCE": "energy", "ONGC": "energy", "IOC": "energy",
        "BPCL": "energy", "GAIL": "energy", "NTPC": "energy",
        "POWERGRID": "energy", "ADANIGREEN": "energy", "TATAPOWER": "energy",
        # Telecom
        "BHARTIARTL": "telecom", "IDEA": "telecom",
        # Consumer
        "HINDUNILVR": "consumer", "ITC": "consumer", "NESTLEIND": "consumer",
        "BRITANNIA": "consumer", "DABUR": "consumer", "MARICO": "consumer",
        "TATACONSUM": "consumer", "GODREJCP": "consumer",
        # Metals
        "TATASTEEL": "metals", "HINDALCO": "metals", "JSWSTEEL": "metals",
        "VEDL": "metals", "COALINDIA": "metals", "NMDC": "metals",
        # Pharma
        "SUNPHARMA": "pharma", "DRREDDY": "pharma", "CIPLA": "pharma",
        "DIVISLAB": "pharma", "APOLLOHOSP": "pharma", "BIOCON": "pharma",
        # Others
        "ADANIPORTS": "infra", "LT": "infra", "ULTRACEMCO": "infra",
        "ZOMATO": "platform", "PAYTM": "platform",
    }

    def __init__(self, config: dict, instruments: InstrumentCatalog, data_fetcher: DataFetcher):
        self._config = config.get("scanner", {})
        self._instruments = instruments
        self._fetcher = data_fetcher
        self._top_picks = self._config.get("top_picks", 50)
        self._min_volume = self._config.get("min_avg_volume", 500000)
        self._min_atr_pct = self._config.get("min_atr_pct", 1.5)
        self._min_price = self._config.get("min_price", 1)
        self._max_price = self._config.get("max_price", 50000)
        self._max_sector_positions = config.get("risk", {}).get("max_sector_positions", 3)

    def scan(self) -> list[dict]:
        """
        Run the full scanning pipeline.

        Returns:
            List of dicts with keys: symbol, score, price, volume,
            atr_pct, tier, sector, signals
        """
        start_time = datetime.now()
        logger.info("=== PRE-MARKET SCAN STARTED ===")

        # Stage 1: Load universe
        all_symbols = self._instruments.get_all_symbols()
        logger.info("Stage 1 — Universe: {} NSE equities loaded", len(all_symbols))

        # Stage 2: Liquidity filter
        liquid_stocks = self._filter_liquidity(all_symbols)
        logger.info("Stage 2 — Liquidity: {} stocks passed (volume > {})", len(liquid_stocks), self._min_volume)

        # Stage 3: Volatility filter
        volatile_stocks = self._filter_volatility(liquid_stocks)
        logger.info("Stage 3 — Volatility: {} stocks passed (ATR% > {})", len(volatile_stocks), self._min_atr_pct)

        # Stage 4: Price action analysis
        movers = self._filter_price_action(volatile_stocks)
        logger.info("Stage 4 — Price Action: {} stocks showing movement", len(movers))

        # Stage 5: Momentum scoring
        scored = self._score_momentum(movers)
        logger.info("Stage 5 — Momentum: {} stocks scored", len(scored))

        # Stage 6: Composite ranking + sector diversification
        picks = self._rank_and_diversify(scored)
        logger.info("Stage 6 — Final picks: {} stocks selected", len(picks))

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info("=== SCAN COMPLETE in {:.1f}s ===", elapsed)

        for i, pick in enumerate(picks, 1):
            logger.info(
                "  #{}: {} | price={:.2f} | score={:.3f} | tier={} | sector={}",
                i, pick["symbol"], pick["price"], pick["score"],
                pick["tier"], pick["sector"],
            )

        return picks

    def _filter_liquidity(self, symbols: list) -> list[dict]:
        """Stage 2: Filter stocks by average daily volume."""
        results = []

        for symbol in symbols:
            try:
                avg_vol = self._fetcher.get_average_volume(symbol, days=20)
                if avg_vol is None or avg_vol < self._min_volume:
                    continue

                close = self._fetcher.get_latest_close(symbol)
                if close is None or close < self._min_price or close > self._max_price:
                    continue

                results.append({
                    "symbol": symbol,
                    "price": close,
                    "avg_volume": avg_vol,
                    "tier": self._instruments.classify_price_tier(close),
                    "sector": self.SECTOR_MAP.get(symbol, "other"),
                })

            except Exception as e:
                logger.debug("Liquidity check failed for {}: {}", symbol, e)
                continue

        return results

    def _filter_volatility(self, stocks: list[dict]) -> list[dict]:
        """Stage 3: Filter by ATR as percentage of price."""
        results = []

        for stock in stocks:
            try:
                df = self._fetcher.get_historical_data(
                    stock["symbol"], interval="1d", days=20
                )
                if df.empty or len(df) < 14:
                    continue

                # Calculate ATR(14) using ta library
                atr_indicator = ta_lib.volatility.AverageTrueRange(
                    high=df["high"], low=df["low"], close=df["close"], window=14
                )
                atr_series = atr_indicator.average_true_range()

                if atr_series is None or atr_series.empty:
                    continue

                current_atr = float(atr_series.iloc[-1])
                atr_pct = (current_atr / stock["price"]) * 100

                if atr_pct < self._min_atr_pct:
                    continue

                stock["atr"] = current_atr
                stock["atr_pct"] = atr_pct
                results.append(stock)

            except Exception as e:
                logger.debug("Volatility check failed for {}: {}", stock["symbol"], e)
                continue

        return results

    def _filter_price_action(self, stocks: list[dict]) -> list[dict]:
        """Stage 4: Filter for stocks showing interesting price action."""
        results = []

        for stock in stocks:
            try:
                df = self._fetcher.get_historical_data(
                    stock["symbol"], interval="1d", days=5
                )
                if df.empty or len(df) < 2:
                    continue

                prev_close = float(df["close"].iloc[-2])
                latest_close = float(df["close"].iloc[-1])
                latest_volume = float(df["volume"].iloc[-1]) if "volume" in df.columns else 0

                # Gap percentage
                gap_pct = ((latest_close - prev_close) / prev_close) * 100

                # Volume spike
                volume_ratio = latest_volume / stock["avg_volume"] if stock["avg_volume"] > 0 else 0

                # Check if stock is near recent high/low (support/resistance)
                recent_high = float(df["high"].max())
                recent_low = float(df["low"].min())
                near_high = (recent_high - latest_close) / latest_close < 0.02
                near_low = (latest_close - recent_low) / latest_close < 0.02

                # Pass if any condition met
                has_gap = abs(gap_pct) > 0.8
                has_volume_spike = volume_ratio > 1.5
                near_level = near_high or near_low

                if has_gap or has_volume_spike or near_level:
                    stock["gap_pct"] = gap_pct
                    stock["volume_ratio"] = volume_ratio
                    stock["near_high"] = near_high
                    stock["near_low"] = near_low
                    results.append(stock)

            except Exception as e:
                logger.debug("Price action check failed for {}: {}", stock["symbol"], e)
                continue

        return results

    def _score_momentum(self, stocks: list[dict]) -> list[dict]:
        """Stage 5: Score each stock by momentum indicators."""
        results = []

        for stock in stocks:
            try:
                df = self._fetcher.get_historical_data(
                    stock["symbol"], interval="1d", days=30
                )
                if df.empty or len(df) < 20:
                    continue

                score = 0.0
                signals = []

                # RSI momentum (0-1)
                rsi_indicator = ta_lib.momentum.RSIIndicator(close=df["close"], window=14)
                rsi_series = rsi_indicator.rsi()
                if rsi_series is not None and not rsi_series.empty:
                    rsi_val = float(rsi_series.iloc[-1])
                    stock["rsi"] = rsi_val
                    if rsi_val < 30:
                        score += 0.3  # Oversold — bounce potential
                        signals.append("RSI_OVERSOLD")
                    elif rsi_val > 70:
                        score += 0.25  # Overbought — short potential
                        signals.append("RSI_OVERBOUGHT")
                    elif 45 < rsi_val < 55:
                        score += 0.1  # Neutral
                    else:
                        score += 0.15

                # MACD trend (0-1)
                macd_indicator = ta_lib.trend.MACD(
                    close=df["close"], window_slow=26, window_fast=12, window_sign=9
                )
                macd_hist = macd_indicator.macd_diff()
                if macd_hist is not None and not macd_hist.empty and len(macd_hist) >= 2:
                    current_hist = float(macd_hist.iloc[-1])
                    prev_hist = float(macd_hist.iloc[-2])
                    if current_hist > 0 and current_hist > prev_hist:
                        score += 0.25
                        signals.append("MACD_BULLISH")
                    elif current_hist < 0 and current_hist < prev_hist:
                        score += 0.2
                        signals.append("MACD_BEARISH")
                    else:
                        score += 0.1

                # Volume trend (0-1)
                if "volume" in df.columns and len(df) >= 5:
                    recent_vol = df["volume"].tail(5).mean()
                    older_vol = df["volume"].head(len(df) - 5).mean()
                    if older_vol > 0:
                        vol_trend = recent_vol / older_vol
                        if vol_trend > 1.5:
                            score += 0.25
                            signals.append("VOL_SURGE")
                        elif vol_trend > 1.0:
                            score += 0.15
                        else:
                            score += 0.05

                # Price trend — 5-day returns
                if len(df) >= 5:
                    ret_5d = (float(df["close"].iloc[-1]) / float(df["close"].iloc[-5]) - 1) * 100
                    stock["return_5d"] = ret_5d
                    if abs(ret_5d) > 3:
                        score += 0.2
                        signals.append("STRONG_TREND")
                    elif abs(ret_5d) > 1:
                        score += 0.1

                # ATR bonus — higher ATR% = more intraday opportunity
                atr_bonus = min(stock.get("atr_pct", 0) / 10, 0.2)
                score += atr_bonus

                # Gap bonus
                gap_bonus = min(abs(stock.get("gap_pct", 0)) / 5, 0.15)
                score += gap_bonus

                stock["score"] = score
                stock["signals"] = signals
                results.append(stock)

            except Exception as e:
                logger.debug("Momentum scoring failed for {}: {}", stock["symbol"], e)
                continue

        return results

    def _rank_and_diversify(self, stocks: list[dict]) -> list[dict]:
        """Stage 6: Rank by composite score and enforce sector diversification."""
        if not stocks:
            return []

        # Sort by score descending
        stocks.sort(key=lambda x: x.get("score", 0), reverse=True)

        # Enforce sector diversification (relaxed for 50 picks)
        picks = []
        sector_counts = {}
        max_per_sector = max(self._max_sector_positions, 8)  # Allow more per sector for 50 picks

        for stock in stocks:
            if len(picks) >= self._top_picks:
                break

            sector = stock.get("sector", "other")
            current_count = sector_counts.get(sector, 0)

            if current_count >= max_per_sector:
                continue

            picks.append(stock)
            sector_counts[sector] = current_count + 1

        return picks

    def get_capital_allocation(self, picks: list[dict], total_capital: float) -> dict:
        """
        Calculate capital allocation per stock based on price tier.

        Returns:
            Dict of symbol -> allocated capital amount
        """
        # Tier allocation weights
        tier_weights = {
            "penny": 0.15,
            "small": 0.30,
            "mid": 0.35,
            "large": 0.20,
        }

        # Count picks per tier
        tier_counts = {}
        for pick in picks:
            tier = pick.get("tier", "mid")
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        # Calculate per-stock allocation
        allocations = {}
        for pick in picks:
            tier = pick.get("tier", "mid")
            tier_capital = total_capital * tier_weights.get(tier, 0.25)
            count = tier_counts.get(tier, 1)
            per_stock = tier_capital / count
            allocations[pick["symbol"]] = per_stock

        return allocations
