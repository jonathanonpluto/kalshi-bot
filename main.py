import os
import time
import tempfile
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
import requests
from pykalshi import KalshiClient, MarketStatus

load_dotenv()

# ===================== CONFIG =====================
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_RAW = os.getenv("KALSHI_PRIVATE_KEY", "").strip()
KALSHI_PRIVATE_KEY = KALSHI_PRIVATE_KEY_RAW.replace("\\n", "\n").replace("\r", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
XAI_API_KEY = os.getenv("XAI_API_KEY")
EDGE_THRESHOLD = float(os.getenv("EDGE_THRESHOLD", 0.06))
TRADE_SIZE = int(os.getenv("TRADE_SIZE_DOLLARS", 20))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", 300))

print("✅ Private key loaded — length after fix:", len(KALSHI_PRIVATE_KEY))

with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
    f.write(KALSHI_PRIVATE_KEY)
    PRIVATE_KEY_PATH = f.name

# LIVE client - NO 'host' argument (library defaults to production)
kalshi = KalshiClient(api_key_id=KALSHI_API_KEY_ID, private_key_path=PRIVATE_KEY_PATH)

def send_telegram(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"})

def get_grok_probability(market):
    if not XAI_API_KEY:
        return 0.5
    try:
        prompt = f"""You are a world-class prediction-market trader. Estimate the TRUE probability (0.0–1.0) that YES resolves for this Kalshi market:
Title: {market.get('title', '')}
Subtitle: {market.get('subtitle', '')}
Current yes price: {market.get('yes_price', 50)} cents
Recent context: {market.get('description', '')}
Output ONLY a number between 0 and 1."""
        response = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {XAI_API_KEY}"},
            json={"model": "grok-beta", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
        )
        return float(response.json()["choices"][0]["message"]["content"].strip())
    except:
        return 0.5

def get_estimated_prob(market):
    try:
        orderbook = kalshi.get_order_book(market["ticker"])
        yes_price = orderbook.get("yes_price", 50) / 100.0
        volume = market.get("volume", 0)
        base_prob = yes_price
        if any(k in market.get("title", "").lower() for k in ["election", "weather", "sport", "crypto", "fed"]):
            grok_prob = get_grok_probability(market)
            base_prob = (base_prob + grok_prob) / 2
        if volume < 5000:
            base_prob = base_prob * 0.95
        return base_prob
    except:
        return 0.5

def scan_and_trade():
    send_telegram(f"🔄 Bot scan started at {datetime.now().strftime('%H:%M')} — looking for edges...")
    markets = kalshi.get_markets(status=MarketStatus.OPEN, limit=200)
    market_list = markets.to_dataframe().to_dict(orient="records")
    
    trades_today = 0
    for m in market_list:
        if m.get("volume_24h", 0) < 5000:
            continue
        if trades_today >= 12:
            break
            
        est_prob = get_estimated_prob(m)
        market_prob = m.get("yes_price", 50) / 100.0
        edge = est_prob - market_prob
        
        if abs(edge) > EDGE_THRESHOLD:
            side = "yes" if edge > 0 else "no"
            ticker = m["ticker"]
            
            message = f"🚀 <b>TRADE SIGNAL</b>\nMarket: {m['title']}\nEdge: {edge:.1%}\nEst prob: {est_prob:.1%}\nMarket prob: {market_prob:.1%}\nAction: BUY {side.upper()} @ ~${m.get('yes_price', '?')}"
            
            try:
                kalshi.place_order(ticker=ticker, side=side, count=TRADE_SIZE, type="market")
                send_telegram(message + f"\n✅ REAL TRADE executed: {TRADE_SIZE} contracts")
            except Exception as e:
                send_telegram(f"⚠️ Order failed: {str(e)}")
            
            trades_today += 1
            time.sleep(2)

    send_telegram(f"✅ Scan complete. {trades_today} signals today. Next check in 5 min.")

# ===================== SCHEDULER =====================
scheduler = BlockingScheduler()
scheduler.add_job(scan_and_trade, "interval", seconds=CHECK_INTERVAL, next_run_time=datetime.now())
send_telegram("🚀 <b>Kalshi Grok Bot STARTED</b> — LIVE trading ON with your real funds. Several trades/day expected.")
scheduler.start()
