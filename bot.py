#!/usr/local/bin/python3
"""
bot.py — Main entry point. Runs the trading loop + live dashboard in one window.

Usage:
    python3 bot.py

The bot will:
  1. Load FinBERT model and NSE symbol table
  2. Authenticate with Groww
  3. Show a live dashboard in the terminal — no second window needed
  4. Every POLL_INTERVAL_SECONDS:
      a. Refresh current holdings from Groww
      b. Check all open positions for SL/TP exits
      c. Fetch new headlines from RSS feeds
      d. For each headline: extract symbol, check portfolio, analyze sentiment
      e. If signal is strong enough and viable: place a BUY order
  5. At market close (15:30 IST): reset the seen-headlines cache for tomorrow
"""

from config import get_logger
import news
import risk
import broker
import positions
import market
import report

import re
import sys
import time
import json
import threading
from datetime import datetime
from pathlib import Path

import schedule
import pytz
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.console import Console
from rich import box

from config import (
    DRY_RUN,
    POLL_INTERVAL_SECONDS,
    SENTIMENT_THRESHOLD,
    TECHNICAL_SCORE_THRESHOLD,
    TRADES_FILE,
    OVERBOUGHT_RSI,
    OVERBOUGHT_OVERRIDE_SENTIMENT,
    MAX_DAY_GAIN_PCT,
)

logger   = get_logger(__name__)
console  = Console()
IST      = pytz.timezone("Asia/Kolkata")

# Max concurrent positions: with 20% per trade, 5 fills 100% of capital
MAX_CONCURRENT_POSITIONS = 5

# Headlines that describe price action or list many stocks, not company news.
# FinBERT scores these "positive", but there is no new information to trade on.
_NON_NEWS_RE = re.compile(
    r"share price live|live updates|\b(among|top)\s+\d+\b|\b\d+\s+(\w+\s+)?stocks\b|stocks? to (buy|watch)",
    re.IGNORECASE,
)

# Recent log lines shown in the dashboard status panel
_log_lines: list[str] = []
_log_lock  = threading.Lock()
_live_instance = None

def _push_log(msg: str) -> None:
    with _log_lock:
        _log_lines.append(msg)
        if len(_log_lines) > 12:
            _log_lines.pop(0)
    if _live_instance is not None:
        try:
            _live_instance.update(_build_layout())
        except Exception:
            pass



# ── Dashboard rendering ────────────────────────────────────────────────────────

def _read_trades() -> list[dict]:
    trades = []
    if TRADES_FILE.exists():
        with open(TRADES_FILE) as f:
            for line in f:
                if line.strip():
                    try:
                        trades.append(json.loads(line))
                    except Exception:
                        pass
    return trades


# Dashboard redraws every second; only re-read trades.jsonl when it changes (or the day rolls over)
_trades_cache: dict = {"key": None, "trades": [], "pnl_line": ""}


def _cached_trades_and_pnl() -> tuple[list[dict], str]:
    st = TRADES_FILE.stat() if TRADES_FILE.exists() else None
    key = (st.st_mtime_ns, st.st_size) if st else None, datetime.now(IST).date()
    if _trades_cache["key"] != key:
        _trades_cache.update(key=key, trades=_read_trades(), pnl_line=report.format_pnl_rich_line())
    return _trades_cache["trades"], _trades_cache["pnl_line"]


def _trades_table() -> Table:
    trades = _cached_trades_and_pnl()[0][-20:]  # last 20 trades

    tbl = Table(
        title="Trades",
        box=box.ROUNDED,
        header_style="bold cyan",
        expand=True,
        show_edge=True,
    )
    tbl.add_column("Time",   style="dim",     width=19)
    tbl.add_column("Mode",   justify="center", width=7)
    tbl.add_column("Action", justify="center", width=6)
    tbl.add_column("Symbol", style="magenta",  width=14)
    tbl.add_column("Qty",    justify="right",  width=5)
    tbl.add_column("Price",  justify="right",  width=10)
    tbl.add_column("Total",  justify="right",  style="green", width=12)
    tbl.add_column("Reason", style="italic")

    if not trades:
        tbl.add_row("", "", "", "[dim]Waiting for first trade...[/]", "", "", "", "")
    else:
        for t in trades:
            action_str = "[bold green]BUY[/]"  if t["action"] == "BUY" else "[bold red]SELL[/]"
            mode_str   = "[yellow]DRY[/]" if t["mode"] == "DRY_RUN" else "[bold red]LIVE[/]"
            tbl.add_row(
                t["timestamp"],
                mode_str,
                action_str,
                t["symbol"],
                str(t["quantity"]),
                f"\u20b9{t['price']:,.2f}",
                f"\u20b9{t['total']:,.2f}",
                t.get("reason", ""),
            )
    return tbl


