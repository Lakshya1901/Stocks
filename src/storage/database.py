"""
database.py — SQLite Persistence Layer

Stores all trades, daily summaries, signals, and scanner results.
"""

import os
import sqlite3
from datetime import datetime, date
from loguru import logger


DB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_PATH = os.path.join(DB_DIR, "trades.db")


class Database:
    """SQLite database for trade persistence and analytics."""

    def __init__(self, db_path: str = None):
        self._db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = None
        self._init_db()

    def _init_db(self):
        """Initialize database and create tables."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL DEFAULT 0,
                pnl REAL DEFAULT 0,
                strategy TEXT DEFAULT '',
                sector TEXT DEFAULT 'other',
                entry_time TEXT NOT NULL,
                exit_time TEXT DEFAULT '',
                order_id TEXT DEFAULT '',
                stop_loss REAL DEFAULT 0,
                take_profit REAL DEFAULT 0,
                paper INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS open_positions (
                symbol TEXT PRIMARY KEY,
                signal_type TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL DEFAULT 0,
                take_profit REAL DEFAULT 0,
                trailing_stop REAL DEFAULT 0,
                strategy TEXT DEFAULT '',
                sector TEXT DEFAULT 'other',
                order_id TEXT DEFAULT '',
                entry_time TEXT NOT NULL,
                highest_price REAL DEFAULT 0,
                lowest_price REAL DEFAULT 0,
                paper INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS daily_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL UNIQUE,
                total_pnl REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                max_drawdown REAL DEFAULT 0,
                capital_start REAL DEFAULT 0,
                capital_end REAL DEFAULT 0,
                stocks_scanned INTEGER DEFAULT 0,
                stocks_traded TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                strategy TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                price REAL DEFAULT 0,
                reason TEXT DEFAULT '',
                acted_on INTEGER DEFAULT 0,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scanner_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                score REAL DEFAULT 0,
                price REAL DEFAULT 0,
                tier TEXT DEFAULT '',
                sector TEXT DEFAULT '',
                atr_pct REAL DEFAULT 0,
                volume_ratio REAL DEFAULT 0,
                signals TEXT DEFAULT '',
                selected INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(entry_time);
            CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
            CREATE INDEX IF NOT EXISTS idx_scanner_date ON scanner_results(scan_date);
        """)

        self._conn.commit()
        logger.info("Database initialized at {}", self._db_path)

    def save_open_position(self, pos: dict):
        """Save or update an open position."""
        self._conn.execute("""
            INSERT OR REPLACE INTO open_positions (
                symbol, signal_type, quantity, entry_price, stop_loss, take_profit,
                trailing_stop, strategy, sector, order_id, entry_time, highest_price,
                lowest_price, paper
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pos.get("symbol", ""),
            pos.get("signal_type", ""),
            pos.get("quantity", 0),
            pos.get("entry_price", 0),
            pos.get("stop_loss", 0),
            pos.get("take_profit", 0),
            pos.get("trailing_stop", 0),
            pos.get("strategy", ""),
            pos.get("sector", "other"),
            pos.get("order_id", ""),
            pos.get("entry_time", datetime.now().isoformat()),
            pos.get("highest_price", 0),
            pos.get("lowest_price", 0),
            1 if pos.get("paper", True) else 0,
        ))
        self._conn.commit()

    def delete_open_position(self, symbol: str):
        """Delete an open position once closed."""
        self._conn.execute(
            "DELETE FROM open_positions WHERE symbol = ?", (symbol,))
        self._conn.commit()

    def get_open_positions(self) -> list[dict]:
        """Get all currently open positions."""
        cursor = self._conn.execute("SELECT * FROM open_positions")
        return [dict(row) for row in cursor.fetchall()]

    def record_trade(self, trade: dict):
        """Record a completed trade."""
        self._conn.execute("""
            INSERT INTO trades (
                symbol, transaction_type, quantity, entry_price, exit_price,
                pnl, strategy, sector, entry_time, exit_time, order_id,
                stop_loss, take_profit, paper
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.get("symbol", ""),
            trade.get("transaction_type", ""),
            trade.get("quantity", 0),
            trade.get("entry_price", 0),
            trade.get("exit_price", 0),
            trade.get("pnl", 0),
            trade.get("strategy", ""),
            trade.get("sector", "other"),
            trade.get("entry_time", datetime.now().isoformat()),
            trade.get("exit_time", ""),
            trade.get("order_id", ""),
            trade.get("stop_loss", 0),
            trade.get("take_profit", 0),
            1 if trade.get("paper", True) else 0,
        ))
        self._conn.commit()

    def record_signal(self, signal: dict):
        """Record a generated signal."""
        self._conn.execute("""
            INSERT INTO signals (symbol, signal_type, strategy, confidence, price, reason, acted_on)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            signal.get("symbol", ""),
            signal.get("signal_type", ""),
            signal.get("strategy", ""),
            signal.get("confidence", 0),
            signal.get("price", 0),
            signal.get("reason", ""),
            1 if signal.get("acted_on", False) else 0,
        ))
        self._conn.commit()

    def record_scanner_results(self, scan_date: str, results: list[dict]):
        """Record daily scanner results."""
        for result in results:
            self._conn.execute("""
                INSERT INTO scanner_results (
                    scan_date, symbol, score, price, tier, sector,
                    atr_pct, volume_ratio, signals, selected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                scan_date,
                result.get("symbol", ""),
                result.get("score", 0),
                result.get("price", 0),
                result.get("tier", ""),
                result.get("sector", "other"),
                result.get("atr_pct", 0),
                result.get("volume_ratio", 0),
                str(result.get("signals", [])),
                1 if result.get("selected", False) else 0,
            ))
        self._conn.commit()

    def get_scanner_results(self, scan_date: str) -> list[dict]:
        """Retrieve scan results for a given date."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT symbol, score, tier, sector FROM scanner_results WHERE scan_date = ? AND selected = 1", (scan_date,))
            rows = cursor.fetchall()
            return [{"symbol": row[0], "score": row[1], "tier": row[2], "sector": row[3], "price": 0.0} for row in rows]
        except Exception as e:
            from loguru import logger
            logger.error("Failed to get scanner results: {}", e)
            return []

    def save_daily_summary(self, summary: dict):
        """Save or update daily summary."""
        self._conn.execute("""
            INSERT OR REPLACE INTO daily_summary (
                trade_date, total_pnl, realized_pnl, total_trades,
                winning_trades, losing_trades, win_rate, max_drawdown,
                capital_start, capital_end, stocks_scanned, stocks_traded
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            summary.get("trade_date", date.today().isoformat()),
            summary.get("total_pnl", 0),
            summary.get("realized_pnl", 0),
            summary.get("total_trades", 0),
            summary.get("winning_trades", 0),
            summary.get("losing_trades", 0),
            summary.get("win_rate", 0),
            summary.get("max_drawdown", 0),
            summary.get("capital_start", 0),
            summary.get("capital_end", 0),
            summary.get("stocks_scanned", 0),
            str(summary.get("stocks_traded", [])),
        ))
        self._conn.commit()

    def get_todays_trades(self) -> list[dict]:
        """Get all trades from today."""
        today = date.today().isoformat()
        cursor = self._conn.execute(
            "SELECT * FROM trades WHERE entry_time LIKE ? ORDER BY entry_time DESC",
            (f"{today}%",),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        """Get recent trades."""
        cursor = self._conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_daily_summaries(self, days: int = 30) -> list[dict]:
        """Get recent daily summaries."""
        cursor = self._conn.execute(
            "SELECT * FROM daily_summary ORDER BY trade_date DESC LIMIT ?", (
                days,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            logger.info("Database connection closed")
