"""
portfolio.py — Position & P&L Tracker

Tracks all open and closed positions with real-time P&L computation.
"""

from datetime import datetime, date
from dataclasses import dataclass
from loguru import logger

from src.strategies.base_strategy import SignalType


@dataclass
class Position:
    """An open or closed trading position."""
    symbol: str
    signal_type: str  # BUY or SELL
    quantity: int
    entry_price: float
    entry_time: datetime
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trailing_stop: float = 0.0
    current_price: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    exit_price: float = 0.0
    exit_time: datetime | None = None
    strategy: str = ""
    sector: str = "other"
    order_id: str = ""
    is_open: bool = True

    @property
    def unrealized_pnl(self) -> float:
        if not self.is_open or self.current_price == 0:
            return 0.0
        if self.signal_type == "BUY":
            return (self.current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.current_price) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float:
        cost = self.entry_price * self.quantity
        if cost == 0:
            return 0.0
        return (self.unrealized_pnl / cost) * 100

    @property
    def realized_pnl(self) -> float:
        if self.is_open or self.exit_price == 0:
            return 0.0
        if self.signal_type == "BUY":
            return (self.exit_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.exit_price) * self.quantity

    @property
    def holding_duration(self) -> float:
        """Duration in minutes."""
        end = self.exit_time or datetime.now()
        return (end - self.entry_time).total_seconds() / 60