def _status_panel() -> Panel:
    now        = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    market     = "[bold green]OPEN[/]" if risk.is_market_open() else "[bold red]CLOSED[/]"
    mode_str   = "[yellow]DRY RUN (Paper)[/]" if DRY_RUN else "[bold red]LIVE TRADING[/]"
    n_pos      = positions.open_position_count()
    available  = broker.get_account_balance()
    portfolio  = broker.get_portfolio_summary()
    pnl_line   = _cached_trades_and_pnl()[1]

    # Show portfolio total and available capital
    total_cap = portfolio.get("total", 0.0)
    if total_cap > 0 and not DRY_RUN:
        cap_str = f"Portfolio: ₹{total_cap:,.2f}   |   Available: ₹{available:,.2f}"
    else:
        cap_str = f"Capital: ₹{available:,.2f}"

    with _log_lock:
        log_text = "\n".join(_log_lines[-7:]) or "[dim]Starting up...[/]"

    content = (
        f"  Time:    {now}\n"
        f"  Market:  {market}   |   Mode: {mode_str}   |   {cap_str}   |   Open: {n_pos}/{MAX_CONCURRENT_POSITIONS}\n"
        f"{pnl_line}\n\n"
        f"{log_text}"
    )
    return Panel(content, title="Status", border_style="cyan", expand=True)


def _build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="status", size=14),
        Layout(name="trades"),
    )
    layout["status"].update(_status_panel())
    layout["trades"].update(_trades_table())
    return layout



# ── Bot logic ─────────────────────────────────────────────────────────────────

def on_market_open() -> None:
    positions.load_positions()
    broker.refresh_portfolio_capital()
    n_pos = positions.open_position_count()
    portfolio = broker.get_portfolio_summary()
    total = portfolio.get("total", 0.0)
    msg = f"=== Market OPEN (09:15 IST) — portfolio ₹{total:,.2f} — monitoring {n_pos} position(s) ==="
    logger.info(msg)
    _push_log(f"[green]{msg}[/]")
    news.reset_seen_urls()
    positions.refresh_holdings()


def on_market_close() -> None:
    n_pos = positions.open_position_count()
    msg = f"=== Market CLOSE (15:30 IST) — holding {n_pos} position(s) overnight ==="
    logger.info(msg)
    _push_log(f"[yellow]{msg}[/]")


