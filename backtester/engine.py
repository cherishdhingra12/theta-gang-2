import math
import datetime
import pandas as pd
import numpy as np
import yfinance as yf
import os

# ----------------------------------------------------
# 1. Option Pricing Utilities (Black-Scholes-Merton)
# ----------------------------------------------------

def std_normal_cdf(x):
    """Cumulative Distribution Function of standard normal distribution."""
    try:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    except (ValueError, OverflowError):
        return 1.0 if x > 0 else 0.0

def black_scholes(s, k, t, r, sigma, option_type):
    """
    Calculates the theoretical Black-Scholes price of a European option.
    s: spot price
    k: strike price
    t: time to expiry in years
    r: risk-free rate (decimal, e.g., 0.07)
    sigma: implied volatility (decimal, e.g., 0.15)
    option_type: 'CE' or 'PE'
    """
    if t <= 0:
        if option_type == 'CE':
            return max(0.01, float(s - k))
        else:
            return max(0.01, float(k - s))
    
    # Avoid zero volatility division
    sigma = max(0.0001, sigma)
    
    try:
        d1 = (math.log(s / k) + (r + 0.5 * sigma ** 2) * t) / (sigma * math.sqrt(t))
        d2 = d1 - sigma * math.sqrt(t)
    except (ValueError, ZeroDivisionError):
        d1, d2 = 0.0, 0.0
        
    if option_type == 'CE':
        price = s * std_normal_cdf(d1) - k * math.exp(-r * t) * std_normal_cdf(d2)
    else:
        price = k * math.exp(-r * t) * std_normal_cdf(-d2) - s * std_normal_cdf(-d1)
        
    return max(0.05, float(price))

def get_time_to_expiry_years(dt, expiry_date=None):
    """
    Calculates time to expiry in years. Defaults to assuming weekly Tuesday expiry if expiry_date is None.
    Expiries happen at 15:30 IST.
    """
    if expiry_date is None:
        current_date = dt.date()
        weekday = current_date.weekday()
        # Days until Tuesday (1)
        days_to_tuesday = (1 - weekday) % 7
        expiry_date = current_date + datetime.timedelta(days=days_to_tuesday)
    
    expiry_datetime = datetime.datetime.combine(
        expiry_date,
        datetime.time(15, 30)
    )
    
    # Handle timezone awareness
    if dt.tzinfo is not None:
        expiry_datetime = expiry_datetime.replace(tzinfo=dt.tzinfo)
        
    # If it's Tuesday after 15:30, it expires next Tuesday
    if dt >= expiry_datetime:
        expiry_datetime += datetime.timedelta(days=7)
        
    time_diff = expiry_datetime - dt
    seconds_remaining = time_diff.total_seconds()
    
    # Convert to years (365 days base)
    return max(0.0, seconds_remaining / (365.0 * 24.0 * 3600.0))

def round_to_nearest_strike(price, step=50):
    """Rounds Nifty prices to the nearest strike interval (e.g. 50) using half-up rounding."""
    return int(math.floor(price / step + 0.5) * step)

# ----------------------------------------------------
# 2. Data Manager
# ----------------------------------------------------

