# Day Trader Bot — Groww API

Automated intraday trading bot that scans the entire NSE universe (1800+ stocks), selects the best daily candidates, and executes trades using three complementary strategies — all monitored via a real-time dark-themed dashboard.

## Features

- **Full NSE Scanner** — Scans 1800+ stocks every morning, picks top 15 by composite momentum/volatility/volume score
- **3 Trading Strategies** — VWAP Mean Reversion, Momentum Breakout, Opening Range Breakout
- **Ensemble Signal Aggregator** — Weighted voting across strategies with conflict detection
- **Risk Management** — Per-trade stop-loss, trailing stops, daily loss limits, sector exposure caps
- **TOTP Authentication** — Automated daily login using TOTP (no manual API key refresh)
- **Paper Trading Mode** — Simulate trades against live prices before going live
- **Real-Time Dashboard** — Dark glassmorphism UI with live P&L, positions, signals, and controls
- **SQLite Persistence** — All trades, signals, and daily summaries persisted for analysis

## Quick Start

### 1. Clone & Install

```bash
cd /path/to/Stocks
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Credentials

```bash
cp .env.example .env
```

Edit `.env` and add your Groww TOTP credentials:
```
GROWW_TOTP_TOKEN=your_totp_token
GROWW_TOTP_SECRET=your_totp_secret
```

To get these:
1. Log into [groww.in](https://groww.in)
2. Go to **Profile → Settings → Trading APIs**
3. Click dropdown next to "Generate API Key" → **Generate TOTP Token**
4. Copy the TOTP Token and TOTP Secret

### 3. Configure Settings

Edit `config.yaml` to adjust:
- **Capital** — Total capital allocation
- **Risk parameters** — Stop-loss %, take-profit %, daily loss limit
- **Scanner settings** — Volume thresholds, price range, number of picks
- **Strategy weights** — How much each strategy influences the final signal

### 4. Run

```bash
# Paper trading (default)
python -m src.main

# Dashboard will be available at http://localhost:8080
```

### 5. Cloud Deployment (Static IP)

See [setup_cloud.md](setup_cloud.md) for Oracle Cloud / AWS free-tier setup with static IP for Groww API whitelisting.

## Architecture

```
09:00  → Full NSE scan: 1800+ stocks → top 15
09:15  → Subscribe to live feeds, start ORB data collection
09:30  → All strategies active — main trading loop
09:30-14:30 → Trade loop: tick → indicators → ensemble → risk → order
14:30  → Stop new entries
15:10  → Square off all positions
15:30  → Daily report, persist to DB, sleep until next trading day
```

## Trading Strategies

| Strategy | Type | Best For | Weight |
|----------|------|----------|--------|
| VWAP Reversion | Mean Reversion | Range-bound markets | 35% |
| Momentum Breakout | Trend Following | Trending markets | 35% |
| Opening Range Breakout | Breakout | First 15-min range | 30% |

## Risk Rules

- Max 1% capital risk per trade
- 2:1 reward-to-risk ratio (2% TP, 1% SL)
- Trailing stop activates at 1% profit
- Daily loss limit: 2% of capital
- Max 6 simultaneous positions
- Max 3 positions per sector
- No new entries after 2:30 PM
- Force square-off at 3:10 PM

## Dashboard

Access at `http://localhost:8080` (or `http://<cloud-ip>:8080`)

- **P&L Timeline** — Live chart tracking cumulative P&L
- **Open Positions** — Real-time table with entry, current price, P&L, stop-loss
- **Trade History** — Completed trades with realized P&L
- **Scanner Results** — Today's stock picks with scores
- **Signal Feed** — Live strategy signals with confidence scores
- **Risk Status** — Progress bars for loss limit, position slots, capital usage
- **Emergency Square-off** — One-click button to close all positions

## Project Structure

```
Stocks/
├── config.yaml            # Configuration
├── .env                   # API credentials (gitignored)
├── requirements.txt       # Dependencies
├── setup_cloud.md         # Cloud deployment guide
├── src/
│   ├── main.py            # Orchestrator
│   ├── data/              # Instruments, market feed, historical data
│   ├── scanner/           # Full NSE stock scanner
│   ├── strategies/        # VWAP, Momentum, ORB, Ensemble
│   ├── risk/              # Position sizing & risk management
│   ├── execution/         # Order manager & portfolio tracker
│   ├── storage/           # SQLite DB & logging
│   └── dashboard/         # Flask + WebSocket UI
├── data/                  # Instrument cache & trades DB
└── logs/                  # Rotating log files
```

## Going Live

1. Run in paper mode for 3-5 full sessions
2. Verify scanner picks, signal quality, and risk limits
3. Set up cloud instance with static IP (see `setup_cloud.md`)
4. Whitelist the IP in Groww
5. Change `trading.mode` to `live` in `config.yaml`
6. Start with minimum quantities (1 share) for 2-3 sessions
7. Gradually increase capital allocation
