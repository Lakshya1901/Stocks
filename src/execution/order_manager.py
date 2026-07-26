"""
order_manager.py — Order Execution & Lifecycle Management

Handles placing, modifying, and cancelling orders through Groww API.
Supports both paper trading (simulation) and live trading modes.
Uses TOTP-based authentication for automated daily login.
"""

import time
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from loguru import logger

try:
    from growwapi import GrowwAPI
    import pyotp
except ImportError:
    GrowwAPI = None
    pyotp = None
    logger.warning("growwapi/pyotp not installed — running in paper-only mode")

from src.strategies.base_strategy import SignalType


class OrderStatus(Enum):
    PENDING = "PENDING"
    PLACED = "PLACED"
    OPEN = "OPEN"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass
class Order:
    """Represents a single order."""
    order_id: str
    symbol: str
    transaction_type: str  # BUY or SELL
    quantity: int
    price: float
    order_type: str  # LIMIT or MARKET
    status: OrderStatus = OrderStatus.PENDING
    executed_price: float = 0.0
    executed_quantity: int = 0
    placed_at: datetime = field(default_factory=datetime.now)
    executed_at: datetime | None = None
    groww_order_id: str = ""
    stop_loss: float = 0.0
    take_profit: float = 0.0
    strategy: str = ""
    paper: bool = True