def download_nifty_spot_data(symbol="^NSEI", period="1mo"):
    """
    Downloads 1-minute historical spot data for the symbol.
    If the requested range is longer than 7 days, it fetches in 5-day chunks to bypass Yahoo Finance's restrictions.
    """
    try:
        print(f"Downloading historical data for {symbol} (period={period})...")
        
        # Determine number of days based on period
        if period == "1mo":
            days = 30
        elif period == "15d":
            days = 15
        elif period == "7d":
            days = 7
        else:
            days = 7  # Fallback
            
        if days > 7:
            # Fetch in 5-day chunks to bypass Yahoo Finance's 7-day limit for 1-minute data
            end_dt = datetime.datetime.now()
            start_dt = end_dt - datetime.timedelta(days=days)
            
            chunks = []
            current_start = start_dt
            while current_start < end_dt:
                current_end = min(current_start + datetime.timedelta(days=5), end_dt)
                start_str = current_start.strftime('%Y-%m-%d')
                end_str = current_end.strftime('%Y-%m-%d')
                print(f"Fetching chunk: {start_str} to {end_str}")
                
                chunk = yf.download(tickers=symbol, start=start_str, end=end_str, interval="1m")
                if not chunk.empty:
                    chunks.append(chunk)
                current_start = current_end
                
            if not chunks:
                raise ValueError("No data chunks downloaded successfully.")
            df = pd.concat(chunks)
        else:
            df = yf.download(tickers=symbol, period=period, interval="1m")
            
        if df.empty:
            raise ValueError("Empty DataFrame returned from yfinance.")
        
        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
            
        df = df.reset_index()
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        
        # Convert to Asia/Kolkata timezone
        try:
            df['Datetime'] = df['Datetime'].dt.tz_convert('Asia/Kolkata')
        except TypeError:
            if df['Datetime'].dt.tz is None:
                df['Datetime'] = df['Datetime'].dt.localize('UTC').dt.tz_convert('Asia/Kolkata')
                
        df = df.drop_duplicates(subset=['Datetime'])
        df = df.sort_values('Datetime').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Failed to fetch market data from Yahoo Finance: {e}")
        print("Generating synthetic Nifty 50 market data for backtesting...")
        return generate_synthetic_spot_data(days=30 if period == "1mo" else (15 if period == "15d" else 7))

def generate_synthetic_spot_data(days=10):
    """
    Generates realistic 1-minute Nifty 50 spot data for simulation/testing.
    Trading hours: 09:15 to 15:30.
    """
    timestamps = []
    current_time = datetime.datetime.now() - datetime.timedelta(days=days)
    spot = 23200.0
    daily_vol = 0.008  # 0.8% daily volatility
    step_vol = daily_vol / math.sqrt(375)  # 375 minutes in a trading day
    
    # Standard Nifty lot sizes and strikes
    for d in range(days + 2):
        date_check = current_time + datetime.timedelta(days=d)
        # Skip weekends
        if date_check.weekday() >= 5:
            continue
            
        # Initialize day spot price with some gap up/down
        spot = spot * (1.0 + np.random.normal(0, 0.003))
        
        # 09:15 to 15:30 is 376 minutes
        start_dt = datetime.datetime.combine(date_check.date(), datetime.time(9, 15))
        for m in range(376):
            dt = start_dt + datetime.timedelta(minutes=m)
            # Localize to IST
            import pytz
            ist = pytz.timezone('Asia/Kolkata')
            dt_ist = ist.localize(dt)
            
            # Random walk
            change = np.random.normal(0, step_vol)
            close = spot * (1.0 + change)
            high = max(spot, close) * (1.0 + abs(np.random.normal(0, step_vol * 0.5)))
            low = min(spot, close) * (1.0 - abs(np.random.normal(0, step_vol * 0.5)))
            open_p = spot
            
            timestamps.append({
                'Datetime': dt_ist,
                'Open': open_p,
                'High': high,
                'Low': low,
                'Close': close,
                'Volume': 1000
            })
            spot = close
            
    return pd.DataFrame(timestamps)

# ----------------------------------------------------
# 3. Core Strategy Simulation Engine
# ----------------------------------------------------

def get_trailed_sl(min_price, X):
    """
    Determines the trailed Stop Loss (SL) for Lot 2 based on the minimum option price reached.
    X: Initial Day High of the contract.
    """
    # Milestones are: X - 30%, X - 40%, etc. (i.e. 0.7X, 0.6X, 0.5X...)
    # SL trails by 20% of Day High (0.2X) behind the milestone.
    if min_price <= 0.1 * X:
        return 0.3 * X
    elif min_price <= 0.2 * X:
        return 0.4 * X
    elif min_price <= 0.3 * X:
        return 0.5 * X
    elif min_price <= 0.4 * X:
        return 0.6 * X
    elif min_price <= 0.5 * X:
        return 0.7 * X
    elif min_price <= 0.6 * X:
        return 0.8 * X
    elif min_price <= 0.7 * X:
        return 0.9 * X
    else:
        return X  # Initial SL is the day high X

