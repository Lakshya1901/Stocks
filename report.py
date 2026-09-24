#!/usr/bin/env python3
"""
report.py — Net P&L report for the paper (or live) test.

Usage:
    python3 report.py          # DRY_RUN trades
    python3 report.py --live   # LIVE trades

Realized = closed trades (FIFO), after all Groww charges.
Unrealized = open positions valued at the current price, as if sold now, after all charges.
"""

from config import get_logger
import risk
import market

import sys
import json

from config import TRADES_FILE, OPEN_POSITIONS_FILE, DRY_RUN


def main() -> None:
    mode = "LIVE" if "--live" in sys.argv else "DRY_RUN"
    trades = []
    if TRADES_FILE.exists():
        trades = [json.loads(l) for l in TRADES_FILE.read_text().splitlines() if l.strip()]
    trades = [t for t in trades if t.get("mode") == mode]

    realized = calculate_pnl_summary(mode)["all_time"]

    positions = json.loads(OPEN_POSITIONS_FILE.read_text()) if OPEN_POSITIONS_FILE.exists() else {}
    open_rows, unreal_net, unreal_cost = [], 0.0, 0.0
    for sym, p in positions.items():
        price = market.get_current_price(sym)
        if price is None:
            open_rows.append(f"  {sym:<12} qty={p['quantity']:<4} entry=₹{p['entry_price']:<9.2f} price unavailable")
            continue
        cost = p["entry_price"] * p["quantity"]
        pnl = risk.calculate_net_pnl(cost, price * p["quantity"])
        unreal_net += pnl["net_pnl"]
        unreal_cost += cost
        open_rows.append(
            f"  {sym:<12} qty={p['quantity']:<4} entry=₹{p['entry_price']:<9.2f} now=₹{price:<9.2f} "
            f"net ₹{pnl['net_pnl']:+,.2f} ({pnl['net_pct']:+.2f}%)"
        )

    buys = [t for t in trades if t["action"] == "BUY"]
    sells = [t for t in trades if t["action"] == "SELL"]
    total_net = realized["amt"] + unreal_net
    deployed = sum(t["total"] for t in buys)

    print(f"=== {mode} report ===")
    if trades:
        print(f"Period: {trades[0]['timestamp']}  →  {trades[-1]['timestamp']}")
    print(f"Buys: {len(buys)}   Sells: {len(sells)}   Open now: {len(positions)}")
    print(f"Realized net P&L   : ₹{realized['amt']:+,.2f} ({realized['pct']:+.2f}% on closed trades, "
          f"fees ₹{realized['fees']:,.2f}, {realized['count']} closed)")
    print(f"Unrealized net P&L : ₹{unreal_net:+,.2f}" + (f" ({unreal_net / unreal_cost * 100:+.2f}%)" if unreal_cost else ""))
    print(f"TOTAL net P&L      : ₹{total_net:+,.2f}   (capital deployed across all buys: ₹{deployed:,.2f})")
    if open_rows:
        print("\nOpen positions (if sold now):")
        print("\n".join(open_rows))
    if sells:
        print("\nExits:")
        for t in sells:
            print(f"  {t['timestamp']}  {t['symbol']:<12} {t['quantity']} @ ₹{t['price']:.2f}  {t.get('reason', '')}")


# =============================================================================
# Computes realized Net Profit/Loss % and INR value across timeframes.
# =============================================================================
import re
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
import pytz


logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def calculate_pnl_summary(mode: str | None = None) -> dict:
    """
    Reads trades from TRADES_FILE, matches BUY and SELL executions via FIFO,
    and deducts exact Groww delivery fees.
    Returns:
        {
            "mode": "DRY_RUN" or "LIVE",
            "today": {"pct": float, "amt": float, "gross_amt": float, "fees": float, "count": int},
            "month": ...,
            "year":  ...,
            "all_time": ...,
        }
    """
    if mode is None:
        mode = "DRY_RUN" if DRY_RUN else "LIVE"

    now = datetime.now(IST)
    empty_bucket = {"pct": 0.0, "amt": 0.0, "gross_amt": 0.0, "fees": 0.0, "count": 0}

    if not Path(TRADES_FILE).exists():
        return {
            "mode": mode,
            "today": empty_bucket.copy(),
            "month": empty_bucket.copy(),
            "year": empty_bucket.copy(),
            "all_time": empty_bucket.copy(),
        }

    inventory = defaultdict(deque)  # sym -> deque of [qty, price, timestamp]
    closed_trades: list[dict] = []

    try:
        with open(TRADES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    t = json.loads(line_str)
                except Exception:
                    continue

                if t.get("mode") != mode:
                    continue

                sym = str(t.get("symbol", "")).strip().upper()
                action = str(t.get("action", "")).strip().upper()
                qty = int(t.get("quantity", 0) or 0)
                price = float(t.get("price", 0.0) or 0.0)
                reason = str(t.get("reason", ""))
                ts_str = str(t.get("timestamp", ""))

                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                except Exception:
                    ts = now

                if action == "BUY":
                    inventory[sym].append([qty, price, ts])
                elif action == "SELL":
                    sell_qty = qty
                    sell_rev = 0.0
                    buy_cost = 0.0

                    while sell_qty > 0 and inventory[sym]:
                        b_qty, b_price, b_ts = inventory[sym][0]
                        matched = min(sell_qty, b_qty)
                        sell_rev += matched * price
                        buy_cost += matched * b_price
                        b_qty -= matched
                        sell_qty -= matched
                        if b_qty == 0:
                            inventory[sym].popleft()
                        else:
                            inventory[sym][0][0] = b_qty

                    # Fallback for orphan sells (e.g. initial manual holding sell without prior buy)
                    if buy_cost == 0.0 and sell_qty > 0:
                        m = re.search(r"P&L\s*([+-]?\d+(?:\.\d+)?)%", reason)
                        if m:
                            pct = float(m.group(1))
                            denom = 1.0 + (pct / 100.0)
                            if denom > 0:
                                buy_cost = (sell_qty * price) / denom
                                sell_rev = sell_qty * price

                    if buy_cost > 0:
                        fee_stats = risk.calculate_net_pnl(buy_cost, sell_rev)
                        closed_trades.append({
                            "time": ts,
                            "cost": buy_cost,
                            "rev": sell_rev,
                            "gross_pnl": fee_stats["gross_pnl"],
                            "fees": fee_stats["total_fees"],
                            "pnl": fee_stats["net_pnl"],
                        })
    except Exception as exc:
        logger.error(f"Error reading trades for P&L: {exc}")

    def calc_bucket(subset: list[dict]) -> dict:
        tot_cost = sum(x["cost"] for x in subset)
        tot_gross = sum(x["gross_pnl"] for x in subset)
        tot_fees = sum(x["fees"] for x in subset)
        tot_net = sum(x["pnl"] for x in subset)
        net_pct = (tot_net / tot_cost * 100.0) if tot_cost > 0 else 0.0
        gross_pct = (tot_gross / tot_cost * 100.0) if tot_cost > 0 else 0.0
        return {
            "pct": round(net_pct, 2),
            "amt": round(tot_net, 2),
            "gross_pct": round(gross_pct, 2),
            "gross_amt": round(tot_gross, 2),
            "fees": round(tot_fees, 2),
            "count": len(subset),
        }

    today_trades = [x for x in closed_trades if x["time"].date() == now.date()]
    month_trades = [x for x in closed_trades if x["time"].year == now.year and x["time"].month == now.month]
    year_trades  = [x for x in closed_trades if x["time"].year == now.year]
    all_trades   = closed_trades

    return {
        "mode": mode,
        "today": calc_bucket(today_trades),
        "month": calc_bucket(month_trades),
        "year":  calc_bucket(year_trades),
        "all_time": calc_bucket(all_trades),
    }


def _format_metric_rich(data: dict) -> str:
    pct = data.get("pct", 0.0)
    amt = data.get("amt", 0.0)
    fees = data.get("fees", 0.0)
    fee_tag = f" [dim](fees: ₹{fees:,.2f})[/]" if fees > 0 else ""

    if pct > 0:
        return f"[bold green]+{pct:.2f}% (+₹{amt:,.2f})[/]{fee_tag}"
    elif pct < 0:
        return f"[bold red]{pct:.2f}% (-₹{abs(amt):,.2f})[/]{fee_tag}"
    else:
        return f"[dim]0.00% (₹0.00)[/]"


def format_pnl_rich_line(mode: str | None = None) -> str:
    """
    Returns a single-line Rich markup string showing Net P&L % and INR for:
    Today, This Month, This Year, and All Time.
    """
    summary = calculate_pnl_summary(mode)
    today_str = _format_metric_rich(summary["today"])
    month_str = _format_metric_rich(summary["month"])
    year_str  = _format_metric_rich(summary["year"])
    all_str   = _format_metric_rich(summary["all_time"])

    mode_label = "Paper Net P&L" if summary["mode"] == "DRY_RUN" else "Live Net P&L"
    return (
        f"  {mode_label}:  "
        f"Today: {today_str}   |   "
        f"Month: {month_str}   |   "
        f"Year: {year_str}   |   "
        f"All Time: {all_str}"
    )


if __name__ == "__main__":
    main()
