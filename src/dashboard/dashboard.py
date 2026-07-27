"""
dashboard.py — Real-Time Trading Dashboard

Flask + WebSocket server providing a live monitoring UI.
"""

from datetime import datetime
from flask import Flask, send_from_directory, jsonify, request
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


@app.route("/api/config", methods=["GET", "POST"])
def manage_config():
    """Get or update bot configuration."""
    import yaml
    import os
    config_path = os.path.join(os.path.dirname(
        __file__), "..", "..", "config.yaml")

    if request.method == "GET":
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            return jsonify(config)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    elif request.method == "POST":
        try:
            new_settings = request.json
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)

            # Update trading section
            if "trading" not in config:
                config["trading"] = {}
            if "mode" in new_settings:
                config["trading"]["mode"] = new_settings["mode"]
            if "capital" in new_settings:
                config["trading"]["capital"] = float(new_settings["capital"])
            if "max_per_trade" in new_settings:
                config["trading"]["max_per_trade"] = float(
                    new_settings["max_per_trade"])
            if "max_daily_loss" in new_settings:
                config["trading"]["max_daily_loss"] = float(
                    new_settings["max_daily_loss"])

            # Save back to file
            with open(config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

            # Hot reload RiskManager
            if _engine and _engine.get("risk_manager"):
                _engine["risk_manager"].update_config(config["trading"])

            return jsonify({"status": "success", "message": "Configuration updated"})
        except Exception as e:
            logger.error("Failed to update config: {}", e)
            return jsonify({"error": str(e)}), 500


@app.route("/api/export_trades")
def export_trades():
    """Export historical trades as Excel."""
    from flask import send_file
    import io
    import pandas as pd

    if _engine is None or _engine.get("database") is None:
        return jsonify({"error": "Database not available"}), 500

    db = _engine.get("database")

    # Fetch historical trades
    recent_trades = db.get_recent_trades(limit=1000)
    trades_df = pd.DataFrame(recent_trades)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if not trades_df.empty:
            drop_cols = ['id'] if 'id' in trades_df.columns else []
            trades_df.drop(columns=drop_cols, errors='ignore').to_excel(
                writer, sheet_name='Trade History', index=False)

            # Calculate Summary Metrics
            total_pnl_rupees = trades_df['pnl'].sum(
            ) if 'pnl' in trades_df.columns else 0.0

            # Try to get number of trading days from daily summary, otherwise estimate based on unique dates
            summaries = db.get_daily_summaries(days=365)
            total_days = len(summaries) if summaries else 1

            # Try to get base capital
            base_capital = summaries[0].get(
                "capital_start", 20000.0) if summaries else 20000.0

            total_pnl_pct = (total_pnl_rupees / base_capital) * \
                100 if base_capital > 0 else 0.0
            avg_per_day_rupees = total_pnl_rupees / total_days
            avg_per_month_rupees = avg_per_day_rupees * 20  # 20 trading days in a month
            avg_per_year_rupees = avg_per_day_rupees * 250  # 250 trading days in a year

            summary_data = [
                {"Metric": "Total Net Profit/Loss (₹)",
                 "Value": f"₹{total_pnl_rupees:.2f}"},
                {"Metric": "Total Net Profit/Loss (%)",
                 "Value": f"{total_pnl_pct:.2f}%"},
                {"Metric": "Trading Days Tracked", "Value": f"{total_days} Days"},
                {"Metric": "Average Profit Per Day (₹)",
                 "Value": f"₹{avg_per_day_rupees:.2f}"},
                {"Metric": "Projected Average Per Month (₹)",
                 "Value": f"₹{avg_per_month_rupees:.2f}"},
                {"Metric": "Projected Average Per Year (₹)",
                 "Value": f"₹{avg_per_year_rupees:.2f}"}
            ]

            summary_df = pd.DataFrame(summary_data)
            summary_df.to_excel(
                writer, sheet_name='Summary Metrics', index=False)

        else:
            pd.DataFrame([{"Message": "No trades executed yet."}]).to_excel(
                writer, sheet_name='Trade History', index=False)

    output.seek(0)

    return send_file(
        output,
        download_name="DayTrader_History.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


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
    socketio.run(app, host=host, port=port, debug=debug,
                 allow_unsafe_werkzeug=True)
