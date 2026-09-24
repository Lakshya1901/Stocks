#!/bin/bash
# run.sh — always uses the correct Python 3.11 interpreter
# Usage: bash run.sh   OR   ./run.sh   (after chmod +x run.sh)

cd "$(dirname "$0")"
/usr/local/bin/python3 bot.py