def run_cycle() -> None:
    now = datetime.now(IST).strftime("%H:%M:%S")
    logger.debug(f"--- Cycle start @ {now} IST ---")

    positions.refresh_holdings()

    if not risk.is_market_open():
        logger.debug("Market closed — skipping exits and news scan")
        return

    positions.check_all_positions()

    articles = news.fetch_new_headlines()
    _push_log(f"[dim]{datetime.now(IST).strftime('%H:%M:%S')} — fetched {len(articles)} new headline(s)[/]")

    for article in articles:
        headline = article["headline"]
        logger.info(f"Processing: {headline}")

        symbol = news.extract_symbol(headline)
        if symbol is None:
            logger.debug("No recognisable stock mentioned — skipping")
            continue

        if _NON_NEWS_RE.search(headline):
            logger.debug(f"Price-report / list headline, not news — skipping: {headline}")
            continue

        positions.check_holdings_against_news(headline, symbol)

        if positions.is_watching(symbol) or positions.is_holding(symbol):
            logger.debug(f"Already holding or watching {symbol} — skipping BUY")
            continue

        if positions.open_position_count() >= MAX_CONCURRENT_POSITIONS:
            logger.info(f"Max positions ({MAX_CONCURRENT_POSITIONS}) reached — skipping this trade")
            continue

        label, score = news.analyze(headline)
        logger.info(f"Sentiment for {symbol}: {label.upper()} ({score:.2f})")

        if label != "positive" or score < SENTIMENT_THRESHOLD:
            _push_log(f"[dim]{datetime.now(IST).strftime('%H:%M:%S')} {symbol}: {label} ({score:.2f}) < {SENTIMENT_THRESHOLD}[/]")
            continue

        _push_log(f"[bold cyan]{datetime.now(IST).strftime('%H:%M:%S')} Candidate: {symbol} (sentiment {score:.2f})[/]")

        # --- Technical Analysis (Overbought Blocker) ---
        # Technicals are backward-looking; news is forward-looking.
        # We don't want technicals to block a high-confidence news signal.
        # Rule: only block if RSI > OVERBOUGHT_RSI (stock already ran up massively),
        #       AND sentiment is below OVERBOUGHT_OVERRIDE_SENTIMENT.
        # An exceptional sentiment score overrides even an overbought signal.
        tech_score, tech_details = market.get_technical_score(symbol)
        rsi = tech_details.get("rsi", 50)
        if rsi > OVERBOUGHT_RSI and score < OVERBOUGHT_OVERRIDE_SENTIMENT:
            logger.info(
                f"OVERBOUGHT BLOCK: {symbol} RSI={rsi:.1f} (score={tech_score:.2f}) "
                f"— stock already extended, sentiment {score:.2f} not strong enough to override"
            )
            _push_log(
                f"[yellow]{datetime.now(IST).strftime('%H:%M:%S')} "
                f"OVERBOUGHT {symbol} RSI={rsi:.0f}[/]"
            )
            continue

        if "error" not in tech_details and tech_score < TECHNICAL_SCORE_THRESHOLD:
            logger.info(f"TECH BLOCK: {symbol} technical score {tech_score:.2f} < {TECHNICAL_SCORE_THRESHOLD}")
            _push_log(f"[yellow]{datetime.now(IST).strftime('%H:%M:%S')} {symbol} technicals {tech_score:.2f} — skipped[/]")
            continue

        # --- Anti-chase: the move already happened ---
        day_change = (market.get_quote(symbol) or {}).get("day_change_pct")
        if day_change is not None and day_change > MAX_DAY_GAIN_PCT:
            logger.info(f"CHASE BLOCK: {symbol} already up {day_change:+.1%} today — news priced in")
            _push_log(f"[yellow]{datetime.now(IST).strftime('%H:%M:%S')} {symbol} already up {day_change:+.1%} — skipped[/]")
            continue

        params = risk.calculate_trade_params(symbol)
        if params is None:
            _push_log(f"[dim yellow]{datetime.now(IST).strftime('%H:%M:%S')} {symbol}: skipped by risk/liquidity guard[/]")
            continue

        sig = f"BUY {params.quantity} x {symbol} @ \u20b9{params.entry_price:.2f} (tech={tech_score})"
        logger.info(f"SIGNAL: {sig} | {article['source']}")
        _push_log(f"[green]{datetime.now(IST).strftime('%H:%M:%S')} SIGNAL: {sig}[/]")

        journal = {
            "headline": headline, "source": article["source"], "sentiment": score,
            "tech_score": tech_score, "rsi": rsi, "day_change_pct": day_change,
        }
        order_id = broker.place_buy_order(params, journal)
        if order_id:
            positions.register_position(params, order_id)

    logger.debug("--- Cycle end ---\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    mode = "DRY RUN" if DRY_RUN else "LIVE TRADING"
    logger.info("=" * 70)
    logger.info(f"Starting Groww Sentiment Bot — {mode}")

    # Schedule market open/close handlers (Mon–Fri)
    for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
        getattr(schedule.every(), day).at("09:15").do(on_market_open)
        getattr(schedule.every(), day).at("15:30").do(on_market_close)

    try:
        positions.refresh_holdings()
    except Exception as exc:
        logger.error(f"Could not connect to Groww on startup: {exc}")

    if not DRY_RUN and broker.is_ddpi_enabled() is False:
        msg = ("DDPI is OFF on your Groww account: stop-loss/take-profit SELLs of shares held overnight "
               "will be rejected unless you authorise them with CDSL TPIN each day. Activate DDPI in the Groww app.")
        logger.warning(msg)
        _push_log(f"[bold red]{msg}[/]")

    # Fetch total portfolio value from Groww (LIVE) or use config (DRY_RUN)
    total_capital = broker.refresh_portfolio_capital()
    logger.info(f"Trading capital: ₹{total_capital:,.2f}")

    positions.load_positions()
    n_active = positions.open_position_count()
    if n_active > 0:
        _push_log(f"[cyan]Loaded {n_active} active multi-day position(s) from disk[/]")

    _push_log(f"[cyan]Bot started — {mode} — capital ₹{total_capital:,.2f} — press Ctrl+C to stop[/]")

    if not sys.stdout.isatty():
        # Headless (cloud server / systemd / nohup): plain log lines, no full-screen dashboard
        try:
            while True:
                try:
                    schedule.run_pending()
                    run_cycle()
                except Exception as cycle_exc:
                    logger.error(f"Error in trading cycle: {cycle_exc}", exc_info=True)
                time.sleep(POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        return

    global _live_instance
    console.clear()
    with Live(
        _build_layout(),
        console=console,
        refresh_per_second=1.0,
        screen=True,
    ) as live:
        _live_instance = live
        try:
            while True:
                try:
                    schedule.run_pending()
                    run_cycle()
                    live.update(_build_layout())
                except Exception as cycle_exc:
                    logger.error(f"Error in trading cycle: {cycle_exc}", exc_info=True)
                    _push_log(f"[red]Error in cycle: {cycle_exc}[/]")
                    live.update(_build_layout())

                # Sleep in 1-second ticks to keep dashboard clock live and respond instantly to Ctrl+C
                for _ in range(POLL_INTERVAL_SECONDS):
                    time.sleep(1)
                    live.update(_build_layout())
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        finally:
            _live_instance = None



if __name__ == "__main__":
    main()
