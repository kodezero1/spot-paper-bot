#!/usr/bin/env python3
"""
HyperLiquid Paper Spot Trading Bot - FIXED VERSION

Tracks XRP, HYPE, SOL, kPEPE, DOGE, GRASS pricing from HyperLiquid
- Uses OHLCV candles for proper momentum calculation like crypto_polymarket_bot
- Buys $100 worth of each token when 24hr trend is up and 30min momentum is positive
- Sets 2% trailing stop when position is 2% profitable
- Saves all trades to JSON
"""

import requests
import json
import time
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# Configuration
TOKENS = ['XRP', 'HYPE', 'SOL', 'kPEPE', 'DOGE', 'GRASS']  # Using kPEPE as PEPE alternative
POSITION_SIZE = 100.0  # $100 per token
TRAILING_STOP_ACTIVATION = 0.02  # 2% profit to activate trailing stop
TRAILING_STOP_DISTANCE = 0.02  # 2% trailing distance
CHECK_INTERVAL = 30  # 30 seconds for faster testing
TRADES_FILE = os.path.join(os.path.dirname(__file__), 'trades.json')

# Global state
paper_balance = 10000.0
positions = {}
trades_history = []

def get_hyperliquid_candles(token: str, timeframe: str = '1m', limit: int = 1500) -> Optional[List]:
    """Fetch OHLCV candles from HyperLiquid for momentum calculation"""
    try:
        end_time = int(time.time() * 1000)
        # Start time to get enough data for 24hr + 30min analysis
        start_time = end_time - (25 * 60 * 60 * 1000)  # 25 hours of data

        response = requests.post('https://api.hyperliquid.xyz/info',
                               json={
                                   'type': 'candleSnapshot',
                                   'req': {
                                       'coin': token,
                                       'interval': timeframe,
                                       'startTime': start_time,
                                       'endTime': end_time,
                                       'limit': limit
                                   }
                               },
                               timeout=10)

        if response.status_code != 200:
            print(f"Error fetching {token} candles: {response.status_code}")
            return None

        candles = response.json()
        if not candles:
            return None

        # Convert to standard OHLCV format: [timestamp, open, high, low, close, volume]
        return [[candle['t'], float(candle['o']), float(candle['h']),
                float(candle['l']), float(candle['c']), float(candle['v'])] for candle in candles]

    except Exception as e:
        print(f"Error fetching {token} candles: {e}")
        return None

def calculate_momentum_from_candles(token: str) -> Dict[str, float]:
    """Calculate 24hr and 30min momentum from OHLCV candles (proper way)"""
    candles = get_hyperliquid_candles(token)

    if not candles or len(candles) < 30:  # Need at least 30 minutes of data
        return {'24hr_change': 0.0, '30min_change': 0.0, 'can_trade': False, 'error': 'Insufficient data'}

    current_price = candles[-1][4]  # Close price of latest candle
    price_changes = {}

    # Calculate timeframe changes EXACTLY like crypto_polymarket_bot
    timeframes = {
        '30m': 30,   # 30 minutes ago
        '24h': 24 * 60  # 1440 minutes ago (24 hours)
    }

    for timeframe, minutes_back in timeframes.items():
        if len(candles) >= minutes_back:
            past_price = candles[-minutes_back][4]  # Close price from timeframe ago
            if past_price > 0:
                change_percent = ((current_price - past_price) / past_price) * 100  # MULTIPLY BY 100!
                price_changes[timeframe] = change_percent
            else:
                price_changes[timeframe] = 0.0
        else:
            price_changes[timeframe] = 0.0

    # Extract 24hr and 30min changes
    change_24hr = price_changes.get('24h', 0.0)
    change_30min = price_changes.get('30m', 0.0)

    # Can trade if BOTH are positive (momentum building)
    can_trade = change_24hr > 0 and change_30min > 0

    return {
        '24hr_change': change_24hr,
        '30min_change': change_30min,
        'can_trade': can_trade,
        'current_price': current_price,
        'candles_available': len(candles)
    }

