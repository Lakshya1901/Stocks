# Groww News Sentiment Trading Bot

A fully automated trading bot that reads live financial news, uses AI to score sentiment, and executes trades on your Groww account.

## Project Structure

```
groww-bot/
├── config.py      All settings + shared logging — edit this file only
├── bot.py         Main loop + live dashboard — entry point
├── news.py        RSS feeds, headline → NSE symbol matching, FinBERT sentiment
├── market.py      Yahoo Finance prices/volume + technical indicators (RSI, MACD, Bollinger)
├── risk.py        Position sizing, SL/TP, Groww fees, market hours & NSE holidays
├── broker.py      Groww API (auth, cash, holdings, orders) + trade journal
├── positions.py   Open positions: SL/TP exits, holdings, protective sells
├── report.py      Net P&L report (python3 report.py)
├── requirements.txt
└── deploy/
    └── setup.sh   One-shot cloud VM setup (Ubuntu 24.04)
```

## Quick Start

### Step 1 — Get your Groww API credentials
Go to [groww.in/trade-api/api-keys](https://groww.in/trade-api/api-keys), log in, and generate an API Key and Secret.

### Step 2 — Create your `.env` file
Create a file called `.env` in the `groww-bot/` folder:
```
GROWW_API_KEY=your_api_key_here
GROWW_TOTP_SECRET=your_totp_secret_here
```
Never commit this file to Git.

### Step 3 — Run locally (for testing)
```bash
pip install -r requirements.txt
python bot.py
```

### Step 4 — Deploy to AWS EC2
1. Launch an Ubuntu 22.04 t2.micro instance (free tier eligible) in the **Mumbai (ap-south-1)** region.
2. Copy the `groww-bot/` folder to the server:
   ```bash
   scp -r groww-bot/ ubuntu@YOUR_SERVER_IP:~/
   ```
3. SSH in and run the setup script:
   ```bash
   ssh ubuntu@YOUR_SERVER_IP
   cd ~/groww-bot
   chmod +x deploy/setup.sh
   ./deploy/setup.sh
   ```
4. Edit the `.env` file on the server with your credentials.
5. Start the bot:
   ```bash
   sudo systemctl start groww-bot
   sudo journalctl -fu groww-bot   # watch live logs
   ```

## Important: Test in Dry Run First
`config.py` has `DRY_RUN = True` by default. In this mode the bot logs every trade it would make but places no real orders. Run it for at least one full trading day and verify the log output looks sensible before switching to live.

To go live, open `config.py` and change:
```python
DRY_RUN = False
```
Then restart the bot.

## Key Configuration Options (`config.py`)

| Setting | Default | Description |
|---|---|---|
| `DRY_RUN` | `True` | Set to `False` to place real orders |
| `TRADE_ACCOUNT_PCT` | `0.10` | Uses 10% of total account balance per trade |
| `SENTIMENT_THRESHOLD` | `0.85` | Min FinBERT confidence score to act on news |
| `TAKE_PROFIT_PCT` | `0.04` | Exit if up 4% |
| `SENTIMENT_THRESHOLD` | `0.85` | Min AI confidence to act |
| `MIN_DAILY_VOLUME` | `50000` | Liquidity guard (0 to disable) |
| `POLL_INTERVAL_SECONDS` | `30` | How often to check for news |
