import time
import threading
import queue
import datetime
import yfinance as yf
import pandas as pd
import pytz
import random
import os
import json
from engine import round_to_nearest_strike, get_time_to_expiry_years, black_scholes, get_trailed_sl
from kite_auth import automate_kite_login
try:
    from nse_holidays import is_trading_holiday, get_holiday_name, get_market_day_status, is_market_hours_now
except ImportError:
    from live_bot.nse_holidays import is_trading_holiday, get_holiday_name, get_market_day_status, is_market_hours_now

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.json')
TRADES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'live_trades.json')
LIVE_STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'live_state.json')

def load_live_state():
    if not os.path.exists(LIVE_STATE_PATH):
        return None
    try:
        with open(LIVE_STATE_PATH, 'r') as f:
            content = f.read().strip()
            if not content:
                return None
            return json.loads(content)
    except Exception as e:
        print(f"Error loading live_state.json: {e}")
        return None

def save_live_state(state_data):
    try:
        os.makedirs(os.path.dirname(LIVE_STATE_PATH), exist_ok=True)
        temp_path = f"{LIVE_STATE_PATH}.tmp.{os.getpid()}.{threading.get_ident()}"
        def sanitize(obj):
            if isinstance(obj, dict):
                return {k: sanitize(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [sanitize(v) for v in obj]
            elif isinstance(obj, float) and (obj == float('inf') or obj == float('-inf')):
                return 999999.0
            return obj

        clean_data = sanitize(state_data)
        with open(temp_path, 'w') as f:
            json.dump(clean_data, f, indent=2)
        os.replace(temp_path, LIVE_STATE_PATH)
        return True
    except Exception as e:
        print(f"Error saving live_state.json: {e}")
        return False


def load_env_file():
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        k, v = k.strip(), v.strip()
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception as e:
            print(f"Error loading .env file: {e}")

load_env_file()

def load_strategy_config():
    config = {
        "auto_pilot": False,
        "mode": "LIVE",
        "capital": 500000.0,
        "strategy_type": "BOTH",
        "strike_offset_pe": 100,
        "strike_offset_ce": -100,
        "entry_discount": 10.0,
        "lot_1_target": 40.0,
        "max_reentries": 5,
        "lot_size": 65,
        "iv": 15.0,
        "r": 7.0
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                config.update(json.load(f))
        except Exception as e:
            print(f"Error loading config.json: {e}")
            
    # Inject credentials from environment variables
    config['kite_api_key'] = os.environ.get('KITE_API_KEY', config.get('kite_api_key', ''))
    config['kite_api_secret'] = os.environ.get('KITE_API_SECRET', config.get('kite_api_secret', ''))
    config['kite_user_id'] = os.environ.get('KITE_USER_ID', config.get('kite_user_id', ''))
    config['kite_password'] = os.environ.get('KITE_PASSWORD', config.get('kite_password', ''))
    config['kite_totp_secret'] = os.environ.get('KITE_TOTP_SECRET', config.get('kite_totp_secret', ''))
    return config

def save_strategy_config(config_data):
    try:
        cred_mapping = {
            'kite_api_key': 'KITE_API_KEY',
            'kite_api_secret': 'KITE_API_SECRET',
            'kite_user_id': 'KITE_USER_ID',
            'kite_password': 'KITE_PASSWORD',
            'kite_totp_secret': 'KITE_TOTP_SECRET'
        }
        clean_config = config_data.copy()
        env_updates = {}
        for ckey, env_var in cred_mapping.items():
            if ckey in clean_config:
                val = clean_config.pop(ckey)
                if val:
                    os.environ[env_var] = str(val)
                    env_updates[env_var] = str(val)

        with open(CONFIG_PATH, 'w') as f:
            json.dump(clean_config, f, indent=2)

        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
        if os.path.exists(env_path) and env_updates:
            lines = []
            with open(env_path, 'r') as f:
                lines = f.readlines()
            new_lines = []
            updated_keys = set()
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and '=' in stripped:
                    k, _ = stripped.split('=', 1)
                    k = k.strip()
                    if k in env_updates:
                        new_lines.append(f"{k}={env_updates[k]}\n")
                        updated_keys.add(k)
                        continue
                new_lines.append(line)
            for k, v in env_updates.items():
                if k not in updated_keys:
                    new_lines.append(f"{k}={v}\n")
            with open(env_path, 'w') as f:
                f.writelines(new_lines)
                
        return True
    except Exception as e:
        print(f"Error saving config.json: {e}")
        return False

def load_live_trades():
    if not os.path.exists(TRADES_PATH):
        return []
    try:
        with open(TRADES_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading live_trades.json: {e}")
        return []

def save_live_trade(trade):
    try:
        trades = load_live_trades()
        trades.append(trade)
        with open(TRADES_PATH, 'w') as f:
            json.dump(trades, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving trade to live_trades.json: {e}")
        return False

def get_inception_summary(unrealized_pnl=0.0):
    trades = load_live_trades()
    if not trades:
        return {
            "total_pnl": round(unrealized_pnl, 2),
            "realized_pnl": 0.0,
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "profit_factor": "N/A",
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "start_date": "N/A"
        }
    
    realized_pnl = sum(float(t.get('pnl', 0.0)) for t in trades)
    total_pnl = realized_pnl + float(unrealized_pnl)
    total_trades = len(trades)
    winning_trades = [t for t in trades if float(t.get('pnl', 0.0)) > 0]
    losing_trades = [t for t in trades if float(t.get('pnl', 0.0)) < 0]
    
    win_count = len(winning_trades)
    lose_count = len(losing_trades)
    win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0
    
    gross_profit = sum(float(t.get('pnl', 0.0)) for t in winning_trades)
    gross_loss = abs(sum(float(t.get('pnl', 0.0)) for t in losing_trades))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else "N/A"
    
    dates = [t.get('date') for t in trades if t.get('date')]
    start_date = min(dates) if dates else "N/A"
    
    return {
        "total_pnl": round(total_pnl, 2),
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_trades": total_trades,
        "winning_trades": win_count,
        "losing_trades": lose_count,
        "win_rate": round(win_rate, 1),
        "profit_factor": profit_factor,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "start_date": start_date
    }

def send_telegram_alert(message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Error sending Telegram alert: {e}")


class LiveManager:
    """
    Manager for the Live Trading Console.
    Runs a background loop to track market data (via Yahoo Finance spot feed or mock simulator),
    evaluates the options trading strategy rules in real-time, and streams updates to clients.
    """
    def __init__(self):
        self.status = "STOPPED"  # RUNNING, STOPPED, FEED_DISRUPTED
        self.mode = "MOCK"       # LIVE, MOCK
        self.symbol = "^NSEI"    # Nifty 50 ticker
        
        self.thread = None
        self.lock = threading.Lock()
        self.clients = []       # Active SSE client queues
        
        # State variables
        self.capital = 500000.0
        self.realized_pnl = 0.0
        self.active_trades = []
        
        # Strategy parameters
        self.strategy_type = "BOTH"  # BOTH, PE_SELL, CE_SELL
        self.strike_offset_pe = 100
        self.strike_offset_ce = -100
        self.entry_discount = 0.10
        self.lot_1_target_pct = 0.40
        self.max_reentries = 5
        self.lot_size = 65
        self.iv = 0.15
        self.r = 0.07
        
        # Current execution state
        self.spot_price = 0.0
        self.simulated_dt = None  # Used for simulated clock in Mock Mode
        self.strategy_state = {}
        self.session_id = 0
        self.nse_session = None
        self.logs = []
        
        # Zerodha specific fields
        self.disruption_counter = 0
        self.feed_disrupted = False
        self.cached_expiry = None

        # Stability / watchdog fields
        self._loop_error_count = 0          # Consecutive loop errors counter
        self.last_tick_time = None           # UTC timestamp of last successful broadcast

        # WebSocket tick engine fields
        self.kws = None
        self.websocket_active = False
        self.websocket_tokens = {}
        self.spot_token = None
        self.last_websocket_tick_time = None
        self.circuit_breaker_tripped = False

    def place_zerodha_order(self, symbol, transaction_type, quantity, order_type="MARKET", trigger_price=0.0, price=0.0):
        """
        Executes real order placement on Zerodha KiteConnect for LIVE_REAL mode.
        Complies with Zerodha 10 OPS rate limits, SL-M restrictions (uses SL-L with limit buffer),
        and market protection parameters.
        Includes multi-try status verification polling (up to 3 seconds).
        """
        if not self.kite:
            self.add_log("🔴 Order placement failed: Zerodha client not connected.")
            return None
            
        try:
            tradingsymbol = symbol.split(":")[1] if ":" in symbol else symbol
            
            # Rate limit throttle: 100ms delay to satisfy 10 OPS limit
            time.sleep(0.1)
            
            params = {
                "variety": self.kite.VARIETY_REGULAR,
                "exchange": self.kite.EXCHANGE_NFO,
                "tradingsymbol": tradingsymbol,
                "transaction_type": transaction_type,
                "quantity": int(quantity),
                "product": self.kite.PRODUCT_NRML,
                "order_type": order_type
            }
            
            # Handle SL-L (Stop-Loss Limit) with buffer to comply with Zerodha's disabled SL-M restriction
            if order_type == "SL":
                params["trigger_price"] = round(float(trigger_price), 2)
                if price <= 0:
                    if transaction_type == "BUY":
                        params["price"] = round(float(trigger_price) * 1.05, 2)  # 5% buffer for Buy SL
                    else:
                        params["price"] = round(float(trigger_price) * 0.95, 2)  # 5% buffer for Sell SL
                else:
                    params["price"] = round(float(price), 2)
            elif order_type == "MARKET":
                params["market_protection"] = 1.0
                
            self.add_log(f"⚡ [REAL ORDER] Placing {transaction_type} {quantity} {tradingsymbol} ({order_type})...")
            order_id = self.kite.place_order(**params)
            self.add_log(f"✅ [REAL ORDER SUBMITTED] Order ID: {order_id}")
            
            # Active status verification polling (up to 6 attempts across ~3 seconds)
            for attempt in range(6):
                time.sleep(0.5)
                try:
                    history = self.kite.order_history(order_id)
                    if history:
                        last_state = history[-1]
                        status = last_state.get("status")
                        avg_price = last_state.get("average_price", 0.0)
                        if status == "COMPLETE":
                            self.add_log(f"🎯 [ORDER FILLED] {tradingsymbol} filled @ ₹{avg_price:.2f}")
                            send_telegram_alert(f"🎯 <b>ORDER FILLED</b>\n{transaction_type} {quantity} {tradingsymbol}\nPrice: ₹{avg_price:.2f}\nOrder ID: {order_id}")
                            return {"order_id": order_id, "status": "COMPLETE", "filled_price": avg_price}
                        elif status == "REJECTED":
                            reason = last_state.get("status_message", "Unknown error")
                            self.add_log(f"🔴 [ORDER REJECTED] {tradingsymbol}: {reason}")
                            send_telegram_alert(f"🔴 <b>ORDER REJECTED</b>\n{tradingsymbol}: {reason}")
                            return {"order_id": order_id, "status": "REJECTED", "reason": reason}
                        elif status == "CANCELLED":
                            self.add_log(f"⚪ [ORDER CANCELLED] {tradingsymbol} Order ID: {order_id}")
                            return {"order_id": order_id, "status": "CANCELLED"}
                except Exception as ex:
                    print(f"Error checking order status on attempt {attempt+1}: {ex}")
                
            return {"order_id": order_id, "status": "OPEN", "filled_price": price or trigger_price}
        except Exception as e:
            self.add_log(f"🔴 [ORDER ERROR] {e}")
            send_telegram_alert(f"🚨 <b>ORDER FAILURE</b>\nSymbol: {symbol}\nError: {e}")
            return None

    def cancel_zerodha_order(self, order_id):
        """Cancels an active open order on Zerodha KiteConnect."""
        if not self.kite or not order_id:
            return False
        try:
            time.sleep(0.1)
            self.kite.cancel_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id)
            self.add_log(f"🗑️ [CANCEL ORDER] Cancelled Zerodha order ID: {order_id}")
            return True
        except Exception as e:
            print(f"Notice/Error cancelling order {order_id}: {e}")
            return False

    def modify_zerodha_sl_order(self, order_id, trigger_price, quantity=None, price=0.0):
        """Modifies trigger price and/or quantity for an existing SL-L order on Zerodha."""
        if not self.kite or not order_id:
            return False
        try:
            time.sleep(0.1)
            params = {
                "variety": self.kite.VARIETY_REGULAR,
                "order_id": order_id,
                "order_type": "SL",
                "trigger_price": round(float(trigger_price), 2)
            }
            if price <= 0:
                params["price"] = round(float(trigger_price) * 1.05, 2)  # 5% buffer for Buy SL
            else:
                params["price"] = round(float(price), 2)
                
            if quantity is not None and int(quantity) > 0:
                params["quantity"] = int(quantity)
                
            self.kite.modify_order(**params)
            self.add_log(f"🔄 [MODIFY SL ORDER] Updated Order {order_id} -> Trigger: ₹{trigger_price:.2f} (Limit: ₹{params['price']:.2f})")
            return True
        except Exception as e:
            self.add_log(f"⚠️ [MODIFY SL NOTICE] Could not modify Order {order_id}: {e}")
            return False

    def place_zerodha_sl_order(self, symbol, quantity, trigger_price, price=0.0):
        """Places a hard Stop-Loss Limit (SL-L) order on Zerodha exchange to protect an open short position."""
        return self.place_zerodha_order(symbol, "BUY", quantity, order_type="SL", trigger_price=trigger_price, price=price)

    def panic_square_off(self, reason="User Emergency Panic Button"):
        """
        One-click emergency square-off: cancels all open exchange SL orders, places market buyback orders
        for all open PE/CE legs at Zerodha, saves trade records, and halts execution.
        """
        self.add_log(f"⚡ Executing Emergency Panic Square-Off: {reason}...")
        current_time = self.get_current_time()
        for opt_type in ['PE', 'CE']:
            state = self.strategy_state.get(opt_type, {})
            # Cancel any open exchange SL order first
            sl_order_id = state.get('sl_order_id')
            if sl_order_id:
                self.cancel_zerodha_order(sl_order_id)
                state['sl_order_id'] = None

            pos = state.get('position')
            if pos and pos != 'None':
                strike = state.get('strike')
                qty = self.lot_size * (2 if pos == 'SHORT_2_LOTS' else 1)
                cmp_price = state.get('price', 0.0)
                
                if self.mode == "LIVE_REAL" and self.kite and strike:
                    symbol = self.get_option_symbol(strike, opt_type, current_time)
                    res = self.place_zerodha_order(symbol, "BUY", qty, order_type="MARKET")
                    if res and res.get("filled_price", 0) > 0:
                        cmp_price = res["filled_price"]
                        
                pnl = (round(state.get('entry_price', 0.0), 2) - round(cmp_price, 2)) * qty
                self.realized_pnl += pnl
                
                trade_rec = {
                    "date": current_time.strftime('%Y-%m-%d'),
                    "type": opt_type,
                    "strike": strike,
                    "lots": 2 if pos == 'SHORT_2_LOTS' else 1,
                    "entry_time": state.get('entry_time') or "09:30:00",
                    "entry_price": round(state.get('entry_price', 0.0), 2),
                    "exit_time": current_time.strftime('%H:%M:%S'),
                    "exit_price": round(cmp_price, 2),
                    "sl": round(state.get('current_sl', 0.0), 2),
                    "day_high": round(state.get('day_high', 0.0), 2),
                    "pnl": round(pnl, 2),
                    "reason": f"Panic Exit ({reason})"
                }
                save_live_trade(trade_rec)
                
                state['position'] = None
                state['unrealized'] = 0.0
                state['above_entry_threshold'] = None
                self.add_log(f"✅ Emergency Closed {opt_type} {strike} position. PnL: ₹{pnl:.2f}")
                send_telegram_alert(f"✅ <b>EMERGENCY CLOSED</b>\n{opt_type} {strike}\nPnL: ₹{pnl:.2f}\nReason: {reason}")
                
        self.stop()
        self.persist_state()

    def persist_state(self):
        """Persists the live strategy state to disk so active positions survive server/process restarts."""
        try:
            current_time = self.get_current_time()
            target_date_str = current_time.strftime('%Y-%m-%d') if hasattr(current_time, 'strftime') else str(current_time)[:10]
            data = {
                "date": target_date_str,
                "updated_at": current_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(current_time, 'strftime') else str(current_time),
                "mode": self.mode,
                "status": self.status,
                "spot_price": self.spot_price,
                "realized_pnl": self.realized_pnl,
                "strategy_state": self.strategy_state,
                "logs": self.logs[-50:]
            }
            save_live_state(data)
        except Exception as e:
            print(f"Error in persist_state: {e}")

    def get_nearest_nifty_expiry(self, current_date):
        if hasattr(current_date, 'date'):
            current_date = current_date.date()
            
        if self.cached_expiry and self.cached_expiry >= current_date:
            return self.cached_expiry
            
        # Recalculate expiry dynamically
        # 1. Try Zerodha
        if getattr(self, 'kite_configured', False) and getattr(self, 'kite', None) is not None:
            try:
                insts = self.kite.instruments('NFO')
                nifty_insts = [i for i in insts if i['name'] == 'NIFTY' and i['expiry']]
                expiries = sorted(list(set(i['expiry'] for i in nifty_insts)))
                valid_expiries = [e for e in expiries if e >= current_date]
                if valid_expiries:
                    self.cached_expiry = valid_expiries[0]
                    self.add_log(f"Nearest Nifty expiry resolved from Zerodha: {self.cached_expiry}")
                    return self.cached_expiry
            except Exception as e:
                print(f"Error fetching expiry from Zerodha: {e}")
                
        # 2. Try NSE Scraper
        try:
            nse_data = self.fetch_nse_option_chain()
            if nse_data:
                records = nse_data.get('records', {})
                expiry_dates = records.get('expiryDates', [])
                if expiry_dates:
                    parsed_expiries = []
                    for estr in expiry_dates:
                        try:
                            edt = datetime.datetime.strptime(estr, "%d-%b-%Y").date()
                            parsed_expiries.append(edt)
                        except Exception:
                            pass
                    valid_expiries = sorted([e for e in parsed_expiries if e >= current_date])
                    if valid_expiries:
                        self.cached_expiry = valid_expiries[0]
                        self.add_log(f"Nearest Nifty expiry resolved from NSE: {self.cached_expiry}")
                        return self.cached_expiry
        except Exception as e:
            print(f"Error fetching expiry from NSE: {e}")
            
        # 3. Fallback to mathematical Tuesday expiry (with holiday adjustment)
        from nse_holidays import is_trading_holiday
        weekday = current_date.weekday()
        days_to_tuesday = (1 - weekday) % 7
        fallback_expiry = current_date + datetime.timedelta(days=days_to_tuesday)
        # If the calculated Tuesday is today but after market close (15:30), roll to next Tuesday
        if fallback_expiry == current_date and hasattr(current_date, 'hour') and current_date.time() >= datetime.time(15, 30):
            fallback_expiry += datetime.timedelta(days=7)
        # Roll back to previous business day if Tuesday is an exchange holiday or weekend
        while is_trading_holiday(fallback_expiry) or fallback_expiry.weekday() >= 5:
            fallback_expiry -= datetime.timedelta(days=1)
        self.cached_expiry = fallback_expiry
        self.add_log(f"Nearest Nifty expiry resolved via fallback (Tuesday cycle): {self.cached_expiry}")
        return self.cached_expiry

    def get_margin_metrics(self):
        """
        Calculates real-time Zerodha Margin metrics and Hedging benefits.
        """
        metrics = {
            "mode": self.mode,
            "is_real_account": (self.mode == "LIVE_REAL" and self.kite is not None),
            "utilized_margin": 0.0,
            "available_margin": round(self.capital, 2),
            "total_account_balance": round(self.capital + self.realized_pnl, 2),
            "max_strategy_required_margin": 0.0,
            "hedging_benefit_amount": 0.0,
            "utilization_pct": 0.0
        }
        try:
            span_per_lot = 85000.0 * (self.lot_size / 65.0)
            exp_per_lot = 25000.0 * (self.lot_size / 65.0)
            single_leg_margin = span_per_lot + exp_per_lot
            
            max_pe_span = span_per_lot * 2
            max_ce_span = span_per_lot * 2
            max_pe_exp = exp_per_lot * 2
            max_ce_exp = exp_per_lot * 2
            
            unhedged_max = (single_leg_margin * 4)
            hedged_max = max(max_pe_span, max_ce_span) + max_pe_exp + max_ce_exp
            metrics["max_strategy_required_margin"] = round(hedged_max, 2)
            metrics["hedging_benefit_amount"] = round(unhedged_max - hedged_max, 2)
            
            if self.mode == "LIVE_REAL" and self.kite is not None:
                try:
                    margins = self.kite.margins('equity')
                    if margins:
                        utilised = margins.get('utilised', {})
                        available = margins.get('available', {})
                        net = margins.get('net', 0.0)
                        
                        used = float(utilised.get('debits', 0.0) or (utilised.get('span', 0.0) + utilised.get('exposure', 0.0)))
                        avail_cash = float(available.get('live_balance', 0.0) or available.get('cash', 0.0))
                        
                        metrics["utilized_margin"] = round(used, 2)
                        metrics["available_margin"] = round(avail_cash, 2)
                        metrics["total_account_balance"] = round(net or (used + avail_cash), 2)
                        if metrics["total_account_balance"] > 0:
                            metrics["utilization_pct"] = round((used / metrics["total_account_balance"]) * 100.0, 1)
                        return metrics
                except Exception as e:
                    print(f"Error fetching live Zerodha margins: {e}")

            pe_pos = self.strategy_state.get('PE', {}).get('position')
            ce_pos = self.strategy_state.get('CE', {}).get('position')
            
            pe_lots = 2 if pe_pos == 'SHORT_2_LOTS' else (1 if pe_pos == 'SHORT_1_LOT' else 0)
            ce_lots = 2 if ce_pos == 'SHORT_2_LOTS' else (1 if ce_pos == 'SHORT_1_LOT' else 0)
            
            if self.kite is not None and (pe_lots > 0 or ce_lots > 0):
                try:
                    orders = []
                    current_time = self.get_current_time()
                    pe_strike = self.strategy_state.get('PE', {}).get('strike')
                    ce_strike = self.strategy_state.get('CE', {}).get('strike')
                    
                    if pe_lots > 0 and pe_strike:
                        symbol = self.get_option_symbol(pe_strike, 'PE', current_time)
                        if symbol:
                            orders.append({
                                "exchange": "NFO", "tradingsymbol": symbol.replace("NFO:", ""),
                                "transaction_type": "SELL", "variety": "regular",
                                "product": "NRML", "order_type": "MARKET", "quantity": pe_lots * self.lot_size
                            })
                    if ce_lots > 0 and ce_strike:
                        symbol = self.get_option_symbol(ce_strike, 'CE', current_time)
                        if symbol:
                            orders.append({
                                "exchange": "NFO", "tradingsymbol": symbol.replace("NFO:", ""),
                                "transaction_type": "SELL", "variety": "regular",
                                "product": "NRML", "order_type": "MARKET", "quantity": ce_lots * self.lot_size
                            })
                        
                    if orders:
                        bm = self.kite.basket_order_margins(orders)
                        if bm and isinstance(bm, dict):
                            final_m = bm.get('final', {}).get('total', 0.0)
                            initial_m = bm.get('initial', {}).get('total', 0.0)
                            if final_m > 0:
                                metrics["utilized_margin"] = round(final_m, 2)
                                metrics["hedging_benefit_amount"] = round(max(0.0, initial_m - final_m), 2)
                                metrics["available_margin"] = round(max(0.0, self.capital - final_m + self.realized_pnl), 2)
                                metrics["total_account_balance"] = round(self.capital + self.realized_pnl, 2)
                                metrics["utilization_pct"] = round((final_m / metrics["total_account_balance"]) * 100.0, 1)
                                return metrics
                except Exception as ex:
                    print(f"Basket margin API check fallback: {ex}")
                    
            if pe_lots > 0 and ce_lots > 0:
                pe_span = span_per_lot * pe_lots
                ce_span = span_per_lot * ce_lots
                pe_exp = exp_per_lot * pe_lots
                ce_exp = exp_per_lot * ce_lots
                
                utilized = max(pe_span, ce_span) + pe_exp + ce_exp
                unhedged = (pe_span + pe_exp) + (ce_span + ce_exp)
                metrics["hedging_benefit_amount"] = round(unhedged - utilized, 2)
            else:
                utilized = (pe_lots + ce_lots) * single_leg_margin
                
            metrics["utilized_margin"] = round(utilized, 2)
            metrics["total_account_balance"] = round(self.capital + self.realized_pnl, 2)
            metrics["available_margin"] = round(max(0.0, metrics["total_account_balance"] - utilized), 2)
            if metrics["total_account_balance"] > 0:
                metrics["utilization_pct"] = round((utilized / metrics["total_account_balance"]) * 100.0, 1)
        except Exception as e:
            print(f"Error in get_margin_metrics: {e}")
            
        return metrics

    def get_option_symbol(self, strike, opt_type, current_date):
        if hasattr(current_date, 'date'):
            current_date = current_date.date()
        
        expiry_date = self.get_nearest_nifty_expiry(current_date)
        
        is_monthly = (expiry_date + datetime.timedelta(days=7)).month != expiry_date.month
        yy = str(expiry_date.year)[2:]
        
        if is_monthly:
            months = ["", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
            month_str = months[expiry_date.month]
            symbol = f"NIFTY{yy}{month_str}{int(strike)}{opt_type}"
        else:
            month_map = {
                1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
                10: "O", 11: "N", 12: "D"
            }
            m_str = month_map[expiry_date.month]
            dd = f"{expiry_date.day:02d}"
            symbol = f"NIFTY{yy}{m_str}{dd}{int(strike)}{opt_type}"
            
        return f"NFO:{symbol}"

    def fetch_nse_option_chain(self):
        import requests
        if not getattr(self, 'nse_session', None):
            self.nse_session = requests.Session()
            self.nse_session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9,en-IN;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Referer': 'https://www.nseindia.com/option-chain',
                'X-Requested-With': 'XMLHttpRequest',
                'Connection': 'keep-alive',
            })
            # Initialize cookie session
            try:
                self.nse_session.get("https://www.nseindia.com", timeout=10)
                self.nse_session.get("https://www.nseindia.com", timeout=2.0)
                self.nse_session.get("https://www.nseindia.com/option-chain", timeout=2.0)
            except Exception as e:
                print(f"Error seeding NSE cookies: {e}")
                
        url = "https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=NIFTY"
        try:
            r = self.nse_session.get(url, timeout=2.0)
            if r.status_code == 200:
                data = r.json()
                if 'records' in data and data['records']:
                    return data
            elif r.status_code == 401:
                # Session expired/unauthorized, try re-seeding cookies once with fast timeout
                self.add_log("NSE Session unauthorized (401). Fast re-seeding cookies...")
                self.nse_session.get("https://www.nseindia.com", timeout=2.0)
                self.nse_session.get("https://www.nseindia.com/option-chain", timeout=2.0)
                r = self.nse_session.get(url, timeout=2.0)
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            print(f"Error fetching NSE option chain: {e}")
        return None

    def parse_nse_option_chain_data(self, data, strike, opt_type, expiry_date_str):
        records = data.get('records', {})
        data_list = records.get('data', [])
        target_expiry = expiry_date_str.upper()
        for item in data_list:
            item_expiry = str(item.get('expiryDate', '')).upper()
            if item.get('strikePrice') == strike and item_expiry == target_expiry:
                opt_data = item.get(opt_type, {})
                if opt_data:
                    return {
                        "last_price": opt_data.get('lastPrice', 0.0),
                        "high": opt_data.get('high', 0.0),
                        "low": opt_data.get('low', 0.0)
                    }
        return None

    def get_kite_client(self):
        config = load_strategy_config()
        api_key = config.get('kite_api_key', '')
        api_secret = config.get('kite_api_secret', '')
        user_id = config.get('kite_user_id', '')
        password = config.get('kite_password', '')
        totp_secret = config.get('kite_totp_secret', '')
        
        if not api_key or not api_secret or not user_id or not password:
            raise ValueError("Zerodha API Key, Secret, User ID, and Password must be configured.")
            
        today_str = datetime.date.today().isoformat()
        
        # Check if we have a valid token for today pushed by the Oracle VPS
        import os
        import json
        shared_path = os.path.join(os.path.expanduser("~"), ".kite_shared_token.json")
        access_token = None
        token_date = None
        if os.path.exists(shared_path):
            try:
                with open(shared_path, 'r') as f:
                    data = json.load(f)
                    access_token = data.get("access_token")
                    token_date = data.get("token_date") or data.get("date")
            except Exception:
                pass

        # ── Self-Healing Fallback: Pull directly from Oracle VPS API if token missing or outdated ──
        if not (access_token and token_date == today_str):
            try:
                import urllib.request
                import base64
                oracle_url = os.getenv("ORACLE_SHARED_TOKEN_URL", "http://127.0.0.1/api/shared-token")
                req = urllib.request.Request(oracle_url)
                auth_user = os.getenv("BASIC_AUTH_USER", "admin")
                auth_pass = os.getenv("BASIC_AUTH_PASS", "admin")
                auth_str = f"{auth_user}:{auth_pass}".encode("utf-8")
                creds = base64.b64encode(auth_str).decode("utf-8")
                req.add_header("Authorization", f"Basic {creds}")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data.get("ok") and data.get("date") == today_str and data.get("access_token"):
                            access_token = data["access_token"]
                            token_date = data["date"]
                            with open(shared_path, 'w') as f:
                                json.dump({"access_token": access_token, "token_date": token_date, "source": "self-healed-pull"}, f, indent=2)
                            self.add_log("⚡ [Self-Healing] Successfully fetched today's shared token directly from Oracle VPS API!")
            except Exception as pull_err:
                pass
                
        from kiteconnect import KiteConnect
        
        if access_token and token_date == today_str:
            self.add_log("Reusing valid Zerodha access token from Oracle VPS for today.")
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(access_token)
            if hasattr(kite, 'reqsession') and hasattr(kite.reqsession, 'timeout'):
                kite.reqsession.timeout = 15
            try:
                # Test session validity
                kite.profile()
                return kite
            except Exception as e:
                self.add_log(f"Stored access token is invalid: {e}. Waiting for Oracle VPS to push fresh token.")

        # ── Oracle-VPS-Only Token Mode ──────────────────────────────────────
        # The token is generated exclusively at 08:45 IST every weekday by
        # token_refresher.py on the Oracle VPS.
        # We NEVER attempt local browser logins on GCP VPS to prevent session invalidation.
        # ───────────────────────────────────────────────────────────────────
        self.add_log(f"[TOKEN] Waiting for today's token ({today_str}) from Oracle VPS in ~/.kite_shared_token.json...")
        return None


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

    def add_log(self, message):
        timestamp = self.get_current_time().strftime("%H:%M:%S")
        log_str = f"[{timestamp}] {message}"
        self.logs.append(log_str)
        # Keep logs list bounded
        if len(self.logs) > 200:
            self.logs.pop(0)
        self.broadcast({"event": "log", "log": log_str})
        print(f"LIVE LOG: {log_str}")

    def get_current_time(self):
        """Returns the current timezone-aware IST time or mock time."""
        ist = pytz.timezone('Asia/Kolkata')
        if self.mode == "MOCK" and self.simulated_dt:
            return self.simulated_dt
        return datetime.datetime.now(ist)

    def get_live_spot_price(self):
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{self.symbol}?interval=1m&range=1d"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        try:
            import requests
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                data = r.json()
                meta = data['chart']['result'][0]['meta']
                return float(meta['regularMarketPrice'])
        except Exception as e:
            print(f"Error fetching live spot price: {e}")
        return None

    def get_live_broker_margins(self):
        if not self.kite:
            return None
        try:
            m = self.kite.margins("equity")
            avail = m.get("available", {})
            utilised = m.get("utilised", {})
            
            cash = float(avail.get("live_balance") or avail.get("cash") or 0.0)
            used = float(utilised.get("debits") or (float(utilised.get("span", 0)) + float(utilised.get("exposure", 0))))
            return {
                "available": cash,
                "utilised": used
            }
        except Exception as e:
            print(f"Error fetching live account margins: {e}")
            return None

    def calculate_margins(self, current_time=None):
        # If no active positions, margin is 0
        pe_pos = self.strategy_state['PE']['position']
        ce_pos = self.strategy_state['CE']['position']
        
        if (not pe_pos or pe_pos == 'None') and (not ce_pos or ce_pos == 'None'):
            return 0.0, 0.0
            
        lot_size = self.lot_size
        
        # If Kite is active, try to fetch real-time margins from Zerodha API
        if self.kite:
            try:
                if current_time is None:
                    if self.mode == "MOCK" and self.simulated_dt:
                        current_time = self.simulated_dt
                    else:
                        ist = pytz.timezone('Asia/Kolkata')
                        current_time = datetime.datetime.now(ist)

                params = []
                if pe_pos and pe_pos != 'None':
                    pe_qty = lot_size * (2 if pe_pos == 'SHORT_2_LOTS' else 1)
                    pe_strike = self.strategy_state['PE']['strike']
                    symbol = self.get_option_symbol(pe_strike, 'PE', current_time) if pe_strike else None
                    if symbol:
                        if ":" in symbol:
                            symbol = symbol.split(":")[1]
                        params.append({
                            "exchange": "NFO",
                            "tradingsymbol": symbol,
                            "transaction_type": "SELL",
                            "variety": "regular",
                            "product": "NRML",
                            "order_type": "MARKET",
                            "quantity": pe_qty,
                            "price": 0,
                            "trigger_price": 0
                        })
                    
                if ce_pos and ce_pos != 'None':
                    ce_qty = lot_size * (2 if ce_pos == 'SHORT_2_LOTS' else 1)
                    ce_strike = self.strategy_state['CE']['strike']
                    symbol = self.get_option_symbol(ce_strike, 'CE', current_time) if ce_strike else None
                    if symbol:
                        if ":" in symbol:
                            symbol = symbol.split(":")[1]
                        params.append({
                            "exchange": "NFO",
                            "tradingsymbol": symbol,
                            "transaction_type": "SELL",
                            "variety": "regular",
                            "product": "NRML",
                            "order_type": "MARKET",
                            "quantity": ce_qty,
                            "price": 0,
                            "trigger_price": 0
                        })
                    
                if params:
                    # Call Zerodha basket margins API to get combined margin with strangle benefit
                    res = self.kite.basket_order_margins(params)
                    margin_required = res.get("initial", {}).get("total", 0.0)
                    if margin_required > 0:
                        return round(margin_required, 2), round(margin_required * 1.20, 2)
            except Exception as e:
                print(f"Error fetching margins from Zerodha API: {e}. Using fallback calculation.")
                
        # Fallback margin estimation logic:
        # 1 lot short of Nifty option is approx ₹1,00,000 margin
        pe_lots = 0
        ce_lots = 0
        if pe_pos == 'SHORT_2_LOTS': pe_lots = 2
        elif pe_pos == 'SHORT_1_LOT': pe_lots = 1
        if ce_pos == 'SHORT_2_LOTS': ce_lots = 2
        elif ce_pos == 'SHORT_1_LOT': ce_lots = 1
        
        total_lots = pe_lots + ce_lots
        if total_lots == 0:
            return 0.0, 0.0
            
        # Strangle benefit: if both sides are short, apply a 25% discount
        if pe_lots > 0 and ce_lots > 0:
            margin_required = total_lots * 100000.0 * 0.75
        else:
            margin_required = total_lots * 100000.0
            
        return round(margin_required, 2), round(margin_required * 1.20, 2)

    def start(self, mode, capital, strategy_type, strike_offset_pe, strike_offset_ce, 
              entry_discount, lot_1_target, max_reentries, lot_size, iv, r):
        with self.lock:
            if self.status == "RUNNING" or self.status == "FEED_DISRUPTED":
                self._stop_internal()
                
            self.mode = mode
            self.capital = float(capital)
            self.realized_pnl = 0.0
            self.strategy_type = strategy_type
            self.strike_offset_pe = int(strike_offset_pe)
            self.strike_offset_ce = int(strike_offset_ce)
            self.entry_discount = float(entry_discount) / 100.0
            self.lot_1_target_pct = float(lot_1_target) / 100.0
            self.max_reentries = int(max_reentries)
            self.lot_size = int(lot_size)
            self.iv = float(iv) / 100.0
            self.r = float(r) / 100.0
            
            # Load Kite settings from saved config.json
            config = load_strategy_config()
            # Determine if Zerodha is configured
            api_key = config.get('kite_api_key', '')
            api_secret = config.get('kite_api_secret', '')
            user_id = config.get('kite_user_id', '')
            password = config.get('kite_password', '')
            self.kite_configured = bool(api_key and api_secret and user_id and password)
            self.kite = None
            self.cached_expiry = None
            self.feed_source = 'MOCK' if self.mode == 'MOCK' else 'PENDING'
            self.disruption_counter = 0
            self.feed_disrupted = False
            self.last_tick_time = None
            
            self.logs = []
            
            # Initialize Strategy State
            self.strategy_state = {
                'PE': {'day_high': 0.0, 'entry_count': 0, 'position': None, 'entry_price': 0.0, 'current_sl': 0.0, 'target': 0.0, 'min_price': float('inf'), 'unrealized': 0.0, 'strike': None, 'price': 0.0, 'entry_time': None, 'above_entry_threshold': None, 'sl_order_id': None},
                'CE': {'day_high': 0.0, 'entry_count': 0, 'position': None, 'entry_price': 0.0, 'current_sl': 0.0, 'target': 0.0, 'min_price': float('inf'), 'unrealized': 0.0, 'strike': None, 'price': 0.0, 'entry_time': None, 'above_entry_threshold': None, 'sl_order_id': None}
            }
            
            # Setup simulated clock for Mock Mode
            if self.mode == "MOCK":
                ist = pytz.timezone('Asia/Kolkata')
                now_ist = datetime.datetime.now(ist)
                self.simulated_dt = now_ist.replace(hour=9, minute=25, second=0, microsecond=0)
                self.spot_price = 23200.0
                self.add_log(f"Starting Mock Simulator at {self.simulated_dt.strftime('%Y-%m-%d %H:%M:%S')} IST (Nifty Spot: {self.spot_price:.2f})")
            else:
                # Live Trading Modes: verify market is open today
                is_trading_day, day_reason = get_market_day_status(self.get_current_time())
                if not is_trading_day:
                    self.status = "STOPPED"
                    msg = f"Cannot start live session: Market is closed today ({day_reason})."
                    self.add_log(msg)
                    self.broadcast({"event": "status", "status": "STOPPED", "log": msg})
                    self.broadcast({"event": "market_status", "status": f"CLOSED ({day_reason})", "reason": day_reason})
                    self.persist_state()
                    return
                self.simulated_dt = None
                self.add_log("Initializing Live Tracking Session...")
                
            self.session_id += 1
            current_session = self.session_id
            self.status = "RUNNING"
            self.thread = threading.Thread(target=self._run_loop, args=(current_session,), daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self._stop_internal()
        if self.thread and self.thread.is_alive() and threading.current_thread() != self.thread:
            self.thread.join(timeout=2.0)

    def _stop_internal(self):
        if self.status in ["RUNNING", "FEED_DISRUPTED"]:
            self.status = "STOPPED"
            self.add_log("Session Stopped.")
            self.broadcast({"event": "status", "status": "STOPPED", "log": "Session Stopped."})
            
            # Square off positions in logs
            for opt_type in ['PE', 'CE']:
                state = self.strategy_state.get(opt_type, {})
                if state.get('position') is not None:
                    self.add_log(f"Square-off: Closed {opt_type} position on stop.")
            
            if self.kws:
                try:
                    self.add_log("Disconnecting WebSocket...")
                    self.kws.close()
                except Exception:
                    pass
                self.kws = None
            self.websocket_active = False
            self.websocket_tokens = {}
            self.spot_token = None
            
            self.simulated_dt = None
            self.persist_state()

    def _sync_session(self, current_time):
        """
        Downloads today's Nifty 1-minute spot bars and replays the strategy
        up to the current time to catch up on positions, day highs, and PnL.
        """
        self.add_log("Synchronizing session state with today's intraday spot data...")
        if current_time.time() < datetime.time(9, 30):
            self.add_log("Current time is before 09:30 AM. Ticks will be processed in real-time. Strike selection will occur at 09:30 AM.")
            return
        try:
            df = yf.download(tickers=self.symbol, period='1d', interval='1m')
            if df.empty:
                self.add_log("No historical spot data available for today yet. Waiting for market ticks.")
                return
            
            # Flatten columns
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
                
            df = df.reset_index()
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            
            try:
                df['Datetime'] = df['Datetime'].dt.tz_convert('Asia/Kolkata')
            except TypeError:
                if df['Datetime'].dt.tz is None:
                    df['Datetime'] = df['Datetime'].dt.localize('UTC').dt.tz_convert('Asia/Kolkata')
                    
            df = df.sort_values('Datetime').reset_index(drop=True)
            
            # Filter up to current time
            df = df[df['Datetime'] <= current_time]
            if df.empty:
                return
                
            # Find 09:30 AM close
            bars_before_930 = df[df['Datetime'].dt.time <= datetime.time(9, 30)]
            if bars_before_930.empty:
                self.add_log("Time is before 09:30 AM. Ticks will be processed in real-time.")
                return
                
            spot_930 = bars_before_930.iloc[-1]['Close']
            pe_strike = round_to_nearest_strike(spot_930 + self.strike_offset_pe)
            ce_strike = round_to_nearest_strike(spot_930 + self.strike_offset_ce)
            
            self.strategy_state['PE']['strike'] = pe_strike
            self.strategy_state['CE']['strike'] = ce_strike
            self.add_log(f"Strikes Selected at 09:30 AM close ({spot_930:.2f}): PE {pe_strike} | CE {ce_strike}")
            
            modes = []
            if self.strategy_type in ['PE_SELL', 'BOTH']:
                modes.append('PE')
            if self.strategy_type in ['CE_SELL', 'BOTH']:
                modes.append('CE')
                
            if self.mode == "MOCK":
                for opt_type in modes:
                    state = self.strategy_state[opt_type]
                    state['above_entry_threshold'] = None
                    strike = state['strike']
                    
                    # Replay each bar (MOCK simulation only)
                    for idx, row in df.iterrows():
                        bar_dt = row['Datetime']
                        bar_time = bar_dt.time()
                        t_years = get_time_to_expiry_years(bar_dt, self.get_nearest_nifty_expiry(bar_dt))
                        
                        # Option pricing
                        if opt_type == 'CE':
                            o_open = black_scholes(row['Open'], strike, t_years, self.r, self.iv, 'CE')
                            o_high = black_scholes(row['High'], strike, t_years, self.r, self.iv, 'CE')
                            o_low = black_scholes(row['Low'], strike, t_years, self.r, self.iv, 'CE')
                            o_close = black_scholes(row['Close'], strike, t_years, self.r, self.iv, 'CE')
                        else:
                            o_open = black_scholes(row['Open'], strike, t_years, self.r, self.iv, 'PE')
                            o_high = black_scholes(row['Low'], strike, t_years, self.r, self.iv, 'PE')
                            o_low = black_scholes(row['High'], strike, t_years, self.r, self.iv, 'PE')
                            o_close = black_scholes(row['Close'], strike, t_years, self.r, self.iv, 'PE')
                            
                        # 1. Day High Tracking before 09:30
                        if bar_time <= datetime.time(9, 30):
                            if o_high > state['day_high']:
                                state['day_high'] = o_high
                            state['price'] = o_close
                            continue
                            
                        # 2. Square-off at 15:15
                        if bar_time >= datetime.time(15, 15):
                            if state['position'] is not None:
                                pnl = 0.0
                                if state['position'] == 'SHORT_2_LOTS':
                                    pnl = (round(state['entry_price'], 2) - round(o_close, 2)) * 2 * self.lot_size
                                elif state['position'] == 'SHORT_1_LOT':
                                    pnl = (round(state['entry_price'], 2) - round(o_close, 2)) * self.lot_size
                                self.realized_pnl += pnl
                                state['position'] = None
                                state['unrealized'] = 0.0
                            continue
                            
                        # 3. Position Logic
                        if state['position'] is None:
                            if o_high > state['day_high']:
                                state['day_high'] = o_high
                            target_entry = state['day_high'] * (1.0 - self.entry_discount)
                            
                            # Initialize or track above_entry_threshold
                            if state.get('above_entry_threshold') is None:
                                state['above_entry_threshold'] = (o_open > target_entry)
                            if o_high > target_entry:
                                state['above_entry_threshold'] = True
                                
                            if state['entry_count'] < self.max_reentries:
                                if state.get('above_entry_threshold', False) and o_low <= target_entry:
                                    state['position'] = 'SHORT_2_LOTS'
                                    state['entry_price'] = target_entry
                                    state['entry_count'] += 1
                                    state['current_sl'] = state['day_high']
                                    state['target'] = state['day_high'] * (1.0 - self.lot_1_target_pct)
                                    state['min_price'] = o_low
                                    state['entry_time'] = bar_dt.strftime('%H:%M:%S')
                                    state['above_entry_threshold'] = None
                                    
                        elif state['position'] == 'SHORT_2_LOTS':
                            state['min_price'] = min(state['min_price'], o_low)
                            state['unrealized'] = (state['entry_price'] - o_close) * 2 * self.lot_size
                            
                            # Once the premium reaches X-30%, SL should shift to X-10% for Lot 1 & Lot 2
                            if state['min_price'] <= state['day_high'] * 0.7:
                                state['current_sl'] = state['day_high'] * 0.9
                            else:
                                state['current_sl'] = state['day_high']
                                
                            if o_high >= state['current_sl']:
                                pnl = (round(state['entry_price'], 2) - round(state['current_sl'], 2)) * 2 * self.lot_size
                                self.realized_pnl += pnl
                                state['position'] = None
                                state['unrealized'] = 0.0
                                state['day_high'] = max(state['day_high'], o_high)
                                state['above_entry_threshold'] = None
                            elif o_low <= state['target']:
                                pnl = (round(state['entry_price'], 2) - round(state['target'], 2)) * self.lot_size
                                self.realized_pnl += pnl
                                state['position'] = 'SHORT_1_LOT'
                                state['unrealized'] = (state['entry_price'] - o_close) * self.lot_size
                                state['current_sl'] = get_trailed_sl(state['min_price'], state['day_high'])
                                
                        elif state['position'] == 'SHORT_1_LOT':
                            state['min_price'] = min(state['min_price'], o_low)
                            state['current_sl'] = get_trailed_sl(state['min_price'], state['day_high'])
                            state['unrealized'] = (state['entry_price'] - o_close) * self.lot_size
                            
                            if o_high >= state['current_sl']:
                                pnl = (round(state['entry_price'], 2) - round(state['current_sl'], 2)) * self.lot_size
                                self.realized_pnl += pnl
                                state['position'] = None
                                state['unrealized'] = 0.0
                                state['day_high'] = max(state['day_high'], o_high)
                                state['above_entry_threshold'] = None
                                
                        state['price'] = o_close
            else:
                # LIVE / LIVE_REAL Mode: Sourced 100% from genuine Zerodha Exchange Quotes
                if self.kite:
                    self.add_log("[Sync] Fetching official exchange Day High and Quotes from Zerodha...")
                    symbols_to_fetch = []
                    opt_types = []
                    for opt_type in modes:
                        state = self.strategy_state[opt_type]
                        strike = state['strike']
                        if strike:
                            opt_symbol = self.get_option_symbol(strike, opt_type, current_time)
                            symbols_to_fetch.append(opt_symbol)
                            opt_types.append(opt_type)
                            
                    if symbols_to_fetch:
                        try:
                            quotes = self.kite.quote(symbols_to_fetch)
                            for opt_type in opt_types:
                                state = self.strategy_state[opt_type]
                                strike = state['strike']
                                opt_symbol = self.get_option_symbol(strike, opt_type, current_time)
                                if opt_symbol in quotes:
                                    quote_data = quotes[opt_symbol]
                                    z_high = float(quote_data.get("ohlc", {}).get("high", 0.0))
                                    z_price = float(quote_data.get("last_price", 0.0))
                                    z_low = float(quote_data.get("ohlc", {}).get("low", z_price))
                                    
                                    state['day_high'] = z_high
                                    state['price'] = z_price
                                    state['min_price'] = z_low
                                    
                                    target_entry = state['day_high'] * (1.0 - self.entry_discount)
                                    if state['price'] > 0:
                                        state['above_entry_threshold'] = (state['price'] > target_entry)
                                        
                                    self.add_log(f"[Sync] {opt_type} {strike} Official Zerodha High (X): {state['day_high']:.2f} | LTP: {state['price']:.2f}")
                        except Exception as ex:
                            self.add_log(f"⚠️ [Sync] Error fetching Zerodha quotes: {ex}")
                else:
                    self.add_log("⚠️ [Sync] Zerodha not connected. Cannot fetch exchange quotes. Awaiting token...")
            
            # 3.5 Restore active in-flight positions across service restarts
            target_date_str = current_time.strftime('%Y-%m-%d') if hasattr(current_time, 'strftime') else str(current_time)[:10]
            saved_state = load_live_state()
            restored_legs = set()
            if saved_state and saved_state.get('date') == target_date_str:
                for opt_type in ['PE', 'CE']:
                    s_leg = saved_state.get('strategy_state', {}).get(opt_type, {})
                    if s_leg.get('position'):
                        leg = self.strategy_state[opt_type]
                        leg['position'] = s_leg['position']
                        leg['entry_price'] = s_leg.get('entry_price', 0.0)
                        leg['entry_time'] = s_leg.get('entry_time')
                        leg['current_sl'] = s_leg.get('current_sl', 0.0)
                        leg['target'] = s_leg.get('target', 0.0)
                        leg['min_price'] = s_leg.get('min_price', float('inf'))
                        leg['entry_count'] = s_leg.get('entry_count', 1)
                        if s_leg.get('day_high', 0.0) > leg.get('day_high', 0.0):
                            leg['day_high'] = s_leg['day_high']
                        restored_legs.add(opt_type)
                        self.add_log(f"[Sync] Restored in-flight {opt_type} position: {leg['position']} @ ₹{leg['entry_price']:.2f} (Entry: {leg['entry_time']})")
                if saved_state.get('logs') and len(self.logs) <= 5:
                    self.logs = saved_state.get('logs', []) + self.logs
            
            # 4. Reconstruct state using actual trades from live_trades.json
            try:
                trades = load_live_trades()
                today_trades = [t for t in trades if t.get('date') == target_date_str]
                
                if today_trades:
                    # Overwrite realized PnL with actual sum
                    self.realized_pnl = sum(t.get('pnl', 0.0) for t in today_trades)
                    self.add_log(f"[Sync] Initialized realized PnL to actual trade log sum: ₹{self.realized_pnl:.2f}")
                    
                    for opt_type in ['PE', 'CE']:
                        opt_trades = [t for t in today_trades if t.get('type') == opt_type]
                        if opt_trades:
                            state = self.strategy_state[opt_type]
                            last_trade = opt_trades[-1]
                            reason = last_trade.get('reason', '')
                            if 'Target (Lot 1)' in reason:
                                state['position'] = 'SHORT_1_LOT'
                                state['entry_price'] = last_trade.get('entry_price', 0.0)
                                state['entry_time'] = last_trade.get('entry_time')
                                if state.get('day_high', 0.0) == 0.0:
                                    state['day_high'] = last_trade.get('day_high', 0.0)
                                state['current_sl'] = last_trade.get('sl', 0.0)
                                state['target'] = last_trade.get('target', 0.0)
                                state['min_price'] = min(state.get('min_price', float('inf')), last_trade.get('exit_price', 0.0))
                                self.add_log(f"[Sync] Reconstructed open {opt_type} position: SHORT_1_LOT (Entry: {state['entry_price']})")
                            elif opt_type not in restored_legs:
                                state['position'] = None
                                state['entry_price'] = 0.0
                                state['entry_time'] = None
                                state['current_sl'] = 0.0
                                state['target'] = 0.0
                                state['min_price'] = float('inf')
                                state['above_entry_threshold'] = None
                                self.add_log(f"[Sync] Reconstructed open {opt_type} position: None (Closed via {reason})")
                                
                            # Sync the entry count (unique entry times)
                            unique_entries = len(set(t.get('entry_time') for t in opt_trades))
                            state['entry_count'] = max(state.get('entry_count', 1), unique_entries)
            except Exception as e:
                self.add_log(f"[Sync] Error reading live_trades.json: {e}")

                # In LIVE_REAL mode, query Zerodha live positions & orders to reconcile broker-truth
                if self.mode == "LIVE_REAL" and self.kite:
                    try:
                        self.add_log("[Sync] Reconciling live positions and open SL orders with Zerodha broker...")
                        positions_resp = self.kite.positions()
                        net_positions = positions_resp.get("net", [])
                        
                        open_orders = []
                        try:
                            all_orders = self.kite.orders()
                            open_orders = [o for o in all_orders if o.get("status") in ["OPEN", "TRIGGER PENDING"]]
                        except Exception as ex_ord:
                            print(f"Notice fetching open orders: {ex_ord}")

                        for opt_type in ['PE', 'CE']:
                            state = self.strategy_state[opt_type]
                            strike = state.get('strike')
                            if not strike:
                                continue
                            symbol = self.get_option_symbol(strike, opt_type, current_time)
                            tsym = symbol.split(":")[1] if ":" in symbol else symbol
                            
                            broker_match = next((p for p in net_positions if p.get("tradingsymbol") == tsym), None)
                            if broker_match:
                                net_qty = broker_match.get("quantity", 0)
                                if net_qty < 0:  # Short option position
                                    short_lots = abs(net_qty) // self.lot_size
                                    state['position'] = 'SHORT_2_LOTS' if short_lots >= 2 else 'SHORT_1_LOT'
                                    avg_p = float(broker_match.get("average_price") or broker_match.get("buy_price") or state.get('entry_price', 0.0))
                                    if avg_p > 0:
                                        state['entry_price'] = avg_p
                                    self.add_log(f"🎯 [Broker Reconciled] {opt_type} {strike}: Confirmed {state['position']} on Zerodha @ ₹{state['entry_price']:.2f}")
                                    
                                    # Match pending SL order
                                    sl_match = next((o for o in open_orders if o.get("tradingsymbol") == tsym and o.get("transaction_type") == "BUY" and o.get("order_type") == "SL"), None)
                                    if sl_match:
                                        state['sl_order_id'] = sl_match.get("order_id")
                                        state['current_sl'] = float(sl_match.get("trigger_price", state.get('current_sl', 0.0)))
                                        self.add_log(f"🛡️ [Broker Reconciled] Found active SL-L order {state['sl_order_id']} for {opt_type} {strike} (Trigger: ₹{state['current_sl']:.2f})")
                                elif net_qty == 0 and state['position'] is not None:
                                    self.add_log(f"ℹ️ [Broker Reconciled] {opt_type} {strike} is flat on Zerodha. Clearing local state.")
                                    state['position'] = None
                                    state['sl_order_id'] = None
                    except Exception as e:
                        self.add_log(f"⚠️ [Broker Position Sync Warning] {e}")

                # After updating day highs, re-calculate SL and target for any active positions
                for opt_type in ['PE', 'CE']:
                    state = self.strategy_state[opt_type]
                    if state['position'] == 'SHORT_1_LOT':
                        state['current_sl'] = get_trailed_sl(state['min_price'], state['day_high'])
                    elif state['position'] == 'SHORT_2_LOTS':
                        state['target'] = state['day_high'] * (1.0 - self.lot_1_target_pct)
                        if state['min_price'] <= state['day_high'] * 0.7:
                            state['current_sl'] = state['day_high'] * 0.9
                        else:
                            state['current_sl'] = state['day_high']
            except Exception as e:
                self.add_log(f"Error re-syncing positions from trade log: {e}")

            if self.mode in ["LIVE", "LIVE_REAL"]:
                self._resolve_and_subscribe_tokens(current_time)

            self.add_log(f"Synchronization complete. PE Strike: {pe_strike} (Day High: {self.strategy_state['PE']['day_high']:.2f}, Pos: {self.strategy_state['PE']['position']}) | CE Strike: {ce_strike} (Day High: {self.strategy_state['CE']['day_high']:.2f}, Pos: {self.strategy_state['CE']['position']})")
        except Exception as e:
            self.add_log(f"Error during synchronization: {str(e)}")

    def _evaluate_strategy_for_leg(self, opt_type, o_price, o_high, o_low, current_time):
        state = self.strategy_state[opt_type]
        strike = state['strike']
        if strike is None:
            return
            
        t_time = current_time.time()
        
        # 15:15 Square-off
        if t_time >= datetime.time(15, 15):
            if state['position'] is not None:
                pnl = 0.0
                lots_closed = 2 if state['position'] == 'SHORT_2_LOTS' else 1
                exit_price = o_price
                
                # Cancel open exchange SL order first
                if state.get('sl_order_id'):
                    self.cancel_zerodha_order(state['sl_order_id'])
                    state['sl_order_id'] = None

                if self.mode == "LIVE_REAL" and self.kite:
                    symbol = self.get_option_symbol(strike, opt_type, current_time)
                    res = self.place_zerodha_order(symbol, "BUY", lots_closed * self.lot_size, order_type="MARKET")
                    if res and res.get("filled_price", 0) > 0:
                        exit_price = res["filled_price"]
                        
                if state['position'] == 'SHORT_2_LOTS':
                    pnl = (round(state['entry_price'], 2) - round(exit_price, 2)) * 2 * self.lot_size
                    self.add_log(f"[SQUARE-OFF] Closed {opt_type} {strike} Short 2 Lots at {exit_price:.2f} (PnL: +₹{pnl:.2f})")
                elif state['position'] == 'SHORT_1_LOT':
                    pnl = (round(state['entry_price'], 2) - round(exit_price, 2)) * self.lot_size
                    self.add_log(f"[SQUARE-OFF] Closed {opt_type} {strike} Short 1 Lot at {exit_price:.2f} (PnL: +₹{pnl:.2f})")
                self.realized_pnl += pnl
                
                trade_rec = {
                    "date": current_time.strftime('%Y-%m-%d'),
                    "type": opt_type,
                    "strike": strike,
                    "lots": lots_closed,
                    "entry_time": state['entry_time'] or "09:30:00",
                    "entry_price": round(state['entry_price'], 2),
                    "exit_time": current_time.strftime('%H:%M:%S'),
                    "exit_price": round(exit_price, 2),
                    "sl": round(state['current_sl'], 2),
                    "day_high": round(state['day_high'], 2),
                    "pnl": round(pnl, 2),
                    "reason": "Square-off (15:15)"
                }
                save_live_trade(trade_rec)
                send_telegram_alert(f"⏰ <b>15:15 SQUARE-OFF</b>\n{opt_type} {strike} ({lots_closed} Lots)\nExit Price: ₹{exit_price:.2f}\nPnL: ₹{pnl:.2f}")
                
                state['position'] = None
                state['unrealized'] = 0.0
                state['above_entry_threshold'] = None
                state['sl_order_id'] = None
            return
            
        # Position Logic
        if state['position'] is None:
            if getattr(self, 'feed_disrupted', False) or getattr(self, 'circuit_breaker_tripped', False):
                return
                
            target_entry = state['day_high'] * (1.0 - self.entry_discount)
            
            if state.get('above_entry_threshold') is None:
                if o_price > 0:
                    state['above_entry_threshold'] = (o_price > target_entry)
            elif o_price > target_entry:
                state['above_entry_threshold'] = True
                
            if state['entry_count'] < self.max_reentries:
                if o_price <= target_entry and o_price > 0 and state.get('above_entry_threshold', False):
                    entry_fill_price = o_price
                    
                    if self.mode == "LIVE_REAL" and self.kite:
                        # Pre-trade Margin Verification
                        margin_req, _ = self.calculate_margins(current_time)
                        margins_info = self.get_live_broker_margins()
                        avail_cash = margins_info.get("available", 0.0) if margins_info else self.capital
                        if avail_cash < margin_req * 1.15:
                            self.add_log(f"🔴 Pre-trade Margin Check FAILED: Available Cash ₹{avail_cash:.2f} < Required ₹{margin_req * 1.15:.2f} (15% buffer). Entry Suppressed.")
                            send_telegram_alert(f"🔴 <b>MARGIN CHECK FAILED</b>\nAvail: ₹{avail_cash:.2f} | Req: ₹{margin_req * 1.15:.2f}\nEntry Skipped.")
                            return
                            
                        symbol = self.get_option_symbol(strike, opt_type, current_time)
                        res = self.place_zerodha_order(symbol, "SELL", 2 * self.lot_size, order_type="MARKET")
                        if res and res.get("status") == "REJECTED":
                            self.add_log(f"🔴 Real Entry Order REJECTED. Position skipped.")
                            return
                        if res and res.get("filled_price", 0) > 0:
                            entry_fill_price = res["filled_price"]
                            
                    state['position'] = 'SHORT_2_LOTS'
                    state['entry_price'] = entry_fill_price
                    state['entry_count'] += 1
                    state['current_sl'] = state['day_high']
                    state['target'] = state['day_high'] * (1.0 - self.lot_1_target_pct)
                    state['min_price'] = entry_fill_price
                    state['entry_time'] = current_time.strftime('%H:%M:%S')
                    state['above_entry_threshold'] = None
                    state['sl_order_id'] = None

                    # Place immediate exchange-side Stop-Loss Limit (SL-L) order in LIVE_REAL mode
                    if self.mode == "LIVE_REAL" and self.kite:
                        symbol = self.get_option_symbol(strike, opt_type, current_time)
                        sl_res = self.place_zerodha_sl_order(symbol, 2 * self.lot_size, trigger_price=state['current_sl'])
                        if sl_res and sl_res.get("order_id"):
                            state['sl_order_id'] = sl_res.get("order_id")
                            self.add_log(f"🛡️ [HARD SL ARMED] {opt_type} {strike} SL-L Order {state['sl_order_id']} active on Zerodha exchange @ ₹{state['current_sl']:.2f}")

                    self.add_log(f"[ENTRY #{state['entry_count']}] Shorted 2 Lots of {opt_type} {strike} at {entry_fill_price:.2f} (SL: {state['current_sl']:.2f} | Target: {state['target']:.2f})")
                    send_telegram_alert(f"🚀 <b>ENTRY #{state['entry_count']} TRIGGERED</b>\n{opt_type} {strike} Short 2 Lots @ ₹{entry_fill_price:.2f}\nSL: ₹{state['current_sl']:.2f} | Target: ₹{state['target']:.2f}")
                    
        elif state['position'] == 'SHORT_2_LOTS':
            state['min_price'] = min(state['min_price'], o_price)
            state['unrealized'] = (state['entry_price'] - o_price) * 2 * self.lot_size
            
            old_sl = state['current_sl']
            if state['min_price'] <= state['day_high'] * 0.7:
                new_sl = state['day_high'] * 0.9
            else:
                new_sl = state['day_high']

            if new_sl != old_sl:
                state['current_sl'] = new_sl
                if self.mode == "LIVE_REAL" and self.kite and state.get('sl_order_id'):
                    self.modify_zerodha_sl_order(state['sl_order_id'], trigger_price=new_sl, quantity=2 * self.lot_size)
                
            # Check SL
            if o_price >= state['current_sl']:
                exit_fill_price = state['current_sl']
                if self.mode == "LIVE_REAL" and self.kite:
                    symbol = self.get_option_symbol(strike, opt_type, current_time)
                    # If exchange SL order exists, check if it was triggered or execute fallback market buy
                    if not state.get('sl_order_id'):
                        res = self.place_zerodha_order(symbol, "BUY", 2 * self.lot_size, order_type="MARKET")
                        if res and res.get("filled_price", 0) > 0:
                            exit_fill_price = res["filled_price"]
                        
                pnl = (round(state['entry_price'], 2) - round(exit_fill_price, 2)) * 2 * self.lot_size
                self.realized_pnl += pnl
                
                reason = "Stop Loss (Shifted)" if state['current_sl'] < state['day_high'] else "Stop Loss (Initial)"
                
                trade_rec = {
                    "date": current_time.strftime('%Y-%m-%d'),
                    "type": opt_type,
                    "strike": strike,
                    "lots": 2,
                    "entry_time": state['entry_time'] or "09:30:00",
                    "entry_price": round(state['entry_price'], 2),
                    "exit_time": current_time.strftime('%H:%M:%S'),
                    "exit_price": round(exit_fill_price, 2),
                    "sl": round(state['current_sl'], 2),
                    "day_high": round(state['day_high'], 2),
                    "pnl": round(pnl, 2),
                    "reason": reason
                }
                save_live_trade(trade_rec)
                
                state['position'] = None
                state['unrealized'] = 0.0
                state['above_entry_threshold'] = None
                state['sl_order_id'] = None
                
                self.add_log(f"[{reason.upper()}] {opt_type} SL hit at {exit_fill_price:.2f} (PnL: ₹{pnl:.2f})")
                send_telegram_alert(f"🛑 <b>{reason.upper()} HIT</b>\n{opt_type} {strike} Closed @ ₹{exit_fill_price:.2f}\nPnL: ₹{pnl:.2f}")
                
            # Check Target Lot 1
            elif o_price <= state['target']:
                exit_fill_price = state['target']
                if self.mode == "LIVE_REAL" and self.kite:
                    symbol = self.get_option_symbol(strike, opt_type, current_time)
                    res = self.place_zerodha_order(symbol, "BUY", 1 * self.lot_size, order_type="MARKET")
                    if res and res.get("filled_price", 0) > 0:
                        exit_fill_price = res["filled_price"]
                        
                pnl = (round(state['entry_price'], 2) - round(exit_fill_price, 2)) * self.lot_size
                self.realized_pnl += pnl
                
                trade_rec = {
                    "date": current_time.strftime('%Y-%m-%d'),
                    "type": opt_type,
                    "strike": strike,
                    "lots": 1,
                    "entry_time": state['entry_time'] or "09:30:00",
                    "entry_price": round(state['entry_price'], 2),
                    "exit_time": current_time.strftime('%H:%M:%S'),
                    "exit_price": round(exit_fill_price, 2),
                    "sl": round(state['day_high'], 2),
                    "day_high": round(state['day_high'], 2),
                    "pnl": round(pnl, 2),
                    "reason": "Target (Lot 1)"
                }
                save_live_trade(trade_rec)
                
                state['position'] = 'SHORT_1_LOT'
                state['unrealized'] = (state['entry_price'] - o_price) * self.lot_size
                state['current_sl'] = get_trailed_sl(state['min_price'], state['day_high'])

                # Modify existing exchange SL-L order to protect remaining 1 Lot
                if self.mode == "LIVE_REAL" and self.kite and state.get('sl_order_id'):
                    self.modify_zerodha_sl_order(state['sl_order_id'], trigger_price=state['current_sl'], quantity=1 * self.lot_size)

                self.add_log(f"[TARGET LOT 1] {opt_type} Target reached at {exit_fill_price:.2f} (Realized: +₹{pnl:.2f})")
                send_telegram_alert(f"🎯 <b>TARGET LOT 1 REACHED</b>\n{opt_type} {strike} Exited 1 Lot @ ₹{exit_fill_price:.2f}\nRealized PnL: +₹{pnl:.2f}")
                
        elif state['position'] == 'SHORT_1_LOT':
            state['min_price'] = min(state['min_price'], o_price)
            new_sl = get_trailed_sl(state['min_price'], state['day_high'])
            if new_sl < state['current_sl']:
                state['current_sl'] = new_sl
                if self.mode == "LIVE_REAL" and self.kite and state.get('sl_order_id'):
                    self.modify_zerodha_sl_order(state['sl_order_id'], trigger_price=new_sl, quantity=1 * self.lot_size)

            state['unrealized'] = (state['entry_price'] - o_price) * self.lot_size
            
            # Check Trailing SL
            if o_price >= state['current_sl']:
                exit_fill_price = state['current_sl']
                if self.mode == "LIVE_REAL" and self.kite:
                    symbol = self.get_option_symbol(strike, opt_type, current_time)
                    if not state.get('sl_order_id'):
                        res = self.place_zerodha_order(symbol, "BUY", 1 * self.lot_size, order_type="MARKET")
                        if res and res.get("filled_price", 0) > 0:
                            exit_fill_price = res["filled_price"]
                        
                pnl = (round(state['entry_price'], 2) - round(exit_fill_price, 2)) * self.lot_size
                self.realized_pnl += pnl
                
                trade_rec = {
                    "date": current_time.strftime('%Y-%m-%d'),
                    "type": opt_type,
                    "strike": strike,
                    "lots": 1,
                    "entry_time": state['entry_time'] or "09:30:00",
                    "entry_price": round(state['entry_price'], 2),
                    "exit_time": current_time.strftime('%H:%M:%S'),
                    "exit_price": round(exit_fill_price, 2),
                    "sl": round(state['current_sl'], 2),
                    "day_high": round(state['day_high'], 2),
                    "pnl": round(pnl, 2),
                    "reason": "Trailing Stop Loss"
                }
                save_live_trade(trade_rec)
                
                state['position'] = None
                state['unrealized'] = 0.0
                state['above_entry_threshold'] = None
                state['sl_order_id'] = None
                self.add_log(f"[TRAILING SL] {opt_type} Trailing SL hit at {exit_fill_price:.2f} (PnL: ₹{pnl:.2f})")
                send_telegram_alert(f"🛑 <b>TRAILING SL HIT</b>\n{opt_type} {strike} Exited Lot 2 @ ₹{exit_fill_price:.2f}\nPnL: ₹{pnl:.2f}")

    def _start_websocket(self):
        if not self.kite:
            return
            
        try:
            self.add_log("Starting Zerodha WebSocket connection...")
            from kiteconnect import KiteTicker
            self.kws = KiteTicker(self.kite.api_key, self.kite.access_token)
            
            # Define callbacks
            def on_ticks(ws, ticks):
                with self.lock:
                    self.last_websocket_tick_time = datetime.datetime.now(datetime.timezone.utc)
                    current_time = self.get_current_time()
                    
                    for tick in ticks:
                        token = tick.get("instrument_token")
                        ltp = tick.get("last_price")
                        if not token or ltp is None:
                            continue
                            
                        # Spot check
                        if token == self.spot_token:
                            self.spot_price = ltp
                            continue
                            
                        # Options check
                        mapping = self.websocket_tokens.get(token)
                        if mapping:
                            opt_type = mapping["opt_type"]
                            state = self.strategy_state[opt_type]
                            
                            state['price'] = ltp
                            high_val = tick.get("ohlc", {}).get("high", ltp)
                            low_val = tick.get("ohlc", {}).get("low", ltp)
                            
                            if state['position'] is None:
                                state['day_high'] = max(state['day_high'], high_val)
                                
                            self._evaluate_strategy_for_leg(opt_type, ltp, high_val, low_val, current_time)
                            
            def on_connect(ws, response):
                self.add_log("Zerodha WebSocket connected.")
                with self.lock:
                    self.websocket_active = True
                    # Subscribe to tokens
                    tokens_to_sub = []
                    if self.spot_token:
                        tokens_to_sub.append(self.spot_token)
                    for tok in self.websocket_tokens.keys():
                        tokens_to_sub.append(tok)
                        
                    if tokens_to_sub:
                        self.add_log(f"Subscribing to WebSocket tokens: {tokens_to_sub}")
                        ws.subscribe(tokens_to_sub)
                        ws.set_mode(ws.MODE_FULL, tokens_to_sub)
                        
            def on_close(ws, code, reason):
                self.add_log(f"Zerodha WebSocket disconnected: {code} - {reason}")
                with self.lock:
                    self.websocket_active = False
                    
            def on_reconnect(ws, attempts_count):
                self.add_log(f"Zerodha WebSocket reconnecting (attempt {attempts_count})...")
                
            def on_error(ws, code, reason):
                print(f"Zerodha WebSocket notice: {code} - {reason}")
                
            self.kws.on_ticks = on_ticks
            self.kws.on_connect = on_connect
            self.kws.on_close = on_close
            self.kws.on_reconnect = on_reconnect
            self.kws.on_error = on_error
            
            # Start in a separate daemon thread
            self.kws.connect(threaded=True)
        except Exception as e:
            self.add_log(f"Failed to start WebSocket: {e}")
            self.websocket_active = False

    def _resolve_and_subscribe_tokens(self, current_time):
        # Resolve tokens from quote
        if not self.kite:
            return
            
        pe_strike = self.strategy_state['PE']['strike']
        ce_strike = self.strategy_state['CE']['strike']
        
        symbols = ["NSE:NIFTY 50"]
        pe_symbol = self.get_option_symbol(pe_strike, 'PE', current_time) if pe_strike else None
        ce_symbol = self.get_option_symbol(ce_strike, 'CE', current_time) if ce_strike else None
        
        if pe_symbol:
            symbols.append(pe_symbol)
        if ce_symbol:
            symbols.append(ce_symbol)
            
        try:
            quotes = self.kite.quote(symbols)
            
            # Get Spot Token
            spot_q = quotes.get("NSE:NIFTY 50", {})
            self.spot_token = spot_q.get("instrument_token")
            
            # Clear previous tokens
            self.websocket_tokens = {}
            
            if pe_symbol and pe_symbol in quotes:
                pe_q = quotes[pe_symbol]
                pe_tok = pe_q.get("instrument_token")
                if pe_tok:
                    self.websocket_tokens[pe_tok] = {"opt_type": "PE", "symbol": pe_symbol}
                    
            if ce_symbol and ce_symbol in quotes:
                ce_q = quotes[ce_symbol]
                ce_tok = ce_q.get("instrument_token")
                if ce_tok:
                    self.websocket_tokens[ce_tok] = {"opt_type": "CE", "symbol": ce_symbol}
                    
            self.add_log(f"Resolved tokens for WebSocket: Spot={self.spot_token} | PE={pe_strike} ({pe_symbol}) | CE={ce_strike} ({ce_symbol})")
            
            # Connect or update subscription
            if self.kws and self.kws.is_connected():
                # Re-subscribe dynamically
                tokens_to_sub = []
                if self.spot_token:
                    tokens_to_sub.append(self.spot_token)
                for tok in self.websocket_tokens.keys():
                    tokens_to_sub.append(tok)
                if tokens_to_sub:
                    self.kws.subscribe(tokens_to_sub)
                    self.kws.set_mode(self.kws.MODE_FULL, tokens_to_sub)
            else:
                self._start_websocket()
        except Exception as e:
            self.add_log(f"Error resolving tokens for WebSocket: {e}")

    def _run_loop(self, session_id):
        self.broadcast({"event": "status", "status": "RUNNING", "log": "Session Started."})
        
        # Synchronization catch-up on startup (Live mode only)
        if self.mode in ["LIVE", "LIVE_REAL"]:
            if getattr(self, 'kite_configured', False) and self.kite is None:
                try:
                    self.add_log("Initializing Zerodha Kite client for synchronization...")
                    self.kite = self.get_kite_client()
                except Exception as e:
                    self.add_log(f"Zerodha initialization failed on startup: {e}. Will fallback to other sources.")
                    self.kite = None

            current_time = self.get_current_time()
            t_time = current_time.time()
            # Only sync if we are within/after trading hours
            if current_time.weekday() < 5 and t_time >= datetime.time(9, 15):
                self._sync_session(current_time)
                
        # Poll interval: 5s for Mock (6x speed), 15s for Live
        tick_interval = 5.0 if self.mode == "MOCK" else 15.0
        
        # Load yfinance ticker once
        yf_ticker = yf.Ticker(self.symbol) if self.mode in ["LIVE", "LIVE_REAL"] else None
        
        # Zerodha client is initialized dynamically in the loop if credentials exist
                
        while self.status in ["RUNNING", "FEED_DISRUPTED", "AWAITING_TOKEN"] and session_id == self.session_id:
            try:
                current_time = self.get_current_time()
                t_time = current_time.time()
                
                spot = 0.0
                expiry_date_str = ""
                expiry_date = None
                
                # Check Market Hours & Holiday Status (09:08 to 15:30 IST)
                is_market_open = True
                if self.mode in ["LIVE", "LIVE_REAL"]:
                    is_trading_day, day_reason = get_market_day_status(current_time)
                    if not is_trading_day:
                        self.add_log(f"Market is closed today ({day_reason}). Stopping session.")
                        self.broadcast({"event": "market_status", "status": f"CLOSED ({day_reason})", "reason": day_reason})
                        self._stop_internal()
                        break
                    elif t_time >= datetime.time(15, 30):
                        self.add_log("Market has closed for the day (15:30 IST). Session completed.")
                        self.broadcast({"event": "market_status", "status": "CLOSED"})
                        self._stop_internal()
                        break
                    elif t_time < datetime.time(9, 8):
                        is_market_open = False
                
                if not is_market_open:
                    self.add_log("Market is currently CLOSED. Waiting for market open...")
                    self.broadcast({"event": "market_status", "status": "CLOSED"})
                    time.sleep(30.0)
                    continue
                
                # Pre-initialize tick variables to guarantee defined scope
                pe_price = ce_price = pe_high = ce_high = pe_low = ce_low = 0.0
                tick_feed = "NONE"

                # 1. Fetch/Generate Spot Price & Option Prices
                if self.mode == "MOCK":
                    change_pct = random.normalvariate(0.0, 0.0004)
                    self.spot_price = self.spot_price * (1.0 + change_pct)
                    spot = self.spot_price
                    expiry_date_str = "Current Weekly Expiry"
                    tick_feed = "MOCK"
                else:
                    # Live Mode: Tiered data extraction
                    expiry_date = self.get_nearest_nifty_expiry(current_time)
                    expiry_date_str = expiry_date.strftime("%d-%b-%Y")
                    
                    pe_strike = self.strategy_state['PE']['strike']
                    ce_strike = self.strategy_state['CE']['strike']
                       # --- Live Trading Feeds ---
                    if self.mode in ["LIVE", "LIVE_REAL"]:
                        # In LIVE and LIVE_REAL modes, we strictly require authentic Zerodha Kite data.
                        # We NEVER fall back to NSE scraper or Black-Scholes synthetic math.
                        if self.kite is None:
                            try:
                                self.kite = self.get_kite_client()
                            except Exception:
                                self.kite = None
                                
                        if self.kite is None:
                            # Token not yet pushed by Oracle VPS -> Wait gracefully
                            tick_feed = "AWAITING_TOKEN"
                            self.status = "AWAITING_TOKEN"
                            if self.feed_source != "AWAITING_TOKEN":
                                self.feed_source = "AWAITING_TOKEN"
                                self.broadcast({"event": "status", "status": "AWAITING_TOKEN", "feed_source": "AWAITING_TOKEN", "log": "Waiting for Oracle VPS to push daily Zerodha token..."})
                            time.sleep(3)
                            continue
                            
                        # If Kite is connected, fetch live Zerodha quotes
                        symbols_to_fetch = ["NSE:NIFTY 50"]
                        pe_symbol = self.get_option_symbol(pe_strike, 'PE', current_time) if pe_strike else None
                        ce_symbol = self.get_option_symbol(ce_strike, 'CE', current_time) if ce_strike else None
                        if pe_symbol:
                            symbols_to_fetch.append(pe_symbol)
                        if ce_symbol:
                            symbols_to_fetch.append(ce_symbol)
                            
                        try:
                            quotes = self.kite.quote(symbols_to_fetch)
                            
                            self.disruption_counter = 0
                            self.zerodha_consecutive_errors = 0
                            if self.feed_disrupted or self.status == "AWAITING_TOKEN":
                                self.feed_disrupted = False
                                self.status = "RUNNING"
                                self.add_log("Zerodha live feed connected.")
                                
                            spot_val = quotes.get("NSE:NIFTY 50", {}).get("last_price")
                            if spot_val:
                                spot = float(spot_val)
                                self.spot_price = spot
                            else:
                                spot = self.spot_price if self.spot_price > 0 else 24000.0
                                
                            if pe_symbol and pe_symbol in quotes:
                                pe_quote = quotes[pe_symbol]
                                pe_price = pe_quote.get("last_price", 0.0)
                                pe_high = pe_quote.get("ohlc", {}).get("high", pe_price)
                                pe_low = pe_quote.get("ohlc", {}).get("low", pe_price)
                            if ce_symbol and ce_symbol in quotes:
                                ce_quote = quotes[ce_symbol]
                                ce_price = ce_quote.get("last_price", 0.0)
                                ce_high = ce_quote.get("ohlc", {}).get("high", ce_price)
                                ce_low = ce_quote.get("ohlc", {}).get("low", ce_price)
                                
                            tick_feed = "ZERODHA"
                        except Exception as e:
                            err_msg = str(e)
                            self.zerodha_consecutive_errors = getattr(self, 'zerodha_consecutive_errors', 0) + 1
                            if "403" in err_msg or "token" in err_msg.lower() or "Forbidden" in err_msg:
                                self.kite = None
                                self.add_log(f"Zerodha session expired ({err_msg}). Waiting for Oracle VPS token refresh...")
                                tick_feed = "AWAITING_TOKEN"
                                time.sleep(2)
                                continue
                            else:
                                tick_feed = "ERROR"
                                if self.zerodha_consecutive_errors >= 2:
                                    self.add_log(f"Zerodha network lag ({err_msg}). Retrying next tick...")
                                time.sleep(1)
                                continue
                    else:
                        # MOCK mode simulation
                        tick_feed = "BS"
                        spot = self.get_live_spot_price() or self.spot_price or 24000.0
                        self.spot_price = spot
                        t_years = get_time_to_expiry_years(current_time, expiry_date)
                        if pe_strike:
                            pe_price = black_scholes(spot, pe_strike, t_years, self.r, self.iv, 'PE')
                            pe_high = pe_price
                            pe_low = pe_price
                        if ce_strike:
                            ce_price = black_scholes(spot, ce_strike, t_years, self.r, self.iv, 'CE')
                            ce_high = ce_price
                            ce_low = ce_price

                    # Update active feed source state
                    if self.feed_source != tick_feed:
                        self.feed_source = tick_feed
                        self.add_log(f"Feed switched to: {tick_feed}")
                        self.broadcast({"event": "status", "status": self.status, "feed_source": self.feed_source, "log": f"Active Feed Source: {self.feed_source}"})
                        
                    # Handle disruption alert state (Only in Live modes)
                    if self.mode in ["LIVE", "LIVE_REAL"]:
                        if tick_feed in ["ERROR", "AWAITING_TOKEN"]:
                            self.disruption_counter = getattr(self, 'disruption_counter', 0) + 1
                            if self.disruption_counter >= 5:
                                if not self.feed_disrupted:
                                    self.feed_disrupted = True
                                    self.status = "FEED_DISRUPTED"
                                    self.add_log(f"🟡 Zerodha live feed disrupted ({tick_feed}). Awaiting reconnect...")
                                    self.broadcast({"event": "status", "status": "FEED_DISRUPTED", "feed_source": self.feed_source, "log": f"Feed disrupted ({tick_feed})."})
                                
                t_years = get_time_to_expiry_years(current_time, expiry_date)
                
                # 2. Strategy Execution State Machine
                modes = []
                if self.strategy_type in ['PE_SELL', 'BOTH']:
                    modes.append('PE')
                if self.strategy_type in ['CE_SELL', 'BOTH']:
                    modes.append('CE')
                
                # A. Strike Selection at 09:30 AM
                if t_time >= datetime.time(9, 30) and self.strategy_state['PE']['strike'] is None:
                    spot_930 = spot
                    pe_strike = round_to_nearest_strike(spot_930 + self.strike_offset_pe)
                    ce_strike = round_to_nearest_strike(spot_930 + self.strike_offset_ce)
                    
                    for opt_type in ['PE', 'CE']:
                        state = self.strategy_state[opt_type]
                        strike = pe_strike if opt_type == 'PE' else ce_strike
                        state['strike'] = strike
                        
                        if self.mode in ["LIVE", "LIVE_REAL"]:
                            if self.kite:
                                opt_symbol = self.get_option_symbol(strike, opt_type, current_time)
                                try:
                                    opt_quotes = self.kite.quote([opt_symbol])
                                    quote_data = opt_quotes.get(opt_symbol, {})
                                    val = float(quote_data.get("last_price", 0.0))
                                    state['day_high'] = float(quote_data.get("ohlc", {}).get("high", val))
                                    state['price'] = val
                                except Exception as e:
                                    self.add_log(f"⚠️ Error fetching initial Zerodha quote for {opt_symbol}: {e}")
                                    val = 0.0
                                    state['day_high'] = 0.0
                                    state['price'] = 0.0
                            else:
                                self.add_log(f"⚠️ Cannot initialize {opt_type} {strike} - Zerodha not connected.")
                                val = 0.0
                                state['day_high'] = 0.0
                                state['price'] = 0.0
                        else:
                            # MOCK mode only
                            val = black_scholes(spot, strike, t_years, self.r, self.iv, opt_type)
                            state['day_high'] = val * 1.15
                            state['price'] = val
                        
                        self.add_log(f"Strike Selected: {opt_type} {strike} | Day High (X): {state['day_high']:.2f}")
                        target_entry = state['day_high'] * (1.0 - self.entry_discount)
                        if state['price'] > 0:
                            state['above_entry_threshold'] = (state['price'] > target_entry)
                        else:
                            state['above_entry_threshold'] = None
                            
                    if self.mode in ["LIVE", "LIVE_REAL"]:
                        self._resolve_and_subscribe_tokens(current_time)
                
                # B. Strategy Rules check
                if self.strategy_state['PE']['strike'] is not None:
                    # Watchdog Check for WebSocket Heartbeat (Live mode only)
                    if self.mode in ["LIVE", "LIVE_REAL"] and self.websocket_active and self.last_websocket_tick_time:
                        elapsed = (datetime.datetime.now(datetime.timezone.utc) - self.last_websocket_tick_time).total_seconds()
                        if elapsed > 45.0:
                            self.add_log(f"⚠️ WebSocket heartbeat timeout! No ticks received for {elapsed:.1f}s. Reconnecting...")
                            self.websocket_active = False
                            if self.kws:
                                try:
                                    self.kws.close()
                                except Exception:
                                    pass
                                self.kws = None
                            self._start_websocket()

                    if not self.websocket_active:
                        # Fallback Mode: Evaluate strategy using REST quotes/fallbacks
                        if self.mode in ["LIVE", "LIVE_REAL"]:
                            # In Live modes, only evaluate ticks if Zerodha REST quote succeeded
                            if tick_feed == "ZERODHA":
                                for opt_type in modes:
                                    state = self.strategy_state[opt_type]
                                    strike = state['strike']
                                    o_price = pe_price if opt_type == 'PE' else ce_price
                                    o_high = pe_high if opt_type == 'PE' else ce_high
                                    o_low = pe_low if opt_type == 'PE' else ce_low
                                    if state['position'] is None:
                                        state['day_high'] = max(state['day_high'], o_high)
                                    state['price'] = o_price
                                    self._evaluate_strategy_for_leg(opt_type, o_price, o_high, o_low, current_time)
                            else:
                                self.add_log("⚠️ Feed disconnected from Zerodha. Skipping tick evaluation to prevent false trades...")
                        else:
                            # MOCK simulation fallback
                            for opt_type in modes:
                                state = self.strategy_state[opt_type]
                                strike = state['strike']
                                o_price = black_scholes(spot, strike, t_years, self.r, self.iv, opt_type)
                                o_high = o_price
                                o_low = o_price
                                if state['position'] is None and o_price > state['day_high']:
                                    state['day_high'] = o_price
                                state['price'] = o_price
                                self._evaluate_strategy_for_leg(opt_type, o_price, o_high, o_low, current_time)
                
                # C. Compute total unrealized and packet updates
                total_unrealized = sum(self.strategy_state[k]['unrealized'] for k in modes)
                
                # 20% Daily Drawdown Circuit Breaker Check
                max_daily_loss = self.capital * 0.20
                current_daily_pnl = self.realized_pnl + total_unrealized
                if current_daily_pnl <= -max_daily_loss and not getattr(self, 'circuit_breaker_tripped', False):
                    self.circuit_breaker_tripped = True
                    self.add_log(f"🚨 20% DAILY DRAWDOWN CIRCUIT BREAKER TRIPPED! Current PnL: ₹{current_daily_pnl:.2f} (Limit: -₹{max_daily_loss:.2f})")
                    send_telegram_alert(f"🚨 <b>20% CIRCUIT BREAKER TRIPPED</b>\nCurrent PnL: ₹{current_daily_pnl:.2f}\nExecuting Emergency Market Square-Off...")
                    self.panic_square_off("20% Daily Drawdown Limit")

                pe_packet = self.strategy_state['PE']
                ce_packet = self.strategy_state['CE']
                
                broker_margins = self.get_live_broker_margins()
                live_cap = self.capital
                broker_utilised = 0.0
                if self.mode == "LIVE_REAL" and broker_margins:
                    # In LIVE_REAL mode, use actual Zerodha cash balance if available
                    if broker_margins.get("available", 0.0) > 0.0:
                        live_cap = broker_margins["available"]
                    broker_utilised = broker_margins.get("utilised", 0.0)
                
                margin_required, margin_safe = self.calculate_margins(current_time)
                
                tick_packet = {
                    "event": "tick",
                    "datetime": current_time.strftime('%Y-%m-%d %H:%M:%S'),
                    "spot": round(spot, 2),
                    "expiry": expiry_date_str,
                    "market_status": "OPEN" if is_market_open else "CLOSED",
                    "mode": self.mode,
                    "account": {
                        "balance": round(live_cap + self.realized_pnl + total_unrealized, 2),
                        "realized_pnl": round(self.realized_pnl, 2),
                        "unrealized_pnl": round(total_unrealized, 2),
                        "capital": live_cap
                    },
                    "margin": {
                        "required": margin_required,
                        "safe": margin_safe,
                        "utilised": broker_utilised
                    },
                    "margin_info": self.get_margin_metrics(),
                    "pe": {
                        "strike": pe_packet['strike'],
                        "price": round(pe_packet['price'], 2),
                        "day_high": round(pe_packet['day_high'], 2),
                        "position": str(pe_packet['position']),
                        "entry_price": round(pe_packet['entry_price'], 2),
                        "sl": round(pe_packet['current_sl'], 2),
                        "target": round(pe_packet['target'], 2),
                        "reentries": pe_packet['entry_count'],
                        "entry_time": pe_packet.get('entry_time'),
                        "unrealized": round(pe_packet.get('unrealized', 0.0), 2)
                    } if pe_packet['strike'] else {},
                    "ce": {
                        "strike": ce_packet['strike'],
                        "price": round(ce_packet['price'], 2),
                        "day_high": round(ce_packet['day_high'], 2),
                        "position": str(ce_packet['position']),
                        "entry_price": round(ce_packet['entry_price'], 2),
                        "sl": round(ce_packet['current_sl'], 2),
                        "target": round(ce_packet['target'], 2),
                        "reentries": ce_packet['entry_count'],
                        "entry_time": ce_packet.get('entry_time'),
                        "unrealized": round(ce_packet.get('unrealized', 0.0), 2)
                    } if ce_packet['strike'] else {},
                    "inception": get_inception_summary(total_unrealized)
                }
                self.broadcast(tick_packet)
                
                # Record successful tick timestamp for watchdog
                self.last_tick_time = datetime.datetime.now(datetime.timezone.utc)
                self._loop_error_count = 0  # Reset consecutive error counter on success
                self.persist_state()
                
                # D. Progress Simulated Clock in Mock Mode (6x speed: 30s sim time / 5s real)
                if self.mode == "MOCK" and self.simulated_dt is not None:
                    self.simulated_dt += datetime.timedelta(seconds=30)
                
            except Exception as e:
                import traceback
                print(f"Error in Live Feed loop: {e}")
                traceback.print_exc()
                self.add_log(f"System Error: {str(e)}")
                self._loop_error_count += 1
                if self._loop_error_count >= 5:
                    # 5 consecutive errors — log critical alert and re-raise so systemd
                    # can auto-restart the service cleanly with the latest code.
                    self.add_log(f"🔴 CRITICAL: Loop has failed {self._loop_error_count} times in a row. Triggering service restart...")
                    self.broadcast({"event": "status", "status": "CRITICAL",
                                    "log": f"🔴 CRITICAL: {self._loop_error_count} consecutive errors. Service restarting..."})
                    time.sleep(2)  # Brief pause so broadcast can flush
                    raise  # Re-raise → systemd Restart=always will restart the service
                
            time.sleep(tick_interval)

# Create a global LiveManager instance
live_manager = LiveManager()

class AutoPilotScheduler:
    def __init__(self, manager):
        self.live_manager = manager
        self.running = False
        self.thread = None
        self.manually_stopped_today = False
        self.last_checked_date = None
        self.last_holiday_logged = None

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self.thread.start()
            print("Auto-Pilot Scheduler thread started.")

    def stop(self):
        self.running = False

    def _scheduler_loop(self):
        ist = pytz.timezone('Asia/Kolkata')
        while self.running:
            try:
                config = load_strategy_config()
                is_auto_pilot = config.get("auto_pilot", False)
                
                now = datetime.datetime.now(ist)
                current_date = now.date()
                current_time = now.time()
                
                # Reset manual override at midnight
                if self.last_checked_date != current_date:
                    self.manually_stopped_today = False
                    self.last_checked_date = current_date
                
                is_trading_day, day_reason = get_market_day_status(now)
                
                if not is_trading_day:
                    if self.last_holiday_logged != current_date:
                        self.live_manager.add_log(f"[Auto-Pilot] {day_reason} detected ({current_date}). Trading engine will remain inactive.")
                        self.last_holiday_logged = current_date
                        self.live_manager.broadcast({"event": "market_status", "status": f"CLOSED ({day_reason})", "reason": day_reason})
                    
                    if self.live_manager.status in ["RUNNING", "FEED_DISRUPTED", "AWAITING_TOKEN"]:
                        self.live_manager.add_log(f"[Auto-Pilot] {day_reason} detected. Auto-stopping trading engine...")
                        self.live_manager.stop()
                        self.manually_stopped_today = False
                    
                    time.sleep(10.0)
                    continue

                if is_auto_pilot and is_trading_day:
                    start_time = datetime.time(9, 8)
                    stop_time = datetime.time(15, 30)
                    
                    if start_time <= current_time < stop_time:
                        thread_dead = (self.live_manager.thread is None or not self.live_manager.thread.is_alive())
                        if (self.live_manager.status == "STOPPED" or thread_dead) and not self.manually_stopped_today:
                            self.live_manager.add_log("[Auto-Pilot] Scheduled start time reached (09:08 - 15:30 IST). Auto-starting trading engine...")
                            self.live_manager.start(
                                mode=config.get("mode", "LIVE"),
                                capital=config.get("capital", 500000.0),
                                strategy_type=config.get("strategy_type", "BOTH"),
                                strike_offset_pe=config.get("strike_offset_pe", 100),
                                strike_offset_ce=config.get("strike_offset_ce", -100),
                                entry_discount=config.get("entry_discount", 10.0),
                                lot_1_target=config.get("lot_1_target", 40.0),
                                max_reentries=config.get("max_reentries", 5),
                                lot_size=config.get("lot_size", 65),
                                iv=config.get("iv", 15.0),
                                r=config.get("r", 7.0)
                            )
                        
                        # --- WATCHDOG: Detect silent broken loop ---
                        # If engine is RUNNING but no successful tick has occurred in
                        # the last 180 seconds, attempt WebSocket token re-subscription.
                        elif self.live_manager.status in ["RUNNING", "FEED_DISRUPTED"]:
                            last_tick = self.live_manager.last_tick_time
                            if last_tick is not None:
                                stale_seconds = (datetime.datetime.now(datetime.timezone.utc) - last_tick).total_seconds()
                                if stale_seconds > 180:
                                    self.live_manager.add_log(
                                        f"🔴 WATCHDOG: No successful tick in {int(stale_seconds)}s. "
                                        f"Attempting WebSocket token re-subscription to recover feed..."
                                    )
                                    try:
                                        ist_now = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
                                        self.live_manager._resolve_and_subscribe_tokens(ist_now)
                                        self.live_manager.last_tick_time = datetime.datetime.now(datetime.timezone.utc)
                                    except Exception as w_ex:
                                        self.live_manager.add_log(f"Watchdog re-subscription failed: {w_ex}")
                    else:
                        # Outside trading hours: if running, auto-stop
                        if self.live_manager.status in ["RUNNING", "FEED_DISRUPTED", "AWAITING_TOKEN"]:
                            self.live_manager.add_log("[Auto-Pilot] Scheduled stop time reached (15:30 IST). Auto-stopping trading engine...")
                            self.live_manager.stop()
                            self.manually_stopped_today = False
            except Exception as e:
                print(f"Error in Auto-Pilot Scheduler loop: {e}")
            
            time.sleep(10.0)

# Instantiate the global scheduler
autopilot_scheduler = AutoPilotScheduler(live_manager)

