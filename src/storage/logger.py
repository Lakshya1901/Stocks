"""
logger.py — Structured Logging Setup

Configures loguru with rotating file handlers and color-coded console output.
"""

import os
import sys
from loguru import logger


LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")


def setup_logging(log_level: str = "INFO"):
    """
    Configure structured logging with separate log files.

    Creates:
      - logs/trader.log      — All messages
      - logs/trades.log      — Trade executions only
      - logs/signals.log     — Strategy signals only
      - logs/errors.log      — Errors and warnings only
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    # Remove default handler
    logger.remove()

    # Console output with colors
    logger.add(
        sys.stdout,
        level=log_level,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # Main log file — everything
    logger.add(
        os.path.join(LOG_DIR, "trader.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
    )

    # Trades log — filter for trade-related messages
    logger.add(
        os.path.join(LOG_DIR, "trades.log"),
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
        rotation="10 MB",
        retention="30 days",
        filter=lambda record: any(
            kw in record["message"].lower()
            for kw in ["position opened", "position closed", "order placed", "[paper]", "[live]"]
        ),
    )

    # Signals log
    logger.add(
        os.path.join(LOG_DIR, "signals.log"),
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
        rotation="10 MB",
        retention="30 days",
        filter=lambda record: any(
            kw in record["message"].lower()
            for kw in ["ensemble signal", "buy", "sell", "signal"]
        ),
    )

    # Errors log
    logger.add(
        os.path.join(LOG_DIR, "errors.log"),
        level="WARNING",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation="10 MB",
        retention="30 days",
    )

    logger.info("Logging initialized — log dir: {}", LOG_DIR)
