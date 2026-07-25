"""
main.py — Day Trading Bot Orchestrator

The entry point that ties everything together.
Runs the full trading day lifecycle from pre-market scan to end-of-day square-off.

Schedule:
  09:00  -> Pre-market scan: select today's stocks
  09:15  -> Market open: subscribe to feeds, start ORB
  09:30  -> Activate VWAP + Momentum strategies
  09:30-14:30 -> Main trading loop
  14:30  -> Stop new entries
  15:10  -> Square off all positions
  15:30  -> Generate daily report

Usage:
  python -m src.main
"""

import os
import sys
import time
import threading
import signal as sig
from datetime import datetime, date, time as dtime
from dotenv import load_dotenv
import yaml
from loguru import logger

# Load environment variables
load_dotenv()

# Setup logging first
from src.storage.logger import setup_logging
setup_logging("INFO")

# Import all components
from src.data.instruments import InstrumentCatalog
from src.data.market_feed import MarketFeed
from src.data.data_fetcher import DataFetcher
from src.scanner.stock_scanner import StockScanner
from src.strategies.vwap_reversion import VWAPReversionStrategy
from src.strategies.momentum_breakout import MomentumBreakoutStrategy
from src.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from src.strategies.strategy_ensemble import StrategyEnsemble
from src.risk.risk_manager import RiskManager
from src.execution.order_manager import OrderManager
from src.execution.portfolio import Portfolio
from src.storage.database import Database
from src.strategies.base_strategy import SignalType
from src.dashboard.dashboard import set_engine, emit_tick, emit_trade, emit_signal, emit_portfolio, start_dashboard