def get_current_price(token: str) -> Optional[float]:
    """Get current price for a single token"""
    try:
        response = requests.post('https://api.hyperliquid.xyz/info',
                               json={'type': 'allMids'},
                               timeout=10)

        if response.status_code == 200:
            data = response.json()
            return float(data.get(token, 0))

    except Exception as e:
        print(f"Error fetching price for {token}: {e}")

    return None

def execute_buy(token: str, price: float) -> bool:
    """Execute paper buy order"""
    global paper_balance

    if paper_balance < POSITION_SIZE:
        print(f"Insufficient balance for {token}: ${paper_balance:.2f}")
        return False

    quantity = POSITION_SIZE / price

    positions[token] = {
        'quantity': quantity,
        'entry_price': price,
        'entry_time': datetime.now().isoformat(),
        'highest_price': price,
        'trailing_stop_active': False,
        'trailing_stop_price': None
    }

    paper_balance -= POSITION_SIZE

    trade = {
        'timestamp': datetime.now().isoformat(),
        'token': token,
        'action': 'BUY',
        'quantity': quantity,
        'price': price,
        'value': POSITION_SIZE
    }

    trades_history.append(trade)
    save_trades()

    print(f"✅ BOUGHT {quantity:.4f} {token} at ${price:.4f} for ${POSITION_SIZE}")
    return True

def execute_sell(token: str, price: float, reason: str) -> bool:
    """Execute paper sell order"""
    global paper_balance

    if token not in positions:
        return False

    position = positions[token]
    quantity = position['quantity']
    entry_price = position['entry_price']
    sale_value = quantity * price
    pnl = sale_value - POSITION_SIZE
    pnl_pct = (pnl / POSITION_SIZE) * 100

    paper_balance += sale_value

    trade = {
        'timestamp': datetime.now().isoformat(),
        'token': token,
        'action': 'SELL',
        'quantity': quantity,
        'price': price,
        'value': sale_value,
        'pnl': pnl,
        'pnl_percent': pnl_pct,
        'reason': reason,
        'entry_price': entry_price,
        'hold_time_hours': (datetime.now() - datetime.fromisoformat(position['entry_time'])).total_seconds() / 3600
    }

    trades_history.append(trade)
    del positions[token]
    save_trades()

    pnl_emoji = "📈" if pnl > 0 else "📉"
    print(f"✅ SOLD {quantity:.4f} {token} at ${price:.4f} for ${sale_value:.2f} | {pnl_emoji} PnL: ${pnl:.2f} ({pnl_pct:.1f}%) | Reason: {reason}")
    return True

def update_trailing_stops():
    """Update trailing stops for profitable positions"""
    for token in list(positions.keys()):
        current_price = get_current_price(token)
        if not current_price:
            continue

        position = positions[token]
        entry_price = position['entry_price']

        # Calculate current profit
        current_value = position['quantity'] * current_price
        profit_pct = (current_value - POSITION_SIZE) / POSITION_SIZE

        # Activate trailing stop if 2% profitable
        if profit_pct >= TRAILING_STOP_ACTIVATION and not position['trailing_stop_active']:
            position['trailing_stop_active'] = True
            position['trailing_stop_price'] = current_price * (1 - TRAILING_STOP_DISTANCE)
            print(f"🛡️ Trailing stop activated for {token} at ${position['trailing_stop_price']:.4f}")

        # Update trailing stop price if price goes higher
        if position['trailing_stop_active']:
            if current_price > position['highest_price']:
                position['highest_price'] = current_price
                new_stop_price = current_price * (1 - TRAILING_STOP_DISTANCE)
                if new_stop_price > position['trailing_stop_price']:
                    position['trailing_stop_price'] = new_stop_price
                    print(f"📈 {token} trailing stop updated to ${new_stop_price:.4f}")

            # Check if trailing stop triggered
            if current_price <= position['trailing_stop_price']:
                execute_sell(token, current_price, "Trailing Stop")

