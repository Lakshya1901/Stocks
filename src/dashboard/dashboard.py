"""
dashboard.py — Real-Time Trading Dashboard

Flask + WebSocket server providing a live monitoring UI.
"""

import json
from datetime import datetime
from flask import Flask, render_template, send_from_directory, jsonify, request
from flask_socketio import SocketIO
from loguru import logger

import os
import logging

# Suppress Werkzeug HTTP request logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, static_folder=STATIC_DIR)
app.config["SECRET_KEY"] = "trader-dashboard-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Reference to the main trading engine (set by main.py)
_engine = None


def set_engine(engine):
    """Set the trading engine reference for API access."""
    global _engine
    _engine = engine


@app.route("/")
def index():
    """Serve the dashboard UI."""
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/status")
def get_status():
    """Get current bot status."""
    if _engine is None:
        return jsonify({"status": "offline"})

    return jsonify({
        "status": "running" if _engine.get("running", False) else "stopped",
        "mode": _engine.get("mode", "paper"),
        "uptime": _engine.get("uptime", ""),
        "market_open": _engine.get("market_open", False),
    })


@app.route("/api/portfolio")
def get_portfolio():
    """Get portfolio data."""
    if _engine is None:
        return jsonify({})

    portfolio = _engine.get("portfolio")
    if portfolio:
        return jsonify(portfolio.to_dict())
    return jsonify({})


@app.route("/api/trades")
def get_trades():
    """Get today's trades."""
    if _engine is None:
        return jsonify([])

    db = _engine.get("database")
    if db:
        return jsonify(db.get_todays_trades())
    return jsonify([])


@app.route("/api/signals")
def get_signals():
    """Get recent signals."""
    if _engine is None:
        return jsonify([])

    ensemble = _engine.get("ensemble")
    if ensemble:
        return jsonify(ensemble.get_signal_history(limit=50))
    return jsonify([])


@app.route("/api/scanner")
def get_scanner():
    """Get today's scanner results."""
    if _engine is None:
        return jsonify([])

    return jsonify(_engine.get("scanner_results", []))


@app.route("/api/metrics")
def get_metrics():
    """Get performance metrics."""
    if _engine is None:
        return jsonify({})

    portfolio = _engine.get("portfolio")
    risk = _engine.get("risk_manager")

    data = {}
    if portfolio:
        data["performance"] = portfolio.get_performance_metrics()
    if risk:
        data["risk"] = risk.get_daily_stats()
    data["mode"] = _engine.get("mode", "paper")

    return jsonify(data)


@app.route("/api/control", methods=["POST"])
def control():
    """Bot control endpoint."""
    action = request.json.get("action")
    if action == "emergency_squareoff":
        _engine["emergency_squareoff"] = True
        logger.warning("EMERGENCY SQUARE-OFF triggered from dashboard")
        return jsonify({"status": "ok", "message": "Emergency square-off initiated"})

    return jsonify({"status": "error", "message": "Unknown action"})


def emit_update(event: str, data: dict):
    """Emit a real-time update to all connected dashboard clients."""
    try:
        socketio.emit(event, data)
    except Exception as e:
        logger.debug("Dashboard emit failed: {}", e)


def emit_tick(symbol: str, price: float, pnl: float = 0):
    """Emit a price tick update."""
    emit_update("tick", {
        "symbol": symbol,
        "price": price,
        "pnl": pnl,
        "time": datetime.now().strftime("%H:%M:%S"),
    })


def emit_trade(trade_data: dict):
    """Emit a trade execution update."""
    emit_update("trade", trade_data)


def emit_signal(signal_data: dict):
    """Emit a strategy signal update."""
    emit_update("signal", signal_data)


def emit_portfolio(portfolio_data: dict):
    """Emit a portfolio update."""
    emit_update("portfolio", portfolio_data)


def start_dashboard(host: str = "0.0.0.0", port: int = 8080, debug: bool = False):
    """Start the dashboard server."""
    logger.info("Starting dashboard on {}:{}", host, port)
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)
