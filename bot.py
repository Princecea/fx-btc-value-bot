
import os
import asyncio
import math
import requests
import pandas as pd
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.5"))
RR = float(os.getenv("RR", "2.0"))
SCAN_MINUTES = int(os.getenv("SCAN_MINUTES", "15"))

# Start small. Add more symbols only after testing.
SYMBOLS = {
    "BTC/USD": "crypto",
    "EUR/USD": "twelve",
    "GBP/USD": "twelve",
    "XAU/USD": "twelve",
}

last_signals = {}

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    gain = d.clip(lower=0).rolling(n).mean()
    loss = (-d.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))

def atr(df, n=14):
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def get_binance(symbol="BTCUSDT", interval="15m", limit=250):
    url = "https://api.binance.com/api/v3/klines"
    r = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    return pd.DataFrame(rows, columns=[
        "time","open","high","low","close","volume","close_time",
        "quote_volume","trades","buy_volume","buy_quote_volume","ignore"
    ]).assign(
        open=lambda x: x.open.astype(float),
        high=lambda x: x.high.astype(float),
        low=lambda x: x.low.astype(float),
        close=lambda x: x.close.astype(float),
        volume=lambda x: x.volume.astype(float)
    )

def get_twelve(symbol, interval, limit=250):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY is missing")
    url = "https://api.twelvedata.com/time_series"
    r = requests.get(url, params={
        "symbol": symbol,
        "interval": interval,
        "outputsize": limit,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON"
    }, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "values" not in data:
        raise RuntimeError(data.get("message", "No market data returned"))
    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "volume" in df:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    return df.sort_values("datetime").reset_index(drop=True)

def fetch(symbol, source, interval):
    if source == "crypto":
        return get_binance("BTCUSDT", interval)
    return get_twelve(symbol, interval)

def analyze(symbol, source):
    h1 = fetch(symbol, source, "1h")
    m15 = fetch(symbol, source, "15m")

    # Ignore the currently forming candle.
    h1 = h1.iloc[:-1].copy()
    m15 = m15.iloc[:-1].copy()

    if len(h1) < 60 or len(m15) < 60:
        return None

    h1["ema20"] = ema(h1.close, 20)
    h1["ema50"] = ema(h1.close, 50)
    m15["ema20"] = ema(m15.close, 20)
    m15["ema50"] = ema(m15.close, 50)
    m15["rsi"] = rsi(m15.close)
    m15["atr"] = atr(m15)

    H = h1.iloc[-1]
    c = m15.iloc[-1]
    prev = m15.iloc[-2]

    trend_up = H.close > H.ema20 > H.ema50
    trend_down = H.close < H.ema20 < H.ema50

    # Confirmation: 15M momentum agrees with 1H trend.
    long_ok = trend_up and c.close > c.ema20 and c.rsi >= 52 and c.close > prev.high
    short_ok = trend_down and c.close < c.ema20 and c.rsi <= 48 and c.close < prev.low

    if not (long_ok or short_ok):
        return None

    direction = "BUY" if long_ok else "SELL"
    entry = float(c.close)
    a = float(c.atr)
    if not math.isfinite(a) or a <= 0:
        return None

    # Volatility-aware stop, then fixed 1:RR target.
    sl_distance = 1.2 * a
    if direction == "BUY":
        sl = entry - sl_distance
        tp = entry + RR * sl_distance
    else:
        sl = entry + sl_distance
        tp = entry - RR * sl_distance

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": RR,
        "risk": RISK_PERCENT,
        "rsi": float(c.rsi),
        "atr": a,
        "time": str(c.get("datetime", c.get("time", ""))),
    }

def fmt_price(x):
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 10:
        return f"{x:.4f}"
    return f"{x:.5f}"

def format_signal(s):
    return (
        f"📊 <b>{s['symbol']}</b> — <b>{s['direction']}</b>\n\n"
        f"Entry: <b>{fmt_price(s['entry'])}</b>\n"
        f"Stop Loss: <b>{fmt_price(s['sl'])}</b>\n"
        f"Take Profit: <b>{fmt_price(s['tp'])}</b>\n"
        f"Risk: <b>{s['risk']:.2f}%</b>\n"
        f"R:R: <b>1:{s['rr']:.0f}</b>\n"
        f"15M RSI: {s['rsi']:.1f}\n\n"
        "⚠️ Signal only. No profit is guaranteed."
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 FX + BTC Value Bot is online.\n\n"
        "Commands:\n"
        "/scan — scan all configured markets\n"
        "/status — show bot settings"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Markets: {', '.join(SYMBOLS)}\n"
        f"Risk per setup: {RISK_PERCENT:.2f}%\n"
        f"Target: 1:{RR:.0f}\n"
        "Timeframes: 1H context + 15M entry"
    )

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    messages = []
    for symbol, source in SYMBOLS.items():
        try:
            s = await asyncio.to_thread(analyze, symbol, source)
            if s:
                messages.append(format_signal(s))
        except Exception as e:
            messages.append(f"⚠️ {symbol}: data error — {str(e)[:120]}")
    if not messages:
        await update.message.reply_text(
            "No setup currently meets the rules. I will not force a trade."
        )
    else:
        await update.message.reply_text("\n\n".join(messages), parse_mode="HTML")

async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    messages = []
    for symbol, source in SYMBOLS.items():
        try:
            s = await asyncio.to_thread(analyze, symbol, source)
            if not s:
                continue
            key = f"{symbol}:{s['direction']}:{round(s['entry'], 5)}"
            if last_signals.get(symbol) == key:
                continue
            last_signals[symbol] = key
            messages.append(format_signal(s))
        except Exception:
            continue
    if messages:
        await context.bot.send_message(chat_id=chat_id, text="\n\n".join(messages), parse_mode="HTML")

async def enable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.job_queue is None:
        await update.message.reply_text("Job queue is not available.")
        return
    chat_id = update.effective_chat.id
    # Avoid duplicate jobs for the same chat.
    for job in context.job_queue.get_jobs_by_name(f"scan:{chat_id}"):
        job.schedule_removal()
    context.job_queue.run_repeating(
        scheduled_scan,
        interval=SCAN_MINUTES * 60,
        first=5,
        data=chat_id,
        name=f"scan:{chat_id}"
    )
    await update.message.reply_text(
        f"✅ Automatic scanning enabled every {SCAN_MINUTES} minutes."
    )

async def disable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.job_queue:
        for job in context.job_queue.get_jobs_by_name(f"scan:{update.effective_chat.id}"):
            job.schedule_removal()
    await update.message.reply_text("🛑 Automatic scanning disabled.")

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("enable", enable))
    app.add_handler(CommandHandler("disable", disable))
    app.run_polling()

if __name__ == "__main__":
    main()