class OrderManager:
    """
    Manages order placement and lifecycle through Groww API.
    Supports paper trading mode for simulation.
    """

    SLIPPAGE_BUFFER = 0.0005  # 0.05% slippage buffer for LIMIT orders

    def __init__(self, config: dict):
        self._config = config
        self._trading_config = config.get("trading", {})
        self._groww_config = config.get("groww", {})
        self._mode = self._trading_config.get("mode", "paper")
        self._groww = None
        self._orders = {}  # order_id -> Order
        self._order_counter = 0

    def authenticate(self, totp_token: str, totp_secret: str) -> bool:
        """
        Authenticate with Groww using TOTP.
        Generates a fresh access token each time.
        """
        if GrowwAPI is None or pyotp is None:
            logger.warning("Groww SDK not available — paper trading only")
            return False

        try:
            # Generate TOTP code
            totp_gen = pyotp.TOTP(totp_secret)
            current_totp = totp_gen.now()
            logger.info("TOTP code generated, requesting access token...")

            # Get access token from Groww
            access_token = GrowwAPI.get_access_token(
                api_key=totp_token,
                totp=current_totp,
            )

            if not access_token:
                logger.error("Failed to get access token from Groww")
                return False

            # Initialize API client
            self._groww = GrowwAPI(access_token)
            logger.info("Groww API authenticated successfully via TOTP")
            return True

        except Exception as e:
            logger.error("Groww authentication failed: {}", e)
            return False

    def get_groww_api(self) -> "GrowwAPI | None":
        """Get the authenticated GrowwAPI instance."""
        return self._groww

    def get_access_token_for_feed(self) -> str | None:
        """Get the access token for initializing GrowwFeed."""
        if self._groww is not None:
            try:
                return self._groww._access_token
            except AttributeError:
                pass
        return None

    def place_order(
        self,
        symbol: str,
        signal_type: SignalType,
        quantity: int,
        price: float,
        stop_loss: float = 0,
        take_profit: float = 0,
        strategy: str = "",
        order_type: str = "LIMIT",
    ) -> Order | None:
        """
        Place a buy or sell order.

        In paper mode: simulates execution at current price.
        In live mode: places order through Groww API.
        """
        self._order_counter += 1
        order_id = f"ORD-{datetime.now().strftime('%Y%m%d')}-{self._order_counter:05d}"

        transaction = "BUY" if signal_type == SignalType.BUY else "SELL"

        order = Order(
            order_id=order_id,
            symbol=symbol,
            transaction_type=transaction,
            quantity=quantity,
            price=price,
            order_type=order_type,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=strategy,
            paper=(self._mode == "paper"),
        )

        if self._mode == "paper":
            return self._execute_paper_order(order)
        else:
            return self._execute_live_order(order)

    def _execute_paper_order(self, order: Order) -> Order:
        """Simulate order execution in paper trading mode."""
        order.status = OrderStatus.EXECUTED
        order.executed_price = order.price
        order.executed_quantity = order.quantity
        order.executed_at = datetime.now()
        order.groww_order_id = f"PAPER-{order.order_id}"

        self._orders[order.order_id] = order

        logger.info(
            "[PAPER] {} {} x{} @ {:.2f} | SL={:.2f} TP={:.2f} | strategy={}",
            order.transaction_type, order.symbol, order.quantity,
            order.executed_price, order.stop_loss, order.take_profit,
            order.strategy,
        )

        return order

    def _execute_live_order(self, order: Order) -> Order | None:
        """Execute order through Groww API."""
        if self._groww is None:
            logger.error("Groww API not authenticated — cannot place live order")
            order.status = OrderStatus.FAILED
            self._orders[order.order_id] = order
            return order

        try:
            # Add slippage buffer for LIMIT orders
            if order.order_type == "LIMIT":
                if order.transaction_type == "BUY":
                    limit_price = order.price * (1 + self.SLIPPAGE_BUFFER)
                else:
                    limit_price = order.price * (1 - self.SLIPPAGE_BUFFER)
            else:
                limit_price = 0

            # Map order type
            groww_order_type = (
                self._groww.ORDER_TYPE_LIMIT
                if order.order_type == "LIMIT"
                else self._groww.ORDER_TYPE_MARKET
            )

            # Map transaction type
            groww_txn = (
                self._groww.TRANSACTION_TYPE_BUY
                if order.transaction_type == "BUY"
                else self._groww.TRANSACTION_TYPE_SELL
            )

            # Place order
            response = self._groww.place_order(
                trading_symbol=order.symbol,
                quantity=order.quantity,
                exchange=self._groww.EXCHANGE_NSE,
                segment=self._groww.SEGMENT_CASH,
                product=self._groww.PRODUCT_MIS,  # Intraday
                order_type=groww_order_type,
                transaction_type=groww_txn,
                validity=self._groww.VALIDITY_DAY,
                price=round(limit_price, 2) if limit_price > 0 else None,
            )

            if response:
                order.status = OrderStatus.PLACED
                order.groww_order_id = str(response.get("orderId", response.get("order_id", "")))
                logger.info(
                    "[LIVE] Order placed: {} {} x{} @ {:.2f} | groww_id={}",
                    order.transaction_type, order.symbol, order.quantity,
                    limit_price, order.groww_order_id,
                )
            else:
                order.status = OrderStatus.FAILED
                logger.error("Order placement returned empty response for {}", order.symbol)

        except Exception as e:
            order.status = OrderStatus.FAILED
            logger.error("Order placement failed for {}: {}", order.symbol, e)

        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        order = self._orders.get(order_id)
        if order is None:
            logger.warning("Order {} not found", order_id)
            return False

        if order.status not in (OrderStatus.PLACED, OrderStatus.OPEN):
            logger.warning("Cannot cancel order {} — status is {}", order_id, order.status.value)
            return False

        if order.paper:
            order.status = OrderStatus.CANCELLED
            logger.info("[PAPER] Order {} cancelled", order_id)
            return True

        try:
            if self._groww and order.groww_order_id:
                self._groww.cancel_order(order_id=order.groww_order_id)
                order.status = OrderStatus.CANCELLED
                logger.info("[LIVE] Order {} cancelled (groww_id={})", order_id, order.groww_order_id)
                return True
        except Exception as e:
            logger.error("Failed to cancel order {}: {}", order_id, e)

        return False

    def get_order(self, order_id: str) -> Order | None:
        """Get order by ID."""
        return self._orders.get(order_id)


    @property
    def is_live(self) -> bool:
        return self._mode == "live"

    @property
    def is_paper(self) -> bool:
        return self._mode == "paper"