def run_backtest(df, strategy_type='BOTH', strike_offset_pe=100, strike_offset_ce=-100,
                 entry_discount=0.10, lot_1_target_pct=0.40, max_reentries=5,
                 lot_size=75, iv=0.15, r=0.07, slippage_pts=0.0, brokerage_per_order=0.0):
    """
    Runs the intraday options selling strategy backtest.
    df: 1-minute OHLCV spot dataframe.
    strategy_type: 'PE_SELL', 'CE_SELL', or 'BOTH'
    """
    # Ensure correct strategy modes
    modes = []
    if strategy_type in ['PE_SELL', 'BOTH']:
        modes.append('PE')
    if strategy_type in ['CE_SELL', 'BOTH']:
        modes.append('CE')
        
    trades = []
    
    # Group by date (using local IST date)
    df['Date'] = df['Datetime'].dt.date
    grouped = df.groupby('Date')
    
    for date, group in grouped:
        day_bars = group.sort_values('Datetime').reset_index(drop=True)
        if len(day_bars) < 20:  # Skip days with insufficient bars (e.g. half trading days or missing data)
            continue
            
        # Get spot price at 09:30 AM
        bars_before_930 = day_bars[day_bars['Datetime'].dt.time <= datetime.time(9, 30)]
        if bars_before_930.empty:
            continue
            
        spot_930 = bars_before_930.iloc[-1]['Close']
        
        # Determine Strikes
        pe_strike = round_to_nearest_strike(spot_930 + strike_offset_pe)
        ce_strike = round_to_nearest_strike(spot_930 + strike_offset_ce)
        
        # For each selected option type
        for opt_type in modes:
            strike = pe_strike if opt_type == 'PE' else ce_strike
            
            # Simulate options prices for all bars in the day
            # CE price rises as Spot rises. PE price rises as Spot falls.
            opt_bars = []
            for idx, bar in day_bars.iterrows():
                dt = bar['Datetime']
                t_years = get_time_to_expiry_years(dt)
                
                # Spot levels
                s_open, s_high, s_low, s_close = bar['Open'], bar['High'], bar['Low'], bar['Close']
                
                # Option pricing
                if opt_type == 'CE':
                    o_open = black_scholes(s_open, strike, t_years, r, iv, 'CE')
                    o_high = black_scholes(s_high, strike, t_years, r, iv, 'CE')
                    o_low = black_scholes(s_low, strike, t_years, r, iv, 'CE')
                    o_close = black_scholes(s_close, strike, t_years, r, iv, 'CE')
                else:  # PE
                    o_open = black_scholes(s_open, strike, t_years, r, iv, 'PE')
                    o_high = black_scholes(s_low, strike, t_years, r, iv, 'PE')
                    o_low = black_scholes(s_high, strike, t_years, r, iv, 'PE')
                    o_close = black_scholes(s_close, strike, t_years, r, iv, 'PE')
                
                opt_bars.append({
                    'Datetime': dt,
                    'Open': o_open,
                    'High': o_high,
                    'Low': o_low,
                    'Close': o_close
                })
                
            opt_df = pd.DataFrame(opt_bars)
            
            # State Machine Variables
            day_high = 0.0
            entry_count = 0
            position = None  # None, 'SHORT_2_LOTS', 'SHORT_1_LOT'
            above_entry_threshold = None
            
            # Trading stats for this contract today
            entry_price = 0.0
            lot1_exit_price = 0.0
            lot2_exit_price = 0.0
            entry_time = None
            lot1_exit_time = None
            lot2_exit_time = None
            current_sl = 0.0
            min_price_since_entry = float('inf')
            
            # Process each minute bar starting from 09:15
            for idx, bar in opt_df.iterrows():
                dt = bar['Datetime']
                t_time = dt.time()
                
                o_open, o_high, o_low, o_close = bar['Open'], bar['High'], bar['Low'], bar['Close']
                
                # 1. 09:15 to 09:30: Only identify Day High
                if t_time <= datetime.time(9, 30):
                    if o_high > day_high:
                        day_high = o_high
                    continue
                
                # 2. Check if square-off time is reached (15:15 IST)
                if t_time >= datetime.time(15, 15):
                    if position is not None:
                        # Close whatever is remaining
                        exit_price = o_close
                        pnl_points = 0.0
                        
                        if position == 'SHORT_2_LOTS':
                            lot1_exit_price = exit_price
                            lot2_exit_price = exit_price
                            lot1_exit_time = dt
                            lot2_exit_time = dt
                            pnl_points = (entry_price - exit_price) * 2
                            trades.append({
                                'Date': date, 'Type': opt_type, 'Strike': strike,
                                'EntryTime': entry_time.strftime('%H:%M'), 'EntryPrice': round(entry_price, 2),
                                'ExitTime': dt.strftime('%H:%M'), 'ExitPrice': round(exit_price, 2),
                                'Lots': 2, 'PnLPoints': round(pnl_points, 2),
                                'PnL': round(pnl_points * lot_size, 2),
                                'Reason': 'Square-off (15:15)', 'DayHigh': round(day_high, 2),
                                'ReEntryNum': entry_count
                            })
                        elif position == 'SHORT_1_LOT':
                            lot2_exit_price = exit_price
                            lot2_exit_time = dt
                            pnl_points = (entry_price - exit_price)
                            trades.append({
                                'Date': date, 'Type': opt_type, 'Strike': strike,
                                'EntryTime': entry_time.strftime('%H:%M'), 'EntryPrice': round(entry_price, 2),
                                'ExitTime': dt.strftime('%H:%M'), 'ExitPrice': round(exit_price, 2),
                                'Lots': 1, 'PnLPoints': round(pnl_points, 2),
                                'PnL': round(pnl_points * lot_size, 2),
                                'Reason': 'Square-off (15:15)', 'DayHigh': round(day_high, 2),
                                'ReEntryNum': entry_count
                            })
                        position = None
                    break # No more trading today
                
                # 3. Position Logic
                if position is None:
                    # Flat state: Monitor for new day highs or entry fills
                    if o_high > day_high:
                        day_high = o_high
                        # Entry price updates dynamically as Day High changes
                    
                    target_entry_price = day_high * (1.0 - entry_discount)
                    
                    # Initialize or track above_entry_threshold
                    if above_entry_threshold is None:
                        above_entry_threshold = (o_open > target_entry_price)
                    if o_high > target_entry_price:
                        above_entry_threshold = True
                    
                    # We only enter if we haven't exhausted our entries for the day
                    if entry_count < max_reentries:
                        # We trigger if Low is below or equal to target_entry_price and we crossed from above
                        if above_entry_threshold and o_low <= target_entry_price:
                            # Enter position (fill at open if it opened below threshold, otherwise exactly at threshold)
                            entry_price = target_entry_price if o_open >= target_entry_price else o_open
                            entry_time = dt
                            position = 'SHORT_2_LOTS'
                            entry_count += 1
                            min_price_since_entry = o_low
                            current_sl = day_high
                            # Reset trade stats
                            lot1_exit_price = 0.0
                            lot2_exit_price = 0.0
                            lot1_exit_time = None
                            lot2_exit_time = None
                            above_entry_threshold = None
                
                elif position == 'SHORT_2_LOTS':
                    # Update lowest price since entry
                    min_price_since_entry = min(min_price_since_entry, o_low)
                    
                    # Once the premium reaches X-30%, SL should shift to X-10% for Lot 1 & Lot 2
                    if min_price_since_entry <= day_high * 0.7:
                        current_sl = day_high * 0.9
                    else:
                        current_sl = day_high
                        
                    # Target price for Lot 1
                    lot1_target = day_high * (1.0 - lot_1_target_pct)
                    
                    # Conservative check: if both SL and Target are breached in the same bar, assume SL (worst-case)
                    if o_high >= current_sl and o_low <= lot1_target:
                        # Stop loss hit first (conservative assumption)
                        exit_price = current_sl
                        lot1_exit_price = exit_price
                        lot2_exit_price = exit_price
                        lot1_exit_time = dt
                        lot2_exit_time = dt
                        pnl_points = (entry_price - exit_price) * 2
                        
                        trades.append({
                            'Date': date, 'Type': opt_type, 'Strike': strike,
                            'EntryTime': entry_time.strftime('%H:%M'), 'EntryPrice': round(entry_price, 2),
                            'ExitTime': dt.strftime('%H:%M'), 'ExitPrice': round(exit_price, 2),
                            'Lots': 2, 'PnLPoints': round(pnl_points, 2),
                            'PnL': round(pnl_points * lot_size, 2),
                            'Reason': 'Stop Loss (Shifted)' if current_sl < day_high else 'Stop Loss (Initial)', 'DayHigh': round(day_high, 2),
                            'ReEntryNum': entry_count
                        })
                        
                        # Set up for re-entry: current day high updates
                        day_high = max(day_high, o_high)
                        position = None
                        above_entry_threshold = None
                        
                    elif o_high >= current_sl:
                        # Stop Loss Hit
                        exit_price = current_sl
                        lot1_exit_price = exit_price
                        lot2_exit_price = exit_price
                        lot1_exit_time = dt
                        lot2_exit_time = dt
                        pnl_points = (entry_price - exit_price) * 2
                        
                        trades.append({
                            'Date': date, 'Type': opt_type, 'Strike': strike,
                            'EntryTime': entry_time.strftime('%H:%M'), 'EntryPrice': round(entry_price, 2),
                            'ExitTime': dt.strftime('%H:%M'), 'ExitPrice': round(exit_price, 2),
                            'Lots': 2, 'PnLPoints': round(pnl_points, 2),
                            'PnL': round(pnl_points * lot_size, 2),
                            'Reason': 'Stop Loss (Shifted)' if current_sl < day_high else 'Stop Loss (Initial)', 'DayHigh': round(day_high, 2),
                            'ReEntryNum': entry_count
                        })
                        
                        # Set up for re-entry
                        day_high = max(day_high, o_high)
                        position = None
                        above_entry_threshold = None
                        
                    elif o_low <= lot1_target:
                        # Lot 1 Target Hit!
                        lot1_exit_price = lot1_target
                        lot1_exit_time = dt
                        pnl_l1 = (entry_price - lot1_target)
                        
                        trades.append({
                            'Date': date, 'Type': opt_type, 'Strike': strike,
                            'EntryTime': entry_time.strftime('%H:%M'), 'EntryPrice': round(entry_price, 2),
                            'ExitTime': dt.strftime('%H:%M'), 'ExitPrice': round(lot1_target, 2),
                            'Lots': 1, 'PnLPoints': round(pnl_l1, 2),
                            'PnL': round(pnl_l1 * lot_size, 2),
                            'Reason': 'Target (Lot 1)', 'DayHigh': round(day_high, 2),
                            'ReEntryNum': entry_count
                        })
                        
                        # Move to 1 lot state and trail SL
                        position = 'SHORT_1_LOT'
                        current_sl = get_trailed_sl(min_price_since_entry, day_high)
                        
                elif position == 'SHORT_1_LOT':
                    # Update lowest price since entry
                    min_price_since_entry = min(min_price_since_entry, o_low)
                    
                    # Update Trailing SL
                    current_sl = get_trailed_sl(min_price_since_entry, day_high)
                    
                    # Check if Trailing SL is hit
                    if o_high >= current_sl:
                        exit_price = current_sl
                        lot2_exit_price = exit_price
                        lot2_exit_time = dt
                        pnl_l2 = (entry_price - exit_price)
                        
                        trades.append({
                            'Date': date, 'Type': opt_type, 'Strike': strike,
                            'EntryTime': entry_time.strftime('%H:%M'), 'EntryPrice': round(entry_price, 2),
                            'ExitTime': dt.strftime('%H:%M'), 'ExitPrice': round(exit_price, 2),
                            'Lots': 1, 'PnLPoints': round(pnl_l2, 2),
                            'PnL': round(pnl_l2 * lot_size, 2),
                            'Reason': 'Trailing Stop Loss', 'DayHigh': round(day_high, 2),
                            'ReEntryNum': entry_count
                        })
                        
                        # Set up for re-entry
                        day_high = max(day_high, o_high)
                        position = None
                        above_entry_threshold = None
                        
    return trades

