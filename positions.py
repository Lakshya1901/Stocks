"""
positions.py — Tracks open positions across multiple trading sessions and
                   executes stop-loss / take-profit exits automatically.

Key features:
  - Multi-day persistence: saves active positions to open_positions.json so held
    stocks survive bot restarts, weekend closures, and multi-day swing holds.
  - Fee-aware monitoring: logs both gross and net P&L after Groww charges.
  - Automatic exits: places SELL orders via broker when SL or TP is reached.
"""

from config import get_logger
import broker
import market
import news
import risk
from risk import TradeParams

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from config import OPEN_POSITIONS_FILE

logger = get_logger(__name__)


@dataclass
class WatchedPosition:
    symbol:      str
    exchange:    str
    quantity:    int
    entry_price: float
    stop_loss:   float
    take_profit: float
    order_id:    str
    opened_at:   str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))


# In-memory registry of all open positions being monitored (keyed by NSE symbol)
_positions: dict[str, WatchedPosition] = {}


def _save_positions() -> None:
    """Persists all current watched positions to OPEN_POSITIONS_FILE."""
    try:
        data = {sym: asdict(pos) for sym, pos in _positions.items()}
        temp_file = Path(str(OPEN_POSITIONS_FILE) + ".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_file.replace(OPEN_POSITIONS_FILE)
        logger.debug(f"Saved {len(_positions)} position(s) to {OPEN_POSITIONS_FILE.name}")
    except Exception as exc:
        logger.error(f"Failed to save open positions to disk: {exc}")


def load_positions() -> None:
    """Loads active watched positions from OPEN_POSITIONS_FILE on startup."""
    global _positions
    if not OPEN_POSITIONS_FILE.exists():
        _positions = {}
        return

    try:
        with open(OPEN_POSITIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        loaded = {}
        for sym, p in data.items():
            loaded[sym] = WatchedPosition(
                symbol      = p["symbol"],
                exchange    = p.get("exchange", "NSE"),
                quantity    = int(p["quantity"]),
                entry_price = float(p["entry_price"]),
                stop_loss   = float(p["stop_loss"]),
                take_profit = float(p["take_profit"]),
                order_id    = p.get("order_id", ""),
                opened_at   = p.get("opened_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
            )
        _positions = loaded
        if _positions:
            syms = ", ".join(_positions.keys())
            logger.info(f"Restored {len(_positions)} active multi-day position(s) from disk: [{syms}]")
    except Exception as exc:
        logger.error(f"Failed to load open positions from disk: {exc}")
        _positions = {}




def register_position(params: TradeParams, order_id: str) -> None:
    """
    Registers a new position after a successful BUY order and saves it to disk.
    """
    pos = WatchedPosition(
        symbol      = params.symbol,
        exchange    = params.exchange,
        quantity    = params.quantity,
        entry_price = params.entry_price,
        stop_loss   = params.stop_loss,
        take_profit = params.take_profit,
        order_id    = order_id,
    )
    _positions[params.symbol] = pos
    _save_positions()
    logger.info(
        f"Watching position: {params.symbol} qty={params.quantity} "
        f"entry=₹{params.entry_price:.2f} "
        f"SL=₹{params.stop_loss:.2f} TP=₹{params.take_profit:.2f} "
        f"(persisted to disk)"
    )


def check_all_positions() -> None:
    """
    Iterates over every watched position and exits if SL or TP is reached.
    Persists state to disk whenever a position is closed.
    """
    if not _positions:
        return

    symbols_to_remove: list[str] = []

    for symbol, pos in _positions.items():
        price = market.get_current_price(symbol)
        if price is None:
            logger.warning(f"Cannot check {symbol} — live price unavailable")
            continue

        cost = pos.entry_price * pos.quantity
        rev = price * pos.quantity
        pnl_stats = risk.calculate_net_pnl(cost, rev)

        gross_pct = pnl_stats["gross_pct"]
        net_pct = pnl_stats["net_pct"]
        net_amt = pnl_stats["net_pnl"]

        logger.debug(
            f"{symbol}: current=₹{price:.2f} entry=₹{pos.entry_price:.2f} "
            f"Gross={gross_pct:+.2f}% Net={net_pct:+.2f}% (net ₹{net_amt:+,.2f}) "
            f"SL=₹{pos.stop_loss:.2f} TP=₹{pos.take_profit:.2f}"
        )

        reason = None
        if price <= pos.stop_loss:
            reason = f"stop-loss hit (price ₹{price:.2f} <= SL ₹{pos.stop_loss:.2f}, net P&L {net_pct:+.2f}%)"
        elif price >= pos.take_profit:
            reason = f"take-profit hit (price ₹{price:.2f} >= TP ₹{pos.take_profit:.2f}, net P&L {net_pct:+.2f}%)"

        if reason:
            logger.info(f"Exiting {symbol}: {reason}")
            sold = broker.place_sell_order(
                symbol   = pos.symbol,
                exchange = pos.exchange,
                quantity = pos.quantity,
                reason   = reason,
                price    = price,
            )
            if sold:
                symbols_to_remove.append(symbol)
                pos.quantity -= sold[1]

    if symbols_to_remove:
        for symbol in symbols_to_remove:
            if _positions[symbol].quantity > 0:
                logger.warning(f"Position {symbol} partially sold — still watching {_positions[symbol].quantity} share(s)")
                continue
            _positions.pop(symbol, None)
            logger.info(f"Position {symbol} closed and removed from active watch list")
        _save_positions()


def remove_position(symbol: str) -> None:
    """Stops tracking a position that was closed elsewhere (e.g. a protective sell)."""
    if _positions.pop(symbol, None) is not None:
        _save_positions()


def open_position_count() -> int:
    return len(_positions)


def is_watching(symbol: str) -> bool:
    return symbol in _positions


def get_all_positions() -> dict[str, WatchedPosition]:
    return dict(_positions)

# =============================================================================
# Tracks held stocks (both locally stored positions and Groww Demat holdings)
# =============================================================================
from config import NEGATIVE_SENTIMENT_THRESHOLD, EXCHANGE, DRY_RUN

logger = get_logger(__name__)

# Map from NSE symbol -> number of shares held
_current_holdings: dict[str, int] = {}


def refresh_holdings() -> dict[str, int]:
    """
    Refreshes the portfolio holdings:
      1. Primary source: locally tracked active positions from open_positions.json.
      2. Secondary source: live Groww Demat holdings (if available) to protect manual holdings.
    Returns: {"RELIANCE": 5, "TCS": 2, ...}
    """
    global _current_holdings
    holdings: dict[str, int] = {}

    # 1. Load local persistent positions
    try:
        local_pos = get_all_positions()
        for sym, pos in local_pos.items():
            if pos.quantity > 0:
                holdings[sym] = pos.quantity
    except Exception as exc:
        logger.debug(f"Could not read local positions in portfolio monitor: {exc}")

    # 2. Merge live Demat holdings from Groww (skip in DRY_RUN — no real holdings to track)
    from config import DRY_RUN
    if not DRY_RUN:
        try:
            raw = broker.get_holdings()
            for h in raw:
                sym = str(h.get("tradingSymbol", "")).strip().upper()
                qty = int(h.get("holdingQuantity", 0) or 0)
                if sym and qty > 0:
                    holdings[sym] = max(holdings.get(sym, 0), qty)
        except Exception as exc:
            logger.debug(f"Could not merge Groww Demat holdings: {exc}")

    _current_holdings = holdings
    if holdings:
        logger.info(f"Active portfolio holdings ({len(holdings)}): {list(holdings.keys())}")
    else:
        logger.debug("No active holdings found in portfolio")
    return holdings


def is_holding(symbol: str) -> bool:
    """Returns True if the symbol is already in our local or Demat portfolio."""
    clean_sym = symbol.strip().upper()
    return clean_sym in _current_holdings


def get_holdings_count() -> int:
    return len(_current_holdings)


def check_holdings_against_news(headline: str, symbol: str) -> None:
    """
    If the identified symbol is a bot-managed position and news sentiment is
    strongly negative, the bot places a protective SELL order.
    Manually held Demat shares are never sold.
    """
    clean_sym = symbol.strip().upper()
    tracked = _positions.get(clean_sym)
    if tracked is None or tracked.quantity <= 0:
        return
    held_qty = tracked.quantity

    label, score = news.analyze(headline)

    if label == "negative" and score >= NEGATIVE_SENTIMENT_THRESHOLD:
        logger.warning(
            f"Negative news ({score:.2f}) detected for held stock {clean_sym} "
            f"(holding {held_qty} shares). Initiating protective SELL."
        )
        price = market.get_current_price(clean_sym) or 0.0
        if price <= 0 and DRY_RUN:
            logger.warning(f"No live price for {clean_sym} — skipping simulated protective SELL")
            return

        sold = broker.place_sell_order(
            symbol=clean_sym,
            exchange=EXCHANGE,
            quantity=held_qty,
            reason=f"protective sell: negative sentiment ({score:.2f})",
            price=price,
        )
        if sold and sold[1] < held_qty:
            tracked.quantity = held_qty - sold[1]
            _save_positions()
            logger.warning(f"Protective SELL of {clean_sym} partially filled — still watching {tracked.quantity} share(s)")
        elif sold:
            _current_holdings.pop(clean_sym, None)
            remove_position(clean_sym)
    else:
        logger.debug(
            f"Held stock {clean_sym}: news sentiment is {label} ({score:.2f}) — no action"
        )