class TradingBot:
    """Main trading bot orchestrator."""

    def __init__(self):
        self._config = self._load_config()
        self._running = False
        self._shutdown = False
        self._engine_state = {}

        # Initialize components
        self._instruments = InstrumentCatalog()
        self._order_manager = OrderManager(self._config)
        self._data_fetcher = DataFetcher()
        self._market_feed = None  # Initialized after auth
        self._scanner = None  # Initialized after instruments load
        self._risk_manager = RiskManager(self._config)
        self._portfolio = Portfolio()
        self._database = Database()

        # Strategies
        strat_cfg = self._config.get("strategies", {})
        self._strategies = []

        if strat_cfg.get("vwap_reversion", {}).get("enabled", True):
            self._strategies.append(VWAPReversionStrategy(strat_cfg.get("vwap_reversion", {})))
        if strat_cfg.get("momentum_breakout", {}).get("enabled", True):
            self._strategies.append(MomentumBreakoutStrategy(strat_cfg.get("momentum_breakout", {})))
        if strat_cfg.get("opening_range_breakout", {}).get("enabled", True):
            self._strategies.append(OpeningRangeBreakoutStrategy(strat_cfg.get("opening_range_breakout", {})))

        self._ensemble = StrategyEnsemble(self._strategies, threshold=0.6)

        # Today's trading state
        self._todays_picks = []
        self._orb_ready = False

        # Dashboard state reference
        self._engine_state = {
            "running": False,
            "mode": self._config.get("trading", {}).get("mode", "paper"),
            "market_open": False,
            "portfolio": self._portfolio,
            "risk_manager": self._risk_manager,
            "ensemble": self._ensemble,
            "database": self._database,
            "scanner_results": [],
            "emergency_squareoff": False,
            "uptime": "",
        }
        set_engine(self._engine_state)

    def _load_config(self) -> dict:
        """Load configuration from config.yaml."""
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            logger.info("Configuration loaded from {}", config_path)
            return config
        except Exception as e:
            logger.error("Failed to load config: {}", e)
            sys.exit(1)

    def start(self):
        """Start the trading bot."""
        logger.info("=" * 60)
        logger.info("  DAY TRADER BOT STARTING")
        logger.info("  Mode: {}", self._config.get("trading", {}).get("mode", "paper").upper())
        logger.info("  Capital: {}", self._config.get("trading", {}).get("capital", 20000))
        logger.info("=" * 60)

        self._running = True
        self._engine_state["running"] = True
        self._engine_state["uptime"] = datetime.now().isoformat()

        # Register signal handlers for graceful shutdown
        sig.signal(sig.SIGINT, self._handle_shutdown)
        sig.signal(sig.SIGTERM, self._handle_shutdown)

        # Step 1: Authenticate with Groww
        self._authenticate()

        # Step 2: Load instruments
        logger.info("Loading instrument catalog...")
        self._instruments.load()

        # Step 3: Initialize scanner
        self._scanner = StockScanner(self._config, self._instruments, self._data_fetcher)

        # Step 4: Start dashboard in background thread
        dashboard_config = self._config.get("dashboard", {})
        dashboard_thread = threading.Thread(
            target=start_dashboard,
            kwargs={
                "host": dashboard_config.get("host", "0.0.0.0"),
                "port": dashboard_config.get("port", 8080),
                "debug": False,
            },
            daemon=True,
        )
        dashboard_thread.start()
        logger.info("Dashboard started on port {}", dashboard_config.get("port", 8080))

        # Step 5: Run the main trading loop
        try:
            self._run_trading_day()
        except Exception as e:
            logger.exception("Fatal error in trading loop: {}", e)
        finally:
            self._cleanup()

    def _authenticate(self):
        """Authenticate with Groww using TOTP."""
        totp_token = os.getenv("GROWW_TOTP_TOKEN", "")
        totp_secret = os.getenv("GROWW_TOTP_SECRET", "")

        if not totp_token or not totp_secret:
            logger.warning(
                "GROWW_TOTP_TOKEN and/or GROWW_TOTP_SECRET not set in .env — "
                "running in paper trading mode only"
            )
            return

        success = self._order_manager.authenticate(totp_token, totp_secret)
        if success:
            # Initialize market feed with the access token
            access_token = self._order_manager.get_access_token_for_feed()
            if access_token:
                self._market_feed = MarketFeed(access_token)
                self._market_feed.connect()
            logger.info("Authentication successful — {} mode active", self._engine_state["mode"])
        else:
            logger.warning("Authentication failed — falling back to paper mode")

    def _run_trading_day(self):
        """Main trading day lifecycle."""
        while self._running and not self._shutdown:
            now = datetime.now()
            current_time = now.time()

            # Check if it's a weekday
            if now.weekday() >= 5:  # Saturday or Sunday
                logger.info("Weekend — market closed. Sleeping until Monday...")
                self._sleep_until_next_trading_day()
                continue

            # ---- Pre-Market Phase (09:00 - 09:15) ----
            if dtime(9, 0) <= current_time < dtime(9, 15):
                if not self._todays_picks:
                    self._pre_market_scan()
                time.sleep(10)
                continue

            # ---- Market Open (09:15 - 09:30): ORB collection phase ----
            if dtime(9, 15) <= current_time < dtime(9, 30):
                self._engine_state["market_open"] = True

                if not self._orb_ready:
                    # Subscribe to feeds
                    if self._market_feed and self._todays_picks:
                        symbols = [p["symbol"] for p in self._todays_picks]
                        self._market_feed.subscribe(symbols)

                    # Collect ORB data (first 15 min)
                    logger.info("Collecting Opening Range data (9:15 - 9:30)...")
                time.sleep(5)
                continue

            # ---- Active Trading Phase (09:30 - 14:30) ----
            if dtime(9, 30) <= current_time < dtime(14, 30):
                # Set ORB ranges if first time entering this phase
                if not self._orb_ready:
                    self._setup_orb_ranges()
                    self._orb_ready = True
                    logger.info("All strategies now active — entering main trading loop")

                # Check emergency square-off
                if self._engine_state.get("emergency_squareoff"):
                    self._square_off_all("EMERGENCY")
                    self._engine_state["emergency_squareoff"] = False
                    continue

                # Main trading iteration
                self._trading_iteration()

                time.sleep(3)  # Poll every 3 seconds
                continue

            # ---- No New Entries Phase (14:30 - 15:10) ----
            if dtime(14, 30) <= current_time < dtime(15, 10):
                # Still monitor open positions for exits
                self._monitor_positions()
                time.sleep(5)
                continue

            # ---- Square-off Phase (15:10 - 15:30) ----
            if dtime(15, 10) <= current_time < dtime(15, 30):
                if self._portfolio.open_count > 0:
                    self._square_off_all("END_OF_DAY")
                time.sleep(10)
                continue

            # ---- Post Market (15:30+) ----
            if current_time >= dtime(15, 30):
                self._end_of_day_report()
                logger.info("Trading day complete. Sleeping until tomorrow...")
                self._reset_for_new_day()
                self._sleep_until_next_trading_day()
                continue

            # ---- Before Market (before 09:00) ----
            if current_time < dtime(9, 0):
                logger.info("Waiting for pre-market scan time (09:00)...")
                time.sleep(30)
                continue

    def _pre_market_scan(self):
        """Run the pre-market stock scanner."""
        logger.info("=== PRE-MARKET SCAN ===")
        try:
            self._todays_picks = self._scanner.scan()
            self._engine_state["scanner_results"] = self._todays_picks

            # Record scanner results in DB
            self._database.record_scanner_results(
                date.today().isoformat(),
                [{**p, "selected": True} for p in self._todays_picks],
            )

            if not self._todays_picks:
                logger.warning("Scanner found no suitable stocks today!")
            else:
                logger.info("Scanner selected {} stocks for today", len(self._todays_picks))

        except Exception as e:
            logger.error("Pre-market scan failed: {}", e)

    def _setup_orb_ranges(self):
        """Set up Opening Range Breakout ranges from first 15 min of data."""
        orb_strategy = None
        for s in self._strategies:
            if isinstance(s, OpeningRangeBreakoutStrategy):
                orb_strategy = s
                break

        if orb_strategy is None:
            return

        orb_strategy.reset_daily()

        for pick in self._todays_picks:
            try:
                df = self._data_fetcher.get_intraday_data(pick["symbol"], interval="1m")
                if not df.empty:
                    orb_strategy.detect_opening_range(df, pick["symbol"])
            except Exception as e:
                logger.debug("ORB setup failed for {}: {}", pick["symbol"], e)

    def _trading_iteration(self):
        """Single iteration of the main trading loop."""
        for pick in self._todays_picks:
            symbol = pick["symbol"]

            try:
                # 1. Get latest price
                current_price = self._get_current_price(symbol)
                if current_price is None or current_price <= 0:
                    continue

                # 2. Check existing position exits
                if self._portfolio.has_position(symbol):
                    self._portfolio.update_price(symbol, current_price)
                    should_exit, reason = self._risk_manager.check_position_exits(symbol, current_price)
                    if should_exit:
                        self._close_position(symbol, current_price, reason)
                    continue  # Skip new signals for symbols we already hold

                # 3. Get intraday data for indicators
                df = self._data_fetcher.get_intraday_data(symbol, interval="5m")
                if df.empty or len(df) < 30:
                    continue

                # 4. Calculate indicators per strategy
                dataframes = {}
                for strategy in self._strategies:
                    try:
                        strategy_df = strategy.calculate_indicators(df.copy())
                        dataframes[strategy.name] = strategy_df
                    except Exception as e:
                        logger.debug("Indicator calc failed for {} / {}: {}", symbol, strategy.name, e)

                # 5. Run ensemble
                signal = self._ensemble.evaluate(dataframes, symbol)
                if signal is None:
                    continue

                # 6. Emit signal to dashboard
                emit_signal({
                    "symbol": signal.symbol,
                    "signal_type": signal.signal_type.value,
                    "confidence": signal.confidence,
                    "reason": signal.reason,
                    "strategy": signal.strategy_name,
                    "time": datetime.now().strftime("%H:%M:%S"),
                })

                # 7. Record signal in DB
                self._database.record_signal({
                    "symbol": signal.symbol,
                    "signal_type": signal.signal_type.value,
                    "strategy": signal.strategy_name,
                    "confidence": signal.confidence,
                    "price": signal.price,
                    "reason": signal.reason,
                })

                # 8. Check risk rules
                sector = pick.get("sector", "other")
                can_trade, deny_reason = self._risk_manager.can_trade(signal, sector)
                if not can_trade:
                    logger.debug("Trade blocked for {}: {}", symbol, deny_reason)
                    continue

                # 9. Calculate position size
                quantity = self._risk_manager.calculate_position_size(signal, current_price)
                if quantity <= 0:
                    continue

                # 10. Place order
                self._execute_trade(signal, quantity, current_price, sector)

            except Exception as e:
                logger.error("Trading iteration error for {}: {}", symbol, e)

        # Emit portfolio update to dashboard
        emit_portfolio(self._portfolio.to_dict())

        # Emit P&L tick
        emit_tick("PORTFOLIO", 0, self._portfolio.total_pnl)

    def _monitor_positions(self):
        """Monitor open positions for exits (used after 14:30 when no new trades allowed)."""
        for symbol, position in list(self._portfolio.get_open_positions().items()):
            try:
                current_price = self._get_current_price(symbol)
                if current_price is None:
                    continue

                self._portfolio.update_price(symbol, current_price)
                should_exit, reason = self._risk_manager.check_position_exits(symbol, current_price)
                if should_exit:
                    self._close_position(symbol, current_price, reason)
            except Exception as e:
                logger.error("Position monitoring error for {}: {}", symbol, e)

    def _get_current_price(self, symbol: str) -> float | None:
        """Get the current price for a symbol."""
        # Try live feed first
        if self._market_feed:
            price = self._market_feed.get_ltp(symbol)
            if price:
                return price

        # Fallback to data fetcher
        return self._data_fetcher.get_latest_close(symbol)

    def _execute_trade(self, signal, quantity: int, price: float, sector: str):
        """Execute a trade based on a signal."""
        stop_loss = signal.stop_loss or self._risk_manager.calculate_stop_loss(price, signal.signal_type)
        take_profit = signal.take_profit or self._risk_manager.calculate_take_profit(price, signal.signal_type)

        order = self._order_manager.place_order(
            symbol=signal.symbol,
            signal_type=signal.signal_type,
            quantity=quantity,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=signal.strategy_name,
        )

        if order and order.status.value in ("EXECUTED", "PLACED"):
            executed_price = order.executed_price or price

            # Register with risk manager
            self._risk_manager.register_position(
                signal.symbol, quantity, executed_price, signal.signal_type, sector
            )

            # Register with portfolio
            self._portfolio.open_position(
                symbol=signal.symbol,
                signal_type=signal.signal_type,
                quantity=quantity,
                entry_price=executed_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=signal.strategy_name,
                sector=sector,
                order_id=order.order_id,
            )

            # Emit to dashboard
            emit_trade({
                "symbol": signal.symbol,
                "type": signal.signal_type.value,
                "quantity": quantity,
                "price": executed_price,
                "strategy": signal.strategy_name,
            })

            logger.info(
                "TRADE EXECUTED: {} {} x{} @ {:.2f} | SL={:.2f} TP={:.2f} | {}",
                signal.signal_type.value, signal.symbol, quantity,
                executed_price, stop_loss, take_profit, signal.strategy_name,
            )

    def _close_position(self, symbol: str, exit_price: float, reason: str):
        """Close a position and record it."""
        position = self._portfolio.get_position(symbol)
        if position is None:
            return

        # Place exit order
        exit_signal_type = SignalType.SELL if position.signal_type == "BUY" else SignalType.BUY

        self._order_manager.place_order(
            symbol=symbol,
            signal_type=exit_signal_type,
            quantity=position.quantity,
            price=exit_price,
            strategy=f"EXIT: {reason}",
        )

        # Update risk manager
        pnl = self._risk_manager.close_position(symbol, exit_price)

        # Update portfolio
        closed = self._portfolio.close_position(symbol, exit_price)

        # Record in database
        if closed:
            self._database.record_trade({
                "symbol": symbol,
                "transaction_type": position.signal_type,
                "quantity": position.quantity,
                "entry_price": position.entry_price,
                "exit_price": exit_price,
                "pnl": closed.realized_pnl,
                "strategy": position.strategy,
                "sector": position.sector,
                "entry_time": position.entry_time.isoformat(),
                "exit_time": datetime.now().isoformat(),
                "paper": self._order_manager.is_paper,
            })

        # Emit to dashboard
        emit_trade({
            "symbol": symbol,
            "type": "CLOSE",
            "exit_price": exit_price,
            "pnl": pnl,
            "reason": reason,
        })

        logger.info("POSITION CLOSED: {} @ {:.2f} | PnL={:.2f} | Reason: {}", symbol, exit_price, pnl, reason)

    def _square_off_all(self, reason: str = "END_OF_DAY"):
        """Close all open positions."""
        positions = self._portfolio.get_open_positions()
        if not positions:
            return

        logger.warning("=== SQUARE OFF ALL ({}) — {} positions ===", reason, len(positions))

        for symbol in list(positions.keys()):
            try:
                price = self._get_current_price(symbol)
                if price:
                    self._close_position(symbol, price, reason)
                else:
                    logger.error("Cannot square off {} — no price available", symbol)
            except Exception as e:
                logger.error("Square-off error for {}: {}", symbol, e)

    def _end_of_day_report(self):
        """Generate and save end-of-day report."""
        metrics = self._portfolio.get_performance_metrics()
        risk_stats = self._risk_manager.get_daily_stats()

        logger.info("=" * 60)
        logger.info("  END OF DAY REPORT — {}", date.today().isoformat())
        logger.info("  Total P&L: {:.2f}", metrics.get("total_pnl", 0))
        logger.info("  Realized: {:.2f}", metrics.get("realized_pnl", 0))
        logger.info("  Trades: {} (W:{} L:{})", metrics.get("total_trades", 0),
                     metrics.get("winning_trades", 0), metrics.get("losing_trades", 0))
        logger.info("  Win Rate: {:.1f}%", metrics.get("win_rate", 0))
        logger.info("  Profit Factor: {:.2f}", metrics.get("profit_factor", 0))
        logger.info("  Avg Holding: {:.0f} min", metrics.get("avg_holding_mins", 0))
        logger.info("=" * 60)

        # Save to database
        self._database.save_daily_summary({
            "trade_date": date.today().isoformat(),
            "total_pnl": metrics.get("total_pnl", 0),
            "realized_pnl": metrics.get("realized_pnl", 0),
            "total_trades": metrics.get("total_trades", 0),
            "winning_trades": metrics.get("winning_trades", 0),
            "losing_trades": metrics.get("losing_trades", 0),
            "win_rate": metrics.get("win_rate", 0),
            "capital_start": self._config.get("trading", {}).get("capital", 20000),
            "capital_end": risk_stats.get("capital_available", 0),
            "stocks_scanned": len(self._todays_picks),
            "stocks_traded": [p["symbol"] for p in self._todays_picks],
        })

    def _reset_for_new_day(self):
        """Reset all daily state."""
        self._todays_picks = []
        self._orb_ready = False
        self._risk_manager.reset_daily()
        self._portfolio.reset_daily()
        self._data_fetcher.clear_cache()
        self._engine_state["scanner_results"] = []
        self._engine_state["market_open"] = False

        # Reset ORB strategy
        for s in self._strategies:
            if isinstance(s, OpeningRangeBreakoutStrategy):
                s.reset_daily()

        # Re-authenticate (TOTP generates fresh token daily)
        self._authenticate()

        logger.info("All state reset for new trading day")

    def _sleep_until_next_trading_day(self):
        """Sleep until the next market day at 08:55 AM."""
        now = datetime.now()
        
        # Target 08:55 AM today
        next_day = now.replace(hour=8, minute=55, second=0, microsecond=0)
        
        # If we're already past 08:55 AM today, the next trading session starts tomorrow
        if now >= next_day:
            next_day += __import__("datetime").timedelta(days=1)
            
        # If the target day lands on a weekend, push it forward to Monday
        while next_day.weekday() >= 5:  # 5=Saturday, 6=Sunday
            next_day += __import__("datetime").timedelta(days=1)

        sleep_seconds = (next_day - now).total_seconds()
        if sleep_seconds > 0:
            logger.info("Sleeping for {:.2f} hours until {}", sleep_seconds / 3600, next_day)
            # Sleep in chunks to allow graceful shutdown
            while sleep_seconds > 0 and not self._shutdown:
                time.sleep(min(sleep_seconds, 60))
                sleep_seconds -= 60

    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown."""
        logger.warning("Shutdown signal received — closing all positions...")
        self._shutdown = True
        self._running = False

        # Square off everything
        if self._portfolio.open_count > 0:
            self._square_off_all("SHUTDOWN")

        self._cleanup()
        sys.exit(0)

    def _cleanup(self):
        """Clean up resources."""
        self._running = False
        self._engine_state["running"] = False

        if self._market_feed:
            self._market_feed.disconnect()
        self._database.close()

        logger.info("Trading bot stopped cleanly")


def main():
    """Entry point."""
    bot = TradingBot()
    bot.start()


if __name__ == "__main__":
    main()
