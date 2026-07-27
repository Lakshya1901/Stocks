#!/bin/bash

# ==========================================
# Stocks Bot - EC2 Deployment Script
# ==========================================
# Run this script on your EC2 instance to instantly
# pull the latest code and update dependencies.

echo "🚀 Starting Deployment Process..."

# 1. Stash any local accidental changes on EC2
echo "📦 Stashing any local changes on EC2..."
git stash

# 2. Pull the latest code from GitHub
echo "📥 Pulling latest code from origin/main..."
git pull origin main

# 3. Update Python dependencies (We removed yfinance!)
echo "🐍 Updating Python dependencies..."
pip3 install -r requirements.txt

echo "✅ Deployment successful!"
echo ""
echo "⚠️  IMPORTANT: Don't forget to restart your bot so the new code takes effect!"
echo "If you use screen/tmux: Stop the script and run 'python3 -m src.main' again."
echo "If you use systemd: Run 'sudo systemctl restart stocksbot'"
