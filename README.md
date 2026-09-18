# FX + BTC Value Bot

Telegram signal bot for BTC/USD, EUR/USD, GBP/USD and XAU/USD.

## Strategy
- 1H trend context: EMA 20/50
- 15M entry confirmation
- RSI momentum filter
- ATR-based stop
- Default target: 1:2 risk/reward
- Default risk setting: 0.5% (used for signal display; the bot does NOT place trades)

## Render environment variables
TELEGRAM_BOT_TOKEN = your Telegram bot token
TWELVE_DATA_API_KEY = your Twelve Data API key
RISK_PERCENT = 0.5
RR = 2
SCAN_MINUTES = 15

## Render
Root Directory: leave empty
Build Command: pip install -r requirements.txt
Start Command: python bot.py

## Telegram
/start
/scan
/status
/enable
/disable

This is a research/signal system, not a guaranteed-profit system. Backtest and paper trade before using real money.
