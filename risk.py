"""
risk.py — Fee-aware position sizing, stop-loss / take-profit calculation,
                  market-hours guard, and liquidity checks.
"""

from config import get_logger
import market

import json
import time
from dataclasses import dataclass
from datetime import datetime
import pytz
import requests
from config import (
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    MIN_DAILY_VOLUME,
    MIN_TRADE_CAPITAL,
    EXCHANGE,
    DRY_RUN,
    TRADE_ACCOUNT_PCT,
    TOTAL_ACCOUNT_CAPITAL,
    NSE_HOLIDAYS_FILE,
)

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# NSE market session (IST)
MARKET_OPEN  = (9, 15)   # 09:15
MARKET_CLOSE = (15, 30)  # 15:30


@dataclass
class TradeParams:
    symbol:        str
    exchange:      str
    quantity:      int
    entry_price:   float
    stop_loss:     float
    take_profit:   float
    capital_used:  float
    breakeven:     float = 0.0


def _fetch_nse_holidays() -> list[str]:
    """Downloads NSE equity (CM) trading holidays as ISO dates. Needs the homepage cookie first."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/",
    })
    s.get("https://www.nseindia.com/", timeout=10)
    resp = s.get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=10)
    resp.raise_for_status()
    return [datetime.strptime(h["tradingDate"], "%d-%b-%Y").date().isoformat() for h in resp.json()["CM"]]


_holidays: set[str] | None = None
_last_fetch_attempt = 0.0


def _get_holidays() -> set[str]:
    """NSE holidays, from a weekly-refreshed cache file. Falls back to the stale cache if NSE is unreachable."""
    global _holidays, _last_fetch_attempt
    cache_fresh = (
        NSE_HOLIDAYS_FILE.exists()
        and time.time() - NSE_HOLIDAYS_FILE.stat().st_mtime < 7 * 24 * 3600
    )
    retry_due = time.time() - _last_fetch_attempt > 3600
    if _holidays is not None and (cache_fresh or not retry_due):
        return _holidays

    if not cache_fresh:
        _last_fetch_attempt = time.time()
        try:
            dates = _fetch_nse_holidays()
            NSE_HOLIDAYS_FILE.write_text(json.dumps(dates), encoding="utf-8")
            logger.info(f"Updated NSE holiday list ({len(dates)} dates)")
        except Exception as exc:
            logger.warning(f"Could not download NSE holidays: {exc}")

    try:
        _holidays = set(json.loads(NSE_HOLIDAYS_FILE.read_text(encoding="utf-8")))
    except Exception:
        logger.warning("No NSE holiday list available — treating every weekday as a trading day")
        _holidays = set()
    return _holidays


def is_market_open() -> bool:
    """Returns True if the current IST time is within NSE trading hours on a trading day."""
    now = datetime.now(IST)
    if now.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    if now.date().isoformat() in _get_holidays():
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t < MARKET_CLOSE


def _get_current_price_and_volume(nse_symbol: str) -> tuple[float, float] | None:
    """Fetches the latest live price and average daily volume via market."""
    return market.get_price_and_volume(nse_symbol)


def build_params(nse_symbol: str, quantity: int, price: float) -> TradeParams:
    """Fee-adjusted breakeven, take-profit and stop-loss for an entry. Also used to re-price on the real fill."""
    breakeven = calculate_breakeven_exit_price(price, quantity)
    # Take-profit targets a genuine net gain above breakeven price
    take_profit = round(breakeven * (1.0 + TAKE_PROFIT_PCT), 2)
    stop_loss   = round(price * (1.0 - STOP_LOSS_PCT), 2)
    return TradeParams(
        symbol       = nse_symbol,
        exchange     = EXCHANGE,
        quantity     = quantity,
        entry_price  = price,
        stop_loss    = stop_loss,
        take_profit  = take_profit,
        capital_used = round(quantity * price, 2),
        breakeven    = breakeven,
    )


def calculate_trade_params(nse_symbol: str) -> TradeParams | None:
    """
    Main entry point. Returns a TradeParams object if the trade is viable,
    or None if any guard condition fails (market closed, illiquid, price unavailable, or capital too small).
    """
    # Guard 1: Market hours
    if not is_market_open():
        logger.info(f"Market is closed — skipping trade for {nse_symbol}")
        return None

    # Guard 2: Live price and volume
    result = _get_current_price_and_volume(nse_symbol)
    if result is None:
        logger.warning(f"Could not fetch live price for {nse_symbol} — skipping")
        return None

    price, avg_volume = result

    # Guard 3: Minimum liquidity check
    if MIN_DAILY_VOLUME > 0 and avg_volume < MIN_DAILY_VOLUME:
        logger.warning(
            f"{nse_symbol} avg volume {avg_volume:,.0f} is below "
            f"minimum {MIN_DAILY_VOLUME:,} — skipping (illiquid)"
        )
        return None

    # Guard 4: Capital allocation and share affordability
    import broker
    # Size every trade as a fixed % of total capital (not of the shrinking remainder),
    # capped by what is still undeployed.
    balance = broker.get_account_balance()
    total = broker.get_portfolio_summary().get("total") or TOTAL_ACCOUNT_CAPITAL
    capital_per_trade = min(total * TRADE_ACCOUNT_PCT, balance)
    if not DRY_RUN:
        # Leave ~0.5% headroom for buy-side charges and price movement on a market order
        live_cash = broker.get_live_cash() * 0.995
        capital_per_trade = min(capital_per_trade, live_cash)

    quantity = int(capital_per_trade // price)
    if quantity < 1:
        logger.warning(
            f"{nse_symbol} price ₹{price:.2f} exceeds our capital limit of "
            f"₹{capital_per_trade:.2f} ({TRADE_ACCOUNT_PCT*100:.0f}% of ₹{total:.2f}, ₹{balance:.2f} undeployed) — cannot buy 1 share"
        )
        return None

    capital_used = round(quantity * price, 2)

    # Guard 5: Minimum trade capital to prevent fixed DP charges from eating profit
    if capital_used < MIN_TRADE_CAPITAL:
        logger.warning(
            f"{nse_symbol} total position value ₹{capital_used:.2f} is below minimum capital "
            f"threshold ₹{MIN_TRADE_CAPITAL:.2f} (Groww fixed fees would erode profits) — skipping"
        )
        return None

    params = build_params(nse_symbol, quantity, price)

    logger.info(
        f"Trade params for {nse_symbol}: qty={quantity}, "
        f"entry=₹{price:.2f}, breakeven=₹{params.breakeven:.2f}, "
        f"TP (net +{TAKE_PROFIT_PCT*100:.1f}%)=₹{params.take_profit:.2f}, "
        f"SL (-{STOP_LOSS_PCT*100:.1f}%)=₹{params.stop_loss:.2f}, "
        f"capital=₹{capital_used:.2f}"
    )
    return params

# =============================================================================
# Groww equity delivery fee and statutory charges engine.
# =============================================================================
# Rates
BROKERAGE_PCT = 0.0005          # 0.05%
MAX_BROKERAGE_PER_ORDER = 20.0  # ₹20 cap
STT_DELIVERY_PCT = 0.001        # 0.1%
NSE_TXN_CHARGE_PCT = 0.0000297  # 0.00297%
STAMP_DUTY_BUY_PCT = 0.00015    # 0.015%
SEBI_TURNOVER_PCT = 0.000001    # 0.0001%
DP_CHARGE_BASE = 13.50          # ₹13.50 per scrip per day on sell
GST_RATE = 0.18                 # 18%
DP_CHARGE_WITH_GST = round(DP_CHARGE_BASE * (1.0 + GST_RATE), 2)  # ₹15.93


def calculate_order_fees(turnover: float, is_buy: bool) -> dict:
    """
    Computes all fees and statutory taxes for a single order (BUY or SELL).
    Returns a dict with itemized charges and the total fee amount in INR.
    """
    if turnover <= 0:
        return {
            "turnover": 0.0,
            "brokerage": 0.0,
            "stt": 0.0,
            "exchange_charges": 0.0,
            "stamp_duty": 0.0,
            "sebi_charges": 0.0,
            "dp_charges": 0.0,
            "gst": 0.0,
            "total_fees": 0.0,
        }

    brokerage = min(round(turnover * BROKERAGE_PCT, 2), MAX_BROKERAGE_PER_ORDER)
    stt = round(turnover * STT_DELIVERY_PCT, 2)
    exchange_charges = round(turnover * NSE_TXN_CHARGE_PCT, 2)
    stamp_duty = round(turnover * STAMP_DUTY_BUY_PCT, 2) if is_buy else 0.0
    sebi_charges = round(turnover * SEBI_TURNOVER_PCT, 4)

    # DP charge is applied once per scrip sold on delivery trades
    dp_charges = DP_CHARGE_WITH_GST if not is_buy else 0.0

    # GST applies to Brokerage, Exchange transaction charge, and SEBI fee
    gst_base = brokerage + exchange_charges + sebi_charges
    gst = round(gst_base * GST_RATE, 2)

    total_fees = round(
        brokerage + stt + exchange_charges + stamp_duty + sebi_charges + dp_charges + gst,
        2
    )

    return {
        "turnover": round(turnover, 2),
        "brokerage": brokerage,
        "stt": stt,
        "exchange_charges": exchange_charges,
        "stamp_duty": stamp_duty,
        "sebi_charges": round(sebi_charges, 2),
        "dp_charges": dp_charges,
        "gst": gst,
        "total_fees": total_fees,
    }


def calculate_roundtrip_fees(buy_cost: float, sell_revenue: float) -> dict:
    """
    Computes total round-trip charges (both BUY and SELL legs).
    """
    buy_fees = calculate_order_fees(buy_cost, is_buy=True)
    sell_fees = calculate_order_fees(sell_revenue, is_buy=False)
    total_roundtrip = round(buy_fees["total_fees"] + sell_fees["total_fees"], 2)

    return {
        "buy_fees": buy_fees,
        "sell_fees": sell_fees,
        "total_fees": total_roundtrip,
    }


def calculate_net_pnl(buy_cost: float, sell_revenue: float) -> dict:
    """
    Computes gross P&L, total round-trip fees, and net realized P&L (amount & %).
    """
    fees = calculate_roundtrip_fees(buy_cost, sell_revenue)
    gross_pnl = round(sell_revenue - buy_cost, 2)
    total_fees = fees["total_fees"]
    net_pnl = round(gross_pnl - total_fees, 2)
    net_pct = round((net_pnl / buy_cost * 100.0), 2) if buy_cost > 0 else 0.0
    gross_pct = round((gross_pnl / buy_cost * 100.0), 2) if buy_cost > 0 else 0.0

    return {
        "buy_cost": round(buy_cost, 2),
        "sell_revenue": round(sell_revenue, 2),
        "gross_pnl": gross_pnl,
        "gross_pct": gross_pct,
        "total_fees": total_fees,
        "net_pnl": net_pnl,
        "net_pct": net_pct,
        "fee_breakdown": fees,
    }


def calculate_breakeven_exit_price(entry_price: float, quantity: int) -> float:
    """
    Calculates the exact exit price required to break even after paying
    all buy fees, sell fees, statutory taxes, and DP charges.
    """
    if quantity <= 0 or entry_price <= 0:
        return entry_price

    buy_turnover = entry_price * quantity
    buy_fees = calculate_order_fees(buy_turnover, is_buy=True)["total_fees"]

    # Fixed sell fee components (DP charge ₹15.93) plus approximate variable percentage
    # Variable sell rate: brokerage (0.05%) + STT (0.1%) + GST on brokerage (0.009%) + exchange (0.003%) ~ 0.162%
    # sell_turnover = exit_price * quantity
    # Net: exit_price * quantity - sell_fees = entry_price * quantity + buy_fees
    # Let v = variable sell rate (~0.00165), fixed = DP_CHARGE_WITH_GST
    # exit_price * quantity * (1 - v) - fixed = buy_turnover + buy_fees
    # exit_price = (buy_turnover + buy_fees + fixed) / (quantity * (1 - v))
    v = BROKERAGE_PCT + STT_DELIVERY_PCT + (BROKERAGE_PCT * GST_RATE) + NSE_TXN_CHARGE_PCT
    needed_revenue = (buy_turnover + buy_fees + DP_CHARGE_WITH_GST) / (1.0 - v)
    breakeven_price = round(needed_revenue / quantity, 2)
    return max(breakeven_price, entry_price)
