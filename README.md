<div align="center">
  <h1>⚡ DayTrader Bot</h1>
  <p><strong>Fully automated, real-time algorithmic day trading bot for the NSE (India).</strong></p>
  
  <p>
    <a href="https://github.com/yourusername/Stocks/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
    <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-green.svg" alt="Python Version"></a>
    <a href="https://groww.in/"><img src="https://img.shields.io/badge/Broker-Groww-orange.svg" alt="Broker: Groww"></a>
    <a href="#"><img src="https://img.shields.io/badge/Status-Active-success.svg" alt="Status"></a>
  </p>
</div>

---

**DayTrader Bot** is a headless, fully automated intraday trading engine that scans the entire National Stock Exchange (NSE) universe of 1800+ stocks every morning, selects the best candidates, and executes trades autonomously using an ensemble of three proven strategies.

It includes a beautiful **real-time glassmorphism dashboard** to monitor your P&L, live positions, and strategy signals.

> [!WARNING]
> **Disclaimer:** This software is for educational purposes only. Do not risk money which you are afraid to lose. USE THE SOFTWARE AT YOUR OWN RISK. The authors and contributors assume no responsibility for your trading results. Always test extensively in **Paper Trading Mode** before going live.

---

## 🌟 Key Features

- **Full NSE Universe Scanner**: Scans 1800+ equities daily at 9:00 AM. Filters for liquidity, volatility (ATR), price action gaps, and momentum to select the top 50 stocks for the day.
- **Ensemble Strategy Engine**: Runs three strategies concurrently:
  - *VWAP Mean Reversion* (Range-bound markets)
  - *Momentum Breakout* (Trend following)
  - *Opening Range Breakout / ORB* (Morning volatility)
- **Strict Risk Management**: Enforces maximum daily loss limits, per-trade position sizing, 2:1 reward/risk ratios, trailing stops, and sector exposure caps.
- **Automated Authentication**: Uses `pyotp` for completely headless TOTP login. No manual API key refreshing required.
- **Real-Time Dashboard**: Flask + WebSocket UI with live charts, position tables, and an emergency square-off button.
- **SQLite Persistence**: Automatically logs all trades, strategy signals, and daily summaries to a local database for backtesting and review.

---

## 🚀 Quick Start (Local Paper Trading)

The safest way to learn how the bot works is to run it locally on your machine in **Paper Trading mode**. It will simulate trades against real-time market data without risking a single penny.

### 1. Clone & Install

```bash
git clone https://github.com/Lakshya1901/Stocks.git
cd Stocks
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Credentials

```bash
cp .env.example .env
```

Edit `.env` and add your Groww TOTP credentials:
```env
GROWW_TOTP_TOKEN=your_totp_token_here
GROWW_TOTP_SECRET=your_totp_secret_here
```
*(To get these: Log into Groww → Profile → Settings → Trading APIs → Generate TOTP Token).*

### 3. Review Configuration

Open `config.yaml`. Every parameter is heavily documented. Feel free to adjust the `capital`, `top_picks`, or strategy `weights`. **Ensure `mode` is set to `paper`.**

### 4. Run the Bot

```bash
python -m src.main
```
The bot will initialize. Open your web browser and go to `http://localhost:8080` to view the live dashboard!

---

## ☁️ Production Deployment (Live Trading)

Because the Groww API requires a **Static IP address** for order execution, you cannot run live trades from a standard home internet connection or a laptop that goes to sleep.

You must deploy the bot to a cloud server. 

Please refer to our complete, step-by-step **[Production Deployment Guide (setup_cloud.md)](setup_cloud.md)**. It explains how to:
1. Create a 100% Free Tier AWS EC2 server.
2. Attach an Elastic IP (Static IP).
3. Deploy the bot as a `systemd` background service that runs 24/7.

---

## 🧠 How It Works (The Daily Lifecycle)

If left running on a cloud server, the bot operates entirely on its own:

1. **09:00 AM**: Runs the full NSE scan (1800+ stocks) and isolates the top 50 picks for the day based on momentum and volatility.
2. **09:15 AM**: Market opens. Subscribes to live WebSocket feeds for the 50 picks. Collects data for the Opening Range Breakout strategy.
3. **09:30 AM**: All strategies become fully active. The main trading loop runs every 3 seconds, evaluating indicators and ensemble signals.
4. **02:30 PM**: Stops accepting *new* trade entries to prevent end-of-day volatility traps. Continues monitoring open positions for exit signals.
5. **03:10 PM**: **Auto Square-Off**. Closes all remaining open positions to prevent the broker from force-closing them at market price.
6. **03:30 PM**: Generates the End-of-Day report, saves it to the SQLite database, and goes to sleep until the next trading day.

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! 
Please check the [Contributing Guidelines](CONTRIBUTING.md) for details on how to get involved.

---

## 📜 License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.