def save_trades():
    """Save trades to JSON file"""
    data = {
        'paper_balance': paper_balance,
        'positions': positions,
        'trades_history': trades_history,
        'last_updated': datetime.now().isoformat()
    }

    try:
        with open(TRADES_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving trades: {e}")

def load_trades():
    """Load trades from JSON file"""
    global paper_balance, positions, trades_history

    try:
        with open(TRADES_FILE, 'r') as f:
            data = json.load(f)

        paper_balance = data.get('paper_balance', 10000.0)
        positions = data.get('positions', {})
        trades_history = data.get('trades_history', [])

        print(f"📊 Loaded state: Balance ${paper_balance:.2f}, {len(positions)} positions, {len(trades_history)} trades")

    except FileNotFoundError:
        print(f"📊 Starting fresh: Balance ${paper_balance:.2f}")
    except Exception as e:
        print(f"Error loading trades: {e}")

def print_status():
    """Print current bot status"""
    total_position_value = 0
    for token, pos in positions.items():
        current_price = get_current_price(token)
        if current_price:
            total_position_value += pos['quantity'] * current_price

    total_portfolio = paper_balance + total_position_value

    print(f"\n{'='*80}")
    print(f"🤖 HYPERLIQUID PAPER SPOT BOT")
    print(f"{'='*80}")
    print(f"💰 Portfolio: ${total_portfolio:.2f} | Cash: ${paper_balance:.2f} | Positions: ${total_position_value:.2f}")
    print(f"📊 Open Positions: {len(positions)}/{len(TOKENS)}")
    print(f"📈 Total Trades: {len(trades_history)}")

    if positions:
        print(f"\n🎯 Active Positions:")
        for token, pos in positions.items():
            current_price = get_current_price(token)
            if current_price:
                current_value = pos['quantity'] * current_price
                pnl = current_value - POSITION_SIZE
                pnl_pct = (pnl / POSITION_SIZE) * 100
                pnl_emoji = "📈" if pnl > 0 else "📉"
                stop_status = "🛡️" if pos['trailing_stop_active'] else ""
                print(f"  {token}: {pos['quantity']:.4f} @ ${pos['entry_price']:.4f} → ${current_price:.4f} | {pnl_emoji} {pnl_pct:+.1f}% {stop_status}")

def main():
    """Main bot loop"""
    print("🚀 Starting HyperLiquid Paper Spot Bot (FIXED VERSION)")
    print(f"🎯 Tokens: {', '.join(TOKENS)}")
    print(f"💰 Position Size: ${POSITION_SIZE} each")
    print(f"🛡️ Trailing Stop: {TRAILING_STOP_ACTIVATION*100}% activation, {TRAILING_STOP_DISTANCE*100}% distance")

    load_trades()

    while True:
        try:
            # Update trailing stops first
            update_trailing_stops()

            # Check for new buy opportunities
            print(f"\n📈 Momentum Analysis (using OHLCV candles):")
            for token in TOKENS:
                if token in positions:
                    print(f"  {token}: Already in position")
                    continue

                momentum = calculate_momentum_from_candles(token)

                if 'error' in momentum:
                    print(f"  {token}: {momentum['error']}")
                    continue

                # Show momentum data for all tokens with proper percentages (already multiplied by 100)
                status_emoji = "🟢" if momentum['can_trade'] else "🔴"
                print(f"  {token}: {status_emoji} 24h {momentum['24hr_change']:+.1f}%, 30m {momentum['30min_change']:+.1f}% ({momentum['candles_available']} candles)")

                if momentum['can_trade']:
                    print(f"🎯 {token} BUYING: Both momentum indicators positive!")
                    execute_buy(token, momentum['current_price'])

            # Print status
            print_status()

            print(f"\n⏳ Waiting {CHECK_INTERVAL} seconds...")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Error in main loop: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()