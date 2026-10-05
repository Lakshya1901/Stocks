"""
broker.py — Groww API wrapper (auth, balances, holdings, orders) + trade journal.

Handles:
  - Token caching: reuses access tokens to avoid exhausting TOTP rate limits
  - Margin & balance checks: queries live Groww cash with local config fallback
  - Portfolio holdings reads: pulls live Demat holdings if available
  - Order placement: places CNC (delivery) market BUY/SELL orders, or simulates in DRY_RUN
"""

from config import get_logger
import risk
from risk import TradeParams

import json
import time
from pathlib import Path
from datetime import datetime, timedelta
import pyotp
import pytz
from growwapi import GrowwAPI
from growwapi.groww.exceptions import GrowwAPIAuthenticationException, GrowwAPIAuthorisationException
from config import GROWW_API_KEY, GROWW_TOTP_SECRET, DRY_RUN, TOTAL_ACCOUNT_CAPITAL

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# Cache token locally until Groww's daily 06:00 IST expiry to avoid TOTP rate limits
_TOKEN_CACHE_FILE = Path(__file__).parent.resolve() / ".groww_token.json"


# ---------------------------------------------------------------------------
# Authentication & Session Management
# ---------------------------------------------------------------------------

def _get_cached_token() -> str | None:
    if _TOKEN_CACHE_FILE.exists():
        try:
            data = json.loads(_TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() < data.get("exp_ts", 0):
                return data.get("token")
        except Exception:
            pass
    return None


def _seconds_until_token_expiry() -> int:
    """Groww access tokens expire daily at 06:00 IST, regardless of when they were issued."""
    now = datetime.now(IST)
    expiry = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= expiry:
        expiry += timedelta(days=1)
    return int((expiry - now).total_seconds())


def _save_cached_token(token: str) -> None:
    try:
        data = {"token": token, "exp_ts": time.time() + _seconds_until_token_expiry()}
        _TOKEN_CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception as exc:
        logger.debug(f"Could not cache Groww token: {exc}")


def _authenticate() -> GrowwAPI:
    """
    Returns an authenticated GrowwAPI client using cached token or fresh TOTP exchange.
    """
    if not GROWW_API_KEY or not GROWW_TOTP_SECRET:
        raise ValueError(
            "GROWW_API_KEY and GROWW_TOTP_SECRET must be set in your .env file. "
            "Generate a TOTP token on https://groww.in/trade-api/api-keys"
        )

    # 1. Try cached token
    cached = _get_cached_token()
    if cached:
        logger.debug("Reusing cached Groww access token")
        return GrowwAPI(cached)

    # 2. Exchange fresh TOTP
    logger.info("Authenticating with Groww API (TOTP flow)...")
    totp_code = pyotp.TOTP(GROWW_TOTP_SECRET).now()
    access_token = GrowwAPI.get_access_token(api_key=GROWW_API_KEY, totp=totp_code)
    _save_cached_token(access_token)
    groww = GrowwAPI(access_token)
    logger.info("Groww authentication successful")
    return groww


_groww: GrowwAPI | None = None


def invalidate_client() -> None:
    """Invalidates the cached Groww session to force re-authentication on the next call."""
    global _groww
    _groww = None
    if _TOKEN_CACHE_FILE.exists():
        try:
            _TOKEN_CACHE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
    logger.debug("Groww client session invalidated")


def get_client() -> GrowwAPI:
    global _groww
    if _groww is None:
        _groww = _authenticate()
    return _groww


def _call(method_name: str, **kwargs):
    """Calls a GrowwAPI method, re-authenticating once if the token was rejected."""
    try:
        return getattr(get_client(), method_name)(**kwargs)
    except (GrowwAPIAuthenticationException, GrowwAPIAuthorisationException):
        logger.warning("Groww session expired. Re-authenticating...")
        invalidate_client()
        return getattr(get_client(), method_name)(**kwargs)


# ---------------------------------------------------------------------------
# Portfolio & Margin Reads
# ---------------------------------------------------------------------------

def get_holdings() -> list[dict]:
    """
    Returns a normalized list of delivery holdings from Groww Demat.
    Each item contains: {"tradingSymbol": str, "holdingQuantity": int, "averagePrice": float}
    """
    try:
        body = _call("get_holdings_for_user", timeout=8)
    except Exception as exc:
        logger.warning(f"Groww holdings fetch failed: {exc}")
        return []
    normalized = []
    for item in body.get("holdings", []) or []:
        sym = str(item.get("tradingSymbol") or item.get("trading_symbol") or item.get("symbol", "")).strip().upper()
        qty = int(item.get("holdingQuantity") or item.get("holding_quantity") or item.get("quantity", 0) or 0)
        avg_p = float(item.get("averagePrice") or item.get("avg_price") or item.get("average_price", 0.0) or 0.0)
        if sym and qty > 0:
            normalized.append({
                "tradingSymbol": sym,
                "holdingQuantity": qty,
                "averagePrice": avg_p,
            })
    logger.info(f"Fetched {len(normalized)} Demat holding(s) from Groww")
    return normalized


def _fetch_cash_balance() -> float:
    """Queries Groww for cash available to buy delivery (CNC) stock. Returns 0.0 on failure."""
    try:
        payload = _call("get_available_margin_details", timeout=8)
        cnc_bal = payload.get("equity_margin_details", {}).get("cnc_balance_available")
        if cnc_bal is None:
            cnc_bal = payload.get("clear_cash", 0.0)
        return float(cnc_bal or 0.0)
    except Exception as exc:
        logger.warning(f"Could not read live Groww margin: {exc}")
    return 0.0


def get_live_cash() -> float:
    """Live cash available for a new CNC buy (LIVE mode only)."""
    return _fetch_cash_balance()


def _fetch_holdings_market_value() -> float:
    """
    Sums the current market value of all Demat holdings.
    Uses live prices from market where available, falls back to average buy price.
    """
    import market
    holdings = get_holdings()
    total_value = 0.0
    for h in holdings:
        sym = h["tradingSymbol"]
        qty = h["holdingQuantity"]
        avg_price = h["averagePrice"]
        # Try live price first, fall back to average buy price
        live_price = market.get_current_price(sym)
        price = live_price if live_price and live_price > 0 else avg_price
        total_value += price * qty
    return total_value


# Cached portfolio value — refreshed once at startup and at each market open
_cached_portfolio: dict = {"total": 0.0, "cash": 0.0, "holdings_value": 0.0, "fetched_at": 0.0}


def refresh_portfolio_capital() -> float:
    """
    Fetches total portfolio value from Groww (cash + market value of held stocks)
    and caches it as the day's capital base.

    Call this at bot startup and at market open. The cached value is used by
    get_account_balance() for trade sizing throughout the day, avoiding
    repeated API calls every cycle.

    Returns the total portfolio value, or TOTAL_ACCOUNT_CAPITAL as fallback.
    """
    global _cached_portfolio

    if DRY_RUN:
        _cached_portfolio = {
            "total": TOTAL_ACCOUNT_CAPITAL,
            "cash": TOTAL_ACCOUNT_CAPITAL,
            "holdings_value": 0.0,
            "fetched_at": time.time(),
        }
        return TOTAL_ACCOUNT_CAPITAL

    cash = _fetch_cash_balance()
    holdings_value = _fetch_holdings_market_value()
    total = cash + holdings_value

    if total > 0:
        _cached_portfolio = {
            "total": total,
            "cash": cash,
            "holdings_value": holdings_value,
            "fetched_at": time.time(),
        }
        logger.info(
            f"Portfolio capital from Groww: ₹{total:,.2f} "
            f"(cash ₹{cash:,.2f} + holdings ₹{holdings_value:,.2f})"
        )
        return total

    # Groww returned 0 for both — account may not be funded yet
    logger.warning(
        f"Groww returned ₹0 for cash and holdings. "
        f"Using config fallback: ₹{TOTAL_ACCOUNT_CAPITAL:,.2f}"
    )
    _cached_portfolio = {
        "total": TOTAL_ACCOUNT_CAPITAL,
        "cash": TOTAL_ACCOUNT_CAPITAL,
        "holdings_value": 0.0,
        "fetched_at": time.time(),
    }
    return TOTAL_ACCOUNT_CAPITAL


def get_portfolio_summary() -> dict:
    """Returns the cached portfolio breakdown for display in the dashboard."""
    return dict(_cached_portfolio)


def get_account_balance() -> float:
    """
    Returns available capital for new trades.

    Uses the cached portfolio total (fetched at startup / market open) as
    the capital base, then deducts capital currently locked in active
    bot-managed positions.

    In DRY_RUN mode, uses TOTAL_ACCOUNT_CAPITAL from config.
    """
    # Use cached portfolio if fresh (< 24 hours, i.e. refreshed at the last market open), otherwise use config fallback
    age_hours = (time.time() - _cached_portfolio["fetched_at"]) / 3600.0
    if _cached_portfolio["fetched_at"] > 0 and age_hours < 24:
        base_capital = _cached_portfolio["total"]
    else:
        base_capital = TOTAL_ACCOUNT_CAPITAL

    # Deduct capital currently tied up in open positions
    try:
        import positions
        open_pos = positions.get_all_positions()
        deployed = sum(p.entry_price * p.quantity for p in open_pos.values())
        available = max(0.0, base_capital - deployed)
        return available
    except Exception:
        return base_capital


# ---------------------------------------------------------------------------
# Order Placement
# ---------------------------------------------------------------------------


_FAILED_ORDER_STATUSES = {"REJECTED", "FAILED", "CANCELLED"}


def _place_market_order(symbol: str, exchange: str, side: str, quantity: int) -> str | None:
    """
    Places a CNC (delivery) market order and briefly polls its status.
    Returns the Groww order id, or None if the order was rejected / failed.
    """
    response = _call(
        "place_order",
        validity=GrowwAPI.VALIDITY_DAY,
        exchange=exchange,
        order_type=GrowwAPI.ORDER_TYPE_MARKET,
        product=GrowwAPI.PRODUCT_CNC,   # CNC = delivery (hold overnight / multi-day)
        quantity=quantity,
        segment=GrowwAPI.SEGMENT_CASH,
        trading_symbol=symbol,
        transaction_type=side,
        timeout=10,
    )
    order_id = response.get("groww_order_id")
    if not order_id:
        logger.error(f"{side} order for {symbol} returned no order id: {response}")
        return None

    status = str(response.get("order_status", "")).upper()
    for _ in range(5):
        if status in _FAILED_ORDER_STATUSES or status in ("EXECUTED", "COMPLETED"):
            break
        time.sleep(1)
        try:
            st = get_client().get_order_status(segment=GrowwAPI.SEGMENT_CASH, groww_order_id=order_id, timeout=5)
            status = str(st.get("order_status", "")).upper()
            response = st
        except Exception as exc:
            logger.debug(f"Order status check failed for {order_id}: {exc}")

    if status in _FAILED_ORDER_STATUSES:
        logger.error(f"{side} order {order_id} for {symbol} {status}: {response.get('remark', '')}")
        return None
    logger.info(f"{side} order {order_id} for {symbol} status: {status or 'UNKNOWN'}")
    return str(order_id)


def _get_fill(order_id: str) -> tuple[int, float] | None:
    """(filled_quantity, average_fill_price) from Groww, or None if nothing has filled yet."""
    try:
        d = get_client().get_order_detail(segment=GrowwAPI.SEGMENT_CASH, groww_order_id=order_id, timeout=5)
        qty = int(d.get("filled_quantity") or 0)
        avg = float(d.get("average_fill_price") or 0.0)
        return (qty, avg) if qty > 0 and avg > 0 else None
    except Exception as exc:
        logger.debug(f"Order detail fetch failed for {order_id}: {exc}")
        return None


def _settle_fill(order_id: str, symbol: str, side: str) -> tuple[int, float] | None:
    """
    Fill for a just-placed order. If none is confirmed yet, cancels the rest of the order
    so nothing can fill later untracked, then re-reads the fill (it may have filled before the cancel).
    """
    fill = _get_fill(order_id)
    if fill is not None:
        return fill
    try:
        get_client().cancel_order(groww_order_id=order_id, segment=GrowwAPI.SEGMENT_CASH, timeout=5)
        logger.warning(f"{side} order {order_id} for {symbol} had no confirmed fill — cancelled it")
    except Exception as exc:
        logger.warning(f"Cancel of unconfirmed {side} order {order_id} for {symbol} failed: {exc}")
    time.sleep(1)
    return _get_fill(order_id)


def is_ddpi_enabled() -> bool | None:
    """Whether DDPI is active. Without it, CDSL requires a TPIN/OTP each day before shares held in demat can be sold."""
    try:
        return bool(_call("get_user_profile", timeout=8).get("ddpi_enabled"))
    except Exception as exc:
        logger.warning(f"Could not read Groww profile: {exc}")
        return None


def place_buy_order(params: TradeParams, extra: dict | None = None) -> str | None:
    """
    Places a CNC (delivery) market BUY order.
    Returns the order_id string on success, None on failure.
    In LIVE mode, params is updated in place with the real fill (quantity, price, SL/TP).
    In DRY_RUN mode logs the trade locally without making an API order.
    """
    mode = "DRY_RUN" if DRY_RUN else "LIVE"

    if DRY_RUN:
        fake_id = f"DRY-BUY-{params.symbol}-{params.quantity}"
        logger.info(
            f"[DRY RUN] Would BUY {params.quantity} x {params.symbol} "
            f"@ ₹{params.entry_price:.2f} (total ₹{params.capital_used:.2f})"
        )
        log_trade("BUY", params.symbol, params.quantity, params.entry_price, "Strong positive news", mode, extra)
        return fake_id

    try:
        order_id = _place_market_order(params.symbol, params.exchange, "BUY", params.quantity)
        if order_id is None:
            return None
        fill = _settle_fill(order_id, params.symbol, "BUY")
        if fill is None:
            # Still nothing filled: don't track a position (and its SL/TP sells) that doesn't exist
            logger.error(
                f"BUY order {order_id} for {params.symbol} has no confirmed fill — "
                f"not tracking it; check the order in Groww"
            )
            return None
        filled = risk.build_params(params.symbol, fill[0], fill[1])
        logger.info(f"{params.symbol} filled {fill[0]} @ ₹{fill[1]:.2f} (quote was ₹{params.entry_price:.2f})")
        for k, v in vars(filled).items():
            setattr(params, k, v)
        logger.info(
            f"BUY order placed: {params.quantity} x {params.symbol} — "
            f"order_id={order_id}"
        )
        log_trade("BUY", params.symbol, params.quantity, params.entry_price, "Strong positive news", mode, extra)
        return str(order_id)
    except Exception as exc:
        logger.error(f"Failed to place BUY order for {params.symbol}: {exc}")
        return None


def place_sell_order(symbol: str, exchange: str, quantity: int, reason: str = "", price: float = 0.0) -> tuple[str, int] | None:
    """
    Places a CNC (delivery) market SELL order.
    Returns (order_id, quantity actually sold) on success, None if nothing was sold.
    """
    mode = "DRY_RUN" if DRY_RUN else "LIVE"

    if DRY_RUN:
        fake_id = f"DRY-SELL-{symbol}-{quantity}"
        logger.info(
            f"[DRY RUN] Would SELL {quantity} x {symbol} "
            f"({'reason: ' + reason if reason else 'no reason given'})"
        )
        log_trade("SELL", symbol, quantity, price, reason, mode)
        return fake_id, quantity

    try:
        order_id = _place_market_order(symbol, exchange, "SELL", quantity)
        if order_id is None:
            return None
        fill = _settle_fill(order_id, symbol, "SELL")
        if fill is None:
            logger.error(f"SELL order {order_id} for {symbol} has no confirmed fill — nothing sold")
            return None
        if fill[0] != quantity:
            logger.warning(f"SELL order {order_id} for {symbol} filled {fill[0]} of {quantity}")
        quantity, price = fill
        logger.info(
            f"SELL order placed: {quantity} x {symbol} "
            f"(reason: {reason}) — order_id={order_id}"
        )
        log_trade("SELL", symbol, quantity, price, reason, mode)
        return str(order_id), quantity
    except Exception as exc:
        logger.error(f"Failed to place SELL order for {symbol}: {exc}")
        return None

# =============================================================================
# Trade journal (trades.jsonl)
# =============================================================================
from config import TRADES_FILE

def log_trade(action: str, symbol: str, quantity: int, price: float, reason: str, mode: str, extra: dict | None = None):
    """
    Appends a trade record to trades.jsonl.
    """
    record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,  # "DRY_RUN" or "LIVE"
        "action": action.upper(),
        "symbol": symbol,
        "quantity": quantity,
        "price": price,
        "total": round(quantity * price, 2),
        "reason": reason,
        **(extra or {}),
    }
    
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")