class Portfolio:
    """Manages all positions and computes portfolio-level metrics."""

    def __init__(self):
        self._open_positions = {}  # symbol -> Position
        self._closed_positions = []  # List of closed Position objects
        self._daily_realized_pnl = 0.0
        self._daily_date = date.today()

    def reset_daily(self):
        """Reset for a new trading day."""
        self._daily_realized_pnl = 0.0
        self._daily_date = date.today()
        self._open_positions.clear()
        logger.info("Portfolio reset for new day")

    def restore_position(self, pos_data: dict):
        """Restore an open position from database."""
        # Convert entry_time string to datetime
        try:
            from datetime import datetime as dt
            entry_time = dt.fromisoformat(pos_data["entry_time"])
        except Exception:
            entry_time = datetime.now()

        pos = Position(
            symbol=pos_data["symbol"],
            signal_type=pos_data["signal_type"],
            quantity=pos_data["quantity"],
            entry_price=pos_data["entry_price"],
            entry_time=entry_time,
            stop_loss=pos_data["stop_loss"],
            take_profit=pos_data["take_profit"],
            trailing_stop=pos_data["trailing_stop"],
            current_price=pos_data["entry_price"],
            highest_price=pos_data.get(
                "highest_price", pos_data["entry_price"]),
            lowest_price=pos_data.get("lowest_price", pos_data["entry_price"]),
            strategy=pos_data.get("strategy", ""),
            sector=pos_data.get("sector", "other"),
            order_id=pos_data.get("order_id", ""),
        )
        self._open_positions[pos.symbol] = pos
        logger.info("Restored open position: {} {} x{}",
                    pos.signal_type, pos.symbol, pos.quantity)
        return pos

    def open_position(
        self,
        symbol: str,
        signal_type: SignalType,
        quantity: int,
        entry_price: float,
        stop_loss: float = 0,
        take_profit: float = 0,
        strategy: str = "",
        sector: str = "other",
        order_id: str = "",
    ) -> Position:
        """Record a new open position."""
        pos = Position(
            symbol=symbol,
            signal_type=signal_type.value,
            quantity=quantity,
            entry_price=entry_price,
            entry_time=datetime.now(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=stop_loss,
            current_price=entry_price,
            highest_price=entry_price,
            lowest_price=entry_price,
            strategy=strategy,
            sector=sector,
            order_id=order_id,
        )

        self._open_positions[symbol] = pos
        logger.info(
            "Position opened: {} {} x{} @ {:.2f} | SL={:.2f} TP={:.2f}",
            signal_type.value, symbol, quantity, entry_price, stop_loss, take_profit,
        )
        return pos

    def close_position(self, symbol: str, exit_price: float) -> Position | None:
        """Close an open position and compute realized P&L."""
        pos = self._open_positions.pop(symbol, None)
        if pos is None:
            logger.warning("No open position found for {} to close", symbol)
            return None

        pos.exit_price = exit_price
        pos.exit_time = datetime.now()
        pos.current_price = exit_price
        pos.is_open = False

        self._closed_positions.append(pos)
        self._daily_realized_pnl += pos.realized_pnl

        logger.info(
            "Position closed: {} {} @ {:.2f} -> {:.2f} | PnL={:.2f} ({:.1f}%) | held {:.0f}min",
            pos.signal_type, symbol, pos.entry_price, exit_price,
            pos.realized_pnl, pos.unrealized_pnl_pct, pos.holding_duration,
        )

        return pos

    def update_price(self, symbol: str, current_price: float):
        """Update current price for an open position."""
        pos = self._open_positions.get(symbol)
        if pos is None:
            return

        pos.current_price = current_price
        pos.highest_price = max(pos.highest_price, current_price)
        pos.lowest_price = min(pos.lowest_price, current_price)

    def get_open_positions(self) -> dict[str, Position]:
        """Get all open positions."""
        return dict(self._open_positions)

    def get_closed_positions(self) -> list[Position]:
        """Get all closed positions (today)."""
        return list(self._closed_positions)

    def get_position(self, symbol: str) -> Position | None:
        """Get an open position by symbol."""
        return self._open_positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        """Check if there's an open position for a symbol."""
        return symbol in self._open_positions

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self._open_positions.values())

    @property
    def total_realized_pnl(self) -> float:
        return self._daily_realized_pnl

    @property
    def total_pnl(self) -> float:
        return self.total_realized_pnl + self.total_unrealized_pnl

    @property
    def open_count(self) -> int:
        return len(self._open_positions)

    def get_performance_metrics(self) -> dict:
        """Calculate comprehensive performance metrics."""
        closed = self._closed_positions
        if not closed:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0,
                "total_pnl": self.total_pnl,
                "realized_pnl": self.total_realized_pnl,
                "unrealized_pnl": self.total_unrealized_pnl,
                "avg_profit": 0,
                "avg_loss": 0,
                "max_profit": 0,
                "max_loss": 0,
                "profit_factor": 0,
                "avg_holding_mins": 0,
            }

        winners = [p for p in closed if p.realized_pnl > 0]
        losers = [p for p in closed if p.realized_pnl <= 0]

        total_profit = sum(p.realized_pnl for p in winners)
        total_loss = abs(sum(p.realized_pnl for p in losers))

        return {
            "total_trades": len(closed),
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "win_rate": len(winners) / len(closed) * 100 if closed else 0,
            "total_pnl": self.total_pnl,
            "realized_pnl": self.total_realized_pnl,
            "unrealized_pnl": self.total_unrealized_pnl,
            "avg_profit": total_profit / len(winners) if winners else 0,
            "avg_loss": -total_loss / len(losers) if losers else 0,
            "max_profit": max((p.realized_pnl for p in winners), default=0),
            "max_loss": min((p.realized_pnl for p in losers), default=0),
            "profit_factor": total_profit / total_loss if total_loss > 0 else float("inf"),
            "avg_holding_mins": sum(p.holding_duration for p in closed) / len(closed),
        }

    def to_dict(self) -> dict:
        """Serialize portfolio state for the dashboard."""
        return {
            "open_positions": [
                {
                    "symbol": p.symbol,
                    "type": p.signal_type,
                    "quantity": p.quantity,
                    "entry_price": round(p.entry_price, 2),
                    "current_price": round(p.current_price, 2),
                    "pnl": round(p.unrealized_pnl, 2),
                    "pnl_pct": round(p.unrealized_pnl_pct, 2),
                    "stop_loss": round(p.stop_loss, 2),
                    "take_profit": round(p.take_profit, 2),
                    "strategy": p.strategy,
                    "holding_mins": round(p.holding_duration, 1),
                }
                for p in self._open_positions.values()
            ],
            "closed_positions": [
                {
                    "symbol": p.symbol,
                    "type": p.signal_type,
                    "quantity": p.quantity,
                    "entry_price": round(p.entry_price, 2),
                    "exit_price": round(p.exit_price, 2),
                    "pnl": round(p.realized_pnl, 2),
                    "strategy": p.strategy,
                    "holding_mins": round(p.holding_duration, 1),
                }
                for p in self._closed_positions[-50:]  # Last 50
            ],
            "metrics": self.get_performance_metrics(),
        }