# ----------------------------------------------------
# 4. Performance Metrics Aggregator
# ----------------------------------------------------

def generate_performance_metrics(trades, lot_size=75, initial_capital=200000.0):
    """
    Calculates key performance metrics from the list of executed trades.
    """
    if not trades:
        return {
            'total_trades': 0, 'win_rate': 0.0, 'net_pnl': 0.0, 'max_drawdown': 0.0,
            'profit_factor': 0.0, 'rom': 0.0, 'trade_log': []
        }
        
    df_trades = pd.DataFrame(trades)
    
    # Group trades by date to find daily PnLs
    daily_pnl = df_trades.groupby('Date')['PnL'].sum().reset_index()
    
    total_trades = len(df_trades)
    winning_trades = len(df_trades[df_trades['PnL'] > 0])
    win_rate = (winning_trades / total_trades) * 100.0 if total_trades > 0 else 0.0
    
    gross_profits = df_trades[df_trades['PnL'] > 0]['PnL'].sum()
    gross_losses = abs(df_trades[df_trades['PnL'] < 0]['PnL'].sum())
    profit_factor = gross_profits / gross_losses if gross_losses > 0 else float('inf')
    
    net_pnl = df_trades['PnL'].sum()
    
    # Calculate Drawdown on equity curve
    daily_pnl = daily_pnl.sort_values('Date').reset_index(drop=True)
    daily_pnl['Cumulative'] = daily_pnl['PnL'].cumsum() + initial_capital
    daily_pnl['Peak'] = daily_pnl['Cumulative'].cummax()
    daily_pnl['Drawdown'] = daily_pnl['Cumulative'] - daily_pnl['Peak']
    daily_pnl['DrawdownPct'] = (daily_pnl['Drawdown'] / daily_pnl['Peak']) * 100.0
    
    max_drawdown_amt = daily_pnl['Drawdown'].min()
    max_drawdown_pct = daily_pnl['DrawdownPct'].min()
    
    # Standardize output types for JSON serializability
    equity_curve = []
    for _, r_row in daily_pnl.iterrows():
        equity_curve.append({
            'Date': r_row['Date'].strftime('%Y-%m-%d') if hasattr(r_row['Date'], 'strftime') else str(r_row['Date']),
            'Cumulative': round(float(r_row['Cumulative']), 2)
        })
        
    serializable_trades = []
    for t in trades:
        t_copy = t.copy()
        if hasattr(t_copy['Date'], 'strftime'):
            t_copy['Date'] = t_copy['Date'].strftime('%Y-%m-%d')
        else:
            t_copy['Date'] = str(t_copy['Date'])
        serializable_trades.append(t_copy)
        
    return {
        'total_trades': int(total_trades),
        'win_rate': round(float(win_rate), 2),
        'net_pnl': round(float(net_pnl), 2),
        'gross_profit': round(float(gross_profits), 2),
        'gross_loss': round(float(gross_losses), 2),
        'profit_factor': round(float(profit_factor), 2) if profit_factor != float('inf') else 'N/A',
        'max_drawdown_amount': round(float(max_drawdown_amt), 2),
        'max_drawdown': round(abs(float(max_drawdown_pct)), 2),
        'rom': round(float((net_pnl / initial_capital) * 100.0), 2),
        'equity_curve': equity_curve,
        'trade_log': serializable_trades
    }
