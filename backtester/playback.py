import time
import threading
import queue
import datetime
import pandas as pd
import numpy as np
import pytz
from engine import (
    download_nifty_spot_data,
    black_scholes,
    get_time_to_expiry_years,
    round_to_nearest_strike,
    get_trailed_sl
)

class PlaybackManager:
    def __init__(self):
        self.status = "STOPPED"  # RUNNING, PAUSED, STOPPED
        self.speed = 1.0        # multiplier: 1x, 5x, 10x, 60x (bars per second)
        self.date_str = ""      # Selected playback date YYYY-MM-DD
        self.symbol = "^NSEI"
        
        self.df = None          # 1m data for the day
        self.current_idx = 0
        
        self.thread = None
        self.lock = threading.Lock()
        self.clients = []       # List of queues to stream to connected clients
        
        # State variables
        self.capital = 200000.0
        self.realized_pnl = 0.0
        self.active_trades = []
        
        # Strategy parameters
        self.strike_offset_pe = 100
        self.strike_offset_ce = -100
        self.entry_discount = 0.10
        self.lot_1_target_pct = 0.40
        self.max_reentries = 5
        self.lot_size = 75
        self.iv = 0.15
        self.r = 0.07
        self.session_id = 0

    def register_client(self):
        q = queue.Queue(maxsize=100)
        self.clients.append(q)
        return q

    def unregister_client(self, q):
        if q in self.clients:
            self.clients.remove(q)

    def broadcast(self, data):
        for q in list(self.clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass

    def start(self, symbol, date_str, speed, capital, strategy_type, strike_offset_pe, strike_offset_ce, entry_discount, lot_1_target, max_reentries, lot_size, iv, r):
        with self.lock:
            if self.status != "STOPPED":
                # Stop existing first
                self._stop_internal()
                
            self.symbol = symbol
            self.date_str = date_str
            self.speed = float(speed)
            self.capital = float(capital)
            self.realized_pnl = 0.0
            self.active_trades = []
            
            self.strategy_type = strategy_type
            self.strike_offset_pe = int(strike_offset_pe)
            self.strike_offset_ce = int(strike_offset_ce)
            self.entry_discount = float(entry_discount) / 100.0
            self.lot_1_target_pct = float(lot_1_target) / 100.0
            self.max_reentries = int(max_reentries)
            self.lot_size = int(lot_size)
            self.iv = float(iv) / 100.0
            self.r = float(r) / 100.0
            
            # Fetch data for the specified date
            # To get enough historical context, download 7d and filter for the day
            full_df = download_nifty_spot_data(self.symbol, period="7d")
            
            # Filter for date_str
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            full_df['DateOnly'] = full_df['Datetime'].dt.date
            self.df = full_df[full_df['DateOnly'] == target_date].sort_values('Datetime').reset_index(drop=True)
            
            if self.df.empty:
                # If yfinance has no data (e.g. weekend or outside 7d), generate synthetic data for that date
                from engine import generate_synthetic_spot_data
                print(f"No real data found for {date_str}. Generating synthetic day...")
                synthetic_df = generate_synthetic_spot_data(days=1)
                # Force datetimes to target_date
                synthetic_df['DateOnly'] = target_date
                # Re-adjust Datetime timestamps to be target_date
                adjusted_rows = []
                for _, row in synthetic_df.iterrows():
                    dt = row['Datetime'].astimezone(pytz.timezone('Asia/Kolkata'))
                    new_dt = dt.replace(year=target_date.year, month=target_date.month, day=target_date.day)
                    row['Datetime'] = new_dt
                    adjusted_rows.append(row)
                self.df = pd.DataFrame(adjusted_rows).sort_values('Datetime').reset_index(drop=True)
                
            self.current_idx = 0
            self.session_id += 1
            current_session = self.session_id
            self.status = "RUNNING"
            
            # Start loop thread
            self.thread = threading.Thread(target=self._run_loop, args=(current_session, self.df), daemon=True)
            self.thread.start()
            print(f"Playback started for {date_str} at speed {speed}x")

    def pause(self):
        with self.lock:
            if self.status == "RUNNING":
                self.status = "PAUSED"
                self.broadcast({"event": "status", "status": "PAUSED", "log": "Simulation Paused."})

    def resume(self):
        with self.lock:
            if self.status == "PAUSED":
                self.status = "RUNNING"
                self.broadcast({"event": "status", "status": "RUNNING", "log": "Simulation Resumed."})

    def stop(self):
        with self.lock:
            self._stop_internal()

    def _stop_internal(self):
        self.status = "STOPPED"
        self.broadcast({"event": "status", "status": "STOPPED", "log": "Simulation Stopped."})
        # Wait for thread to finish if running
        self.df = None
        self.current_idx = 0

    def set_speed(self, speed):
        with self.lock:
            self.speed = float(speed)
            self.broadcast({"event": "speed", "speed": self.speed, "log": f"Speed changed to {self.speed}x."})

    def _run_loop(self, session_id, df):
        df_len = len(df)
        
        # We need the 9:30 Close price for strike selection
        bars_before_930 = df[df['Datetime'].dt.time <= datetime.time(9, 30)]
        if bars_before_930.empty:
            spot_930 = 23000.0  # default fallback
        else:
            spot_930 = bars_before_930.iloc[-1]['Close']
            
        pe_strike = round_to_nearest_strike(spot_930 + self.strike_offset_pe)
        ce_strike = round_to_nearest_strike(spot_930 + self.strike_offset_ce)
        
        # State Machine per contract
        strategy_state = {
            'PE': {'day_high': 0.0, 'entry_count': 0, 'position': None, 'entry_price': 0.0, 'current_sl': 0.0, 'target': 0.0, 'min_price': float('inf'), 'unrealized': 0.0, 'strike': pe_strike},
            'CE': {'day_high': 0.0, 'entry_count': 0, 'position': None, 'entry_price': 0.0, 'current_sl': 0.0, 'target': 0.0, 'min_price': float('inf'), 'unrealized': 0.0, 'strike': ce_strike}
        }
        
        modes = []
        if self.strategy_type in ['PE_SELL', 'BOTH']:
            modes.append('PE')
        if self.strategy_type in ['CE_SELL', 'BOTH']:
            modes.append('CE')
            
        # Log initial setup
        self.broadcast({
            "event": "log", 
            "log": f"Setting up Strategy. PE Strike: {pe_strike} | CE Strike: {ce_strike}"
        })
        
        while self.current_idx < df_len and session_id == self.session_id:
            # Check stopped
            if self.status == "STOPPED":
                break
                
            # Check paused
            if self.status == "PAUSED":
                time.sleep(0.5)
                continue
                
            # Get current spot bar
            bar = df.iloc[self.current_idx]
            dt = bar['Datetime']
            t_time = dt.time()
            spot_close = bar['Close']
            
            t_years = get_time_to_expiry_years(dt)
            
            # Process option logic
            log_messages = []
            
            for opt_type in modes:
                state = strategy_state[opt_type]
                strike = state['strike']
                
                # Option Pricing
                if opt_type == 'CE':
                    o_open = black_scholes(bar['Open'], strike, t_years, self.r, self.iv, 'CE')
                    o_high = black_scholes(bar['High'], strike, t_years, self.r, self.iv, 'CE')
                    o_low = black_scholes(bar['Low'], strike, t_years, self.r, self.iv, 'CE')
                    o_close = black_scholes(spot_close, strike, t_years, self.r, self.iv, 'CE')
                else:
                    o_open = black_scholes(bar['Open'], strike, t_years, self.r, self.iv, 'PE')
                    o_high = black_scholes(bar['Low'], strike, t_years, self.r, self.iv, 'PE')
                    o_low = black_scholes(bar['High'], strike, t_years, self.r, self.iv, 'PE')
                    o_close = black_scholes(spot_close, strike, t_years, self.r, self.iv, 'PE')
                    
                # 1. 09:15 to 09:30: Update Day High
                if t_time <= datetime.time(9, 30):
                    if o_high > state['day_high']:
                        state['day_high'] = o_high
                        log_messages.append(f"[{dt.strftime('%H:%M')}] {opt_type} Day High X updated to {state['day_high']:.2f}")
                    
                    state['price'] = o_close
                    continue
                    
                # 2. Check 15:15 Square-off
                if t_time >= datetime.time(15, 15):
                    if state['position'] is not None:
                        exit_price = o_close
                        pnl = 0.0
                        if state['position'] == 'SHORT_2_LOTS':
                            pnl = (state['entry_price'] - exit_price) * 2 * self.lot_size
                            log_messages.append(f"[{dt.strftime('%H:%M')}] [SQUARE-OFF] PE Sell 2 Lots Closed at {exit_price:.2f} (PnL: +₹{pnl:.2f})")
                        elif state['position'] == 'SHORT_1_LOT':
                            pnl = (state['entry_price'] - exit_price) * self.lot_size
                            log_messages.append(f"[{dt.strftime('%H:%M')}] [SQUARE-OFF] PE Sell 1 Lot Closed at {exit_price:.2f} (PnL: +₹{pnl:.2f})")
                            
                        self.realized_pnl += pnl
                        state['position'] = None
                        state['unrealized'] = 0.0
                    continue
                    
                # 3. Position Logic
                if state['position'] is None:
                    # Flat state: Monitor for new Day High or trigger Entry
                    if o_high > state['day_high']:
                        state['day_high'] = o_high
                        log_messages.append(f"[{dt.strftime('%H:%M')}] {opt_type} Day High X updated to {state['day_high']:.2f}")
                        
                    target_entry = state['day_high'] * (1.0 - self.entry_discount)
                    
                    if state['entry_count'] < self.max_reentries:
                        # Enter short if Low dips below target entry
                        if o_low <= target_entry:
                            state['position'] = 'SHORT_2_LOTS'
                            state['entry_price'] = target_entry
                            state['entry_count'] += 1
                            state['current_sl'] = state['day_high']
                            state['target'] = state['day_high'] * (1.0 - self.lot_1_target_pct)
                            state['min_price'] = o_low
                            log_messages.append(f"[{dt.strftime('%H:%M')}] [ENTRY #{state['entry_count']}] Shorted 2 Lots of {opt_type} {strike} at {target_entry:.2f} (SL: {state['current_sl']:.2f} | Tgt: {state['target']:.2f})")
                            
                elif state['position'] == 'SHORT_2_LOTS':
                    state['min_price'] = min(state['min_price'], o_low)
                    
                    # Calculate live unrealized PnL
                    state['unrealized'] = (state['entry_price'] - o_close) * 2 * self.lot_size
                    
                    # Check SL
                    if o_high >= state['current_sl']:
                        pnl = (state['entry_price'] - state['current_sl']) * 2 * self.lot_size
                        self.realized_pnl += pnl
                        state['position'] = None
                        state['unrealized'] = 0.0
                        log_messages.append(f"[{dt.strftime('%H:%M')}] [STOP LOSS] {opt_type} SL hit at {state['current_sl']:.2f} (PnL: ₹{pnl:.2f})")
                        state['day_high'] = max(state['day_high'], o_high)
                        
                    # Check Target Lot 1
                    elif o_low <= state['target']:
                        pnl = (state['entry_price'] - state['target']) * self.lot_size
                        self.realized_pnl += pnl
                        state['position'] = 'SHORT_1_LOT'
                        state['unrealized'] = (state['entry_price'] - o_close) * self.lot_size
                        log_messages.append(f"[{dt.strftime('%H:%M')}] [TARGET LOT 1] {opt_type} Target reached at {state['target']:.2f} (Realized: +₹{pnl:.2f})")
                        state['current_sl'] = get_trailed_sl(state['min_price'], state['day_high'])
                        
                elif state['position'] == 'SHORT_1_LOT':
                    state['min_price'] = min(state['min_price'], o_low)
                    
                    state['current_sl'] = get_trailed_sl(state['min_price'], state['day_high'])
                    state['unrealized'] = (state['entry_price'] - o_close) * self.lot_size
                    
                    # Check Trailing SL
                    if o_high >= state['current_sl']:
                        pnl = (state['entry_price'] - state['current_sl']) * self.lot_size
                        self.realized_pnl += pnl
                        state['position'] = None
                        state['unrealized'] = 0.0
                        log_messages.append(f"[{dt.strftime('%H:%M')}] [TRAILING SL] {opt_type} Trailing SL hit at {state['current_sl']:.2f} (PnL: ₹{pnl:.2f})")
                        state['day_high'] = max(state['day_high'], o_high)
                        
                # Update current option price to show on dashboard
                state['price'] = o_close
            
            # Broadcast state
            total_unrealized = sum(strategy_state[k]['unrealized'] for k in modes)
            
            pe_packet = strategy_state['PE'] if 'PE' in modes else None
            ce_packet = strategy_state['CE'] if 'CE' in modes else None
            
            tick_packet = {
                "event": "tick",
                "datetime": dt.strftime('%Y-%m-%d %H:%M'),
                "spot": round(spot_close, 2),
                "account": {
                    "balance": round(self.capital + self.realized_pnl + total_unrealized, 2),
                    "realized_pnl": round(self.realized_pnl, 2),
                    "unrealized_pnl": round(total_unrealized, 2),
                    "capital": self.capital
                },
                "pe": {
                    "strike": pe_packet['strike'],
                    "price": round(pe_packet['price'], 2),
                    "day_high": round(pe_packet['day_high'], 2),
                    "position": str(pe_packet['position']),
                    "entry_price": round(pe_packet['entry_price'], 2),
                    "sl": round(pe_packet['current_sl'], 2),
                    "target": round(pe_packet['target'], 2),
                    "reentries": pe_packet['entry_count']
                } if pe_packet else {},
                "ce": {
                    "strike": ce_packet['strike'],
                    "price": round(ce_packet['price'], 2),
                    "day_high": round(ce_packet['day_high'], 2),
                    "position": str(ce_packet['position']),
                    "entry_price": round(ce_packet['entry_price'], 2),
                    "sl": round(ce_packet['current_sl'], 2),
                    "target": round(ce_packet['target'], 2),
                    "reentries": ce_packet['entry_count']
                } if ce_packet else {},
                "logs": log_messages
            }
            
            self.broadcast(tick_packet)
            
            # Clock speed control
            self.current_idx += 1
            time.sleep(1.0 / self.speed)
            
        # Finished playback
        if session_id == self.session_id:
            self.status = "STOPPED"
            self.broadcast({"event": "status", "status": "STOPPED", "log": "Simulation completed."})

# Global manager instance
playback_manager = PlaybackManager()
