"""
risk_manager.py — Position Sizing & Risk Management

The most critical component — protects your capital from catastrophic losses.
Enforces per-trade limits, daily limits, position sizing, and sector exposure.
"""

from datetime import datetime, date
from loguru import logger

from src.strategies.base_strategy import Signal, SignalType


class RiskManager:
    """
    Enforces all risk management rules before any trade is executed.
    Every order must pass through the risk manager.
    """

    def __init__(self, config: dict):
        risk_cfg = config.get("risk", {})
        trading_cfg = config.get("trading", {})

        self._capital = trading_cfg.get("capital", 20000)
        self._max_per_trade = trading_cfg.get("max_per_trade", 4000)
        self._max_open_positions = trading_cfg.get("max_open_positions", 6)
        self._max_daily_loss = trading_cfg.get("max_daily_loss", 400)

        self._stop_loss_pct = risk_cfg.get("stop_loss_pct", 1.0) / 100
        self._take_profit_pct = risk_cfg.get("take_profit_pct", 2.0) / 100
        self._trailing_stop_pct = risk_cfg.get("trailing_stop_pct", 0.5) / 100
        self._max_drawdown_pct = risk_cfg.get("max_drawdown_pct", 2.0) / 100
        self._max_sector_positions = risk_cfg.get("max_sector_positions", 3)

        # Daily state
        self._daily_pnl = 0.0
        self._daily_trades = 0
        self._daily_date = date.today()
        self._open_positions = {}  # symbol -> position dict
        self._sector_positions = {}  # sector -> count
        self._halted = False

    def reset_daily(self):
        """Reset daily counters at the start of each day."""
        self._daily_pnl = 0.0
        self._daily_trades = 0
        self._daily_date = date.today()
        self._halted = False
        self._open_positions.clear()
        self._sector_positions.clear()
        logger.info("Risk manager reset for new trading day")

    def can_trade(self, signal: Signal, sector: str = "other") -> tuple[bool, str]:
        """
        Check if a trade is allowed based on all risk rules.

        Returns:
            (allowed: bool, reason: str)
        """
        # Auto-reset if new day
        if date.today() != self._daily_date:
            self.reset_daily()

        # Rule 1: Trading halted?
        if self._halted:
            return False, "Trading halted — daily loss limit reached"

        # Rule 2: Daily loss limit
        if self._daily_pnl <= -self._max_daily_loss:
            self._halted = True
            logger.warning("DAILY LOSS LIMIT HIT: PnL={:.2f}", self._daily_pnl)
            return False, f"Daily loss limit reached (PnL: {self._daily_pnl:.2f})"

        # Rule 3: Max open positions
        if len(self._open_positions) >= self._max_open_positions:
            return False, f"Max open positions ({self._max_open_positions}) reached"

        # Rule 4: Already have a position in this symbol
        if signal.symbol in self._open_positions:
            return False, f"Already have an open position in {signal.symbol}"

        # Rule 5: Sector exposure limit
        sector_count = self._sector_positions.get(sector, 0)
        if sector_count >= self._max_sector_positions:
            return False, f"Max sector positions ({self._max_sector_positions}) reached for {sector}"

        # Rule 6: Time-based rules
        now = datetime.now().time()
        from datetime import time as dtime
        if now > dtime(14, 30):  # No new entries after 2:30 PM
            return False, "No new entries after 14:30"
        if now < dtime(9, 15):  # Market not open
            return False, "Market not open yet"

        # Rule 7: Check available capital
        available = self._get_available_capital()
        if available < signal.price:  # Can't even buy 1 unit
            return False, f"Insufficient capital (available: {available:.2f})"

        return True, "Trade approved"

    def calculate_position_size(self, signal: Signal, current_price: float) -> int:
        """
        Calculate the number of shares to trade based on risk rules.
        Uses ATR-based or fixed percentage position sizing.
        """
        if current_price <= 0:
            return 0

        # Max capital for this trade
        available = self._get_available_capital()
        max_for_trade = min(self._max_per_trade, available)

        # Calculate based on stop loss distance
        if signal.stop_loss > 0:
            if signal.signal_type == SignalType.BUY:
                risk_per_share = current_price - signal.stop_loss
            else:
                risk_per_share = signal.stop_loss - current_price

            if risk_per_share > 0:
                # Risk max 1% of capital per trade
                max_risk = self._capital * self._stop_loss_pct
                size_by_risk = int(max_risk / risk_per_share)
            else:
                size_by_risk = int(max_for_trade / current_price)
        else:
            size_by_risk = int(max_for_trade / current_price)

        # Size by capital limit
        size_by_capital = int(max_for_trade / current_price)

        # Take the smaller of the two
        quantity = min(size_by_risk, size_by_capital)

        # Minimum 1 share
        return max(quantity, 1) if quantity > 0 else 0

    def calculate_stop_loss(self, entry_price: float, signal_type: SignalType) -> float:
        """Calculate default stop loss price."""
        if signal_type == SignalType.BUY:
            return entry_price * (1 - self._stop_loss_pct)
        elif signal_type == SignalType.SELL:
            return entry_price * (1 + self._stop_loss_pct)
        return 0

    def calculate_take_profit(self, entry_price: float, signal_type: SignalType) -> float:
        """Calculate default take profit price."""
        if signal_type == SignalType.BUY:
            return entry_price * (1 + self._take_profit_pct)
        elif signal_type == SignalType.SELL:
            return entry_price * (1 - self._take_profit_pct)
        return 0

    def calculate_trailing_stop(self, entry_price: float, current_price: float,
                                 current_stop: float, signal_type: SignalType) -> float:
        """
        Calculate trailing stop price.
        Only activates once the trade is in profit by at least 1%.
        """
        if signal_type == SignalType.BUY:
            profit_pct = (current_price - entry_price) / entry_price
            if profit_pct >= self._stop_loss_pct:  # In profit by at least SL%
                new_stop = current_price * (1 - self._trailing_stop_pct)
                return max(new_stop, current_stop)  # Only move stop up, never down
        elif signal_type == SignalType.SELL:
            profit_pct = (entry_price - current_price) / entry_price
            if profit_pct >= self._stop_loss_pct:
                new_stop = current_price * (1 + self._trailing_stop_pct)
                return min(new_stop, current_stop) if current_stop > 0 else new_stop

        return current_stop

    def register_position(self, symbol: str, quantity: int, entry_price: float,
                          signal_type: SignalType, sector: str = "other"):
        """Register a new open position."""
        self._open_positions[symbol] = {
            "quantity": quantity,
            "entry_price": entry_price,
            "signal_type": signal_type.value,
            "sector": sector,
            "entry_time": datetime.now(),
            "stop_loss": self.calculate_stop_loss(entry_price, signal_type),
            "take_profit": self.calculate_take_profit(entry_price, signal_type),
            "highest_price": entry_price,
            "lowest_price": entry_price,
        }
        self._sector_positions[sector] = self._sector_positions.get(sector, 0) + 1
        self._daily_trades += 1
        logger.info(
            "Position registered: {} {} x{} @ {:.2f}",
            signal_type.value, symbol, quantity, entry_price,
        )

    def close_position(self, symbol: str, exit_price: float) -> float:
        """Close a position and update daily P&L. Returns realized P&L."""
        pos = self._open_positions.pop(symbol, None)
        if pos is None:
            return 0.0

        # Update sector count
        sector = pos.get("sector", "other")
        self._sector_positions[sector] = max(0, self._sector_positions.get(sector, 0) - 1)

        # Calculate P&L
        if pos["signal_type"] == SignalType.BUY.value:
            pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["quantity"]

        self._daily_pnl += pnl
        logger.info(
            "Position closed: {} @ {:.2f} -> {:.2f} | PnL={:.2f} | Daily PnL={:.2f}",
            symbol, pos["entry_price"], exit_price, pnl, self._daily_pnl,
        )

        return pnl

    def check_position_exits(self, symbol: str, current_price: float) -> tuple[bool, str]:
        """
        Check if an open position should be exited (stop loss, take profit, trailing stop).

        Returns:
            (should_exit: bool, reason: str)
        """
        pos = self._open_positions.get(symbol)
        if pos is None:
            return False, ""

        entry_price = pos["entry_price"]
        stop_loss = pos["stop_loss"]
        take_profit = pos["take_profit"]
        signal_type = SignalType(pos["signal_type"])

        # Update price extremes
        pos["highest_price"] = max(pos["highest_price"], current_price)
        pos["lowest_price"] = min(pos["lowest_price"], current_price)

        # Update trailing stop
        trailing = self.calculate_trailing_stop(
            entry_price, current_price, stop_loss, signal_type
        )
        if trailing != stop_loss:
            pos["stop_loss"] = trailing
            stop_loss = trailing

        if signal_type == SignalType.BUY:
            # Stop loss hit
            if current_price <= stop_loss:
                return True, f"STOP_LOSS hit ({current_price:.2f} <= {stop_loss:.2f})"
            # Take profit hit
            if current_price >= take_profit:
                return True, f"TAKE_PROFIT hit ({current_price:.2f} >= {take_profit:.2f})"
        elif signal_type == SignalType.SELL:
            if current_price >= stop_loss:
                return True, f"STOP_LOSS hit ({current_price:.2f} >= {stop_loss:.2f})"
            if current_price <= take_profit:
                return True, f"TAKE_PROFIT hit ({current_price:.2f} <= {take_profit:.2f})"

        return False, ""


    def _get_available_capital(self) -> float:
        """Calculate available capital after accounting for open positions."""
        used = sum(
            pos["entry_price"] * pos["quantity"]
            for pos in self._open_positions.values()
        )
        return max(0, self._capital - used + self._daily_pnl)

    def get_open_positions(self) -> dict:
        """Get all open positions."""
        return dict(self._open_positions)

    def get_daily_stats(self) -> dict:
        """Get daily trading statistics."""
        return {
            "date": self._daily_date.isoformat(),
            "pnl": self._daily_pnl,
            "trades": self._daily_trades,
            "open_positions": len(self._open_positions),
            "capital_used": sum(
                p["entry_price"] * p["quantity"]
                for p in self._open_positions.values()
            ),
            "capital_available": self._get_available_capital(),
            "halted": self._halted,
        }

    @property
    def is_halted(self) -> bool:
        return self._halted
