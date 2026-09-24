#!/usr/bin/env bash
# =============================================================================
# deploy/setup.sh — One-shot setup for a fresh Ubuntu 24.04 cloud VM
# (AWS EC2 t3.micro / t4g.micro, Oracle Always Free, etc. — 1 GB RAM is enough)
#
# Run this once on your server, from inside the uploaded groww-bot folder:
#   chmod +x deploy/setup.sh
#   ./deploy/setup.sh
# =============================================================================
set -e

BOT_DIR="$HOME/groww-bot"

echo "============================================"
echo " Groww Sentiment Bot — cloud setup"
echo "============================================"

# --- 1. System packages ---
echo "[1/7] Installing system dependencies..."
sudo apt-get update -q
sudo apt-get install -yq python3 python3-venv python3-pip curl

# --- 2. Swap: FinBERT + PyTorch need ~1 GB, more than a 1 GB VM has free ---
if ! swapon --show | grep -q /swapfile; then
  echo "[2/7] Adding 2 GB swap..."
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
else
  echo "[2/7] Swap already present — skipping"
fi

# --- 3. Copy bot code (skip if already running from $BOT_DIR) ---
echo "[3/7] Copying bot files to $BOT_DIR..."
mkdir -p "$BOT_DIR"
if [ "$(pwd)" != "$BOT_DIR" ]; then
  cp -r ./* "$BOT_DIR/"
fi

# --- 4. Python virtual environment + dependencies ---
echo "[4/7] Installing Python packages (5–10 minutes)..."
python3 -m venv "$BOT_DIR/venv"
"$BOT_DIR/venv/bin/pip" install --quiet --upgrade pip
# CPU-only PyTorch: the default Linux wheel pulls ~2.5 GB of CUDA libraries a small VM can't hold
"$BOT_DIR/venv/bin/pip" install --quiet torch --index-url https://download.pytorch.org/whl/cpu
"$BOT_DIR/venv/bin/pip" install --quiet -r "$BOT_DIR/requirements.txt"

# --- 5. Pre-download FinBERT model ---
echo "[5/7] Pre-downloading FinBERT model weights (~440 MB)..."
"$BOT_DIR/venv/bin/python" -c "
from transformers import AutoTokenizer, AutoModelForSequenceClassification
AutoTokenizer.from_pretrained('ProsusAI/finbert')
AutoModelForSequenceClassification.from_pretrained('ProsusAI/finbert')
print('FinBERT model downloaded successfully.')
"

# --- 6. Smoke test: modules import and the NSE symbol list loads ---
echo "[6/7] Checking the bot starts..."
(cd "$BOT_DIR" && TZ=Asia/Kolkata "$BOT_DIR/venv/bin/python" -c "import bot; print('Bot imports OK')")

# --- 7. systemd service (auto-restart, survives reboots) ---
# TZ=Asia/Kolkata: the 09:15 / 15:30 schedule and trade timestamps use local time.
echo "[7/7] Registering systemd service..."
sudo tee /etc/systemd/system/groww-bot.service > /dev/null <<EOF
[Unit]
Description=Groww News Sentiment Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BOT_DIR
Environment=TZ=Asia/Kolkata
Environment=PYTHONUNBUFFERED=1
ExecStart=$BOT_DIR/venv/bin/python $BOT_DIR/bot.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable groww-bot

echo ""
echo "============================================"
echo " Setup complete!"
echo "============================================"
echo ""
echo "DRY_RUN (paper trading) needs no Groww credentials."
echo "  Start:        sudo systemctl start groww-bot"
echo "  Live logs:    sudo journalctl -fu groww-bot"
echo "  P&L report:   cd $BOT_DIR && TZ=Asia/Kolkata venv/bin/python report.py"
