import os
import sys

# Add project subdirectories to sys.path so all imports resolve seamlessly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backtester'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'live_bot'))
import fcntl
import datetime
import queue
import json
import pytz

def load_env_file():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
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

# Prevent multiple instances of the trading bot from running concurrently
lock_file = None

def acquire_lock():
    global lock_file
    try:
        lock_file = open('/tmp/nifty_bot.lock', 'w')
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file.write(str(os.getpid()))
        lock_file.flush()
    except IOError:
        print("CRITICAL: Another instance of the nifty-bot is already running. Exiting to prevent duplication.")
        sys.exit(1)

from flask import Flask, request, jsonify, render_template, Response, send_from_directory
from engine import download_nifty_spot_data, run_backtest, generate_performance_metrics
from playback import playback_manager
from live_feed import live_manager
import pandas as pd

app = Flask(__name__, template_folder='templates', static_folder='static')

def check_auth(username, password):
    expected_user = os.environ.get('BASIC_AUTH_USER', 'admin')
    expected_pass = os.environ.get('BASIC_AUTH_PASS', 'admin')
    return username == expected_user and password == expected_pass

def authenticate():
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'}
    )

@app.before_request
def require_authentication():
    if request.endpoint == 'static':
        return
    # Bypass auth for Zerodha authentication redirect containing request_token
    if 'request_token' in request.args:
        return
    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password):
        return authenticate()

# Create necessary static/css, static/js, and templates directories if they don't exist
os.makedirs(os.path.join(app.root_path, 'templates'), exist_ok=True)
os.makedirs(os.path.join(app.root_path, 'static', 'css'), exist_ok=True)
os.makedirs(os.path.join(app.root_path, 'static', 'js'), exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/paper')
def paper():
    return render_template('paper.html')

@app.route('/api/backtest', methods=['POST'])
def api_backtest():
    try:
        data = request.get_json() or {}
        
        # Parse inputs
        symbol = data.get('symbol', '^NSEI')
        period = data.get('period', '1mo')
        strategy_type = data.get('strategy_type', 'BOTH')
        
        strike_offset_pe = int(data.get('strike_offset_pe', 100))
        strike_offset_ce = int(data.get('strike_offset_ce', -100))
        
        entry_discount = float(data.get('entry_discount', 10)) / 100.0
        lot_1_target = float(data.get('lot_1_target', 40)) / 100.0
        max_reentries = int(data.get('max_reentries', 5))
        lot_size = int(data.get('lot_size', 65))
        
        iv = float(data.get('iv', 15)) / 100.0
        r = float(data.get('r', 7)) / 100.0
        capital = float(data.get('capital', 200000.0))
        
        # Download spot data
        df = download_nifty_spot_data(symbol, period)
        if df.empty:
            return jsonify({'error': 'No market data could be fetched or simulated.'}), 400
            
        # Run backtest
        trades = run_backtest(
            df=df,
            strategy_type=strategy_type,
            strike_offset_pe=strike_offset_pe,
            strike_offset_ce=strike_offset_ce,
            entry_discount=entry_discount,
            lot_1_target_pct=lot_1_target,
            max_reentries=max_reentries,
            lot_size=lot_size,
            iv=iv,
            r=r
        )
        
        # Calculate performance metrics
        metrics = generate_performance_metrics(trades, lot_size=lot_size, initial_capital=capital)
        
        # Include summary metadata for response
        metrics['symbol'] = symbol
        metrics['period'] = period
        metrics['strategy'] = strategy_type
        
        return jsonify(metrics)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload-csv', methods=['POST'])
def api_upload_csv():
    """
    Optional endpoint for uploading custom option/spot data.
    Runs a backtest on the uploaded file contents directly.
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part in the request.'}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected.'}), 400
            
        # Read parameters from form
        strategy_type = request.form.get('strategy_type', 'BOTH')
        strike_offset_pe = int(request.form.get('strike_offset_pe', 100))
        strike_offset_ce = int(request.form.get('strike_offset_ce', -100))
        entry_discount = float(request.form.get('entry_discount', 10)) / 100.0
        lot_1_target = float(request.form.get('lot_1_target', 40)) / 100.0
        max_reentries = int(request.form.get('max_reentries', 5))
        lot_size = int(request.form.get('lot_size', 65))
        iv = float(request.form.get('iv', 15)) / 100.0
        r = float(request.form.get('r', 7)) / 100.0
        capital = float(request.form.get('capital', 200000.0))
        
        # Load CSV into pandas
        df = pd.read_csv(file)
        
        # Check required columns
        required_cols = {'Datetime', 'Open', 'High', 'Low', 'Close'}
        if not required_cols.issubset(df.columns):
            # Try parsing if columns have different capitalization
            df.columns = [c.capitalize() for c in df.columns]
            if not required_cols.issubset(df.columns):
                return jsonify({'error': f'CSV must contain columns: {required_cols}'}), 400
                
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        # If naive, localize to Asia/Kolkata
        if df['Datetime'].dt.tz is None:
            df['Datetime'] = df['Datetime'].dt.localize('Asia/Kolkata')
        else:
            df['Datetime'] = df['Datetime'].dt.tz_convert('Asia/Kolkata')
            
        df = df.sort_values('Datetime').reset_index(drop=True)
        
        # Run backtest
        trades = run_backtest(
            df=df,
            strategy_type=strategy_type,
            strike_offset_pe=strike_offset_pe,
            strike_offset_ce=strike_offset_ce,
            entry_discount=entry_discount,
            lot_1_target_pct=lot_1_target,
            max_reentries=max_reentries,
            lot_size=lot_size,
            iv=iv,
            r=r
        )
        
        metrics = generate_performance_metrics(trades, lot_size=lot_size, initial_capital=capital)
        metrics['symbol'] = 'Uploaded File'
        metrics['period'] = 'Custom'
        metrics['strategy'] = strategy_type
        
        return jsonify(metrics)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/playback/control', methods=['POST'])
def api_playback_control():
    try:
        data = request.get_json() or {}
        action = data.get('action')
        
        if action == 'start':
            symbol = data.get('symbol', '^NSEI')
            date_str = data.get('date', '')
            speed = data.get('speed', 1.0)
            capital = data.get('capital', 200000.0)
            strategy_type = data.get('strategy_type', 'BOTH')
            strike_offset_pe = data.get('strike_offset_pe', 100)
            strike_offset_ce = data.get('strike_offset_ce', -100)
            entry_discount = data.get('entry_discount', 10.0)
            lot_1_target = data.get('lot_1_target', 40.0)
            max_reentries = data.get('max_reentries', 5)
            lot_size = data.get('lot_size', 65)
            iv = data.get('iv', 15.0)
            r = data.get('r', 7.0)
            
            if not date_str:
                return jsonify({'error': 'Date is required for simulation.'}), 400
                
            playback_manager.start(
                symbol=symbol, date_str=date_str, speed=speed, capital=capital,
                strategy_type=strategy_type, strike_offset_pe=strike_offset_pe,
                strike_offset_ce=strike_offset_ce, entry_discount=entry_discount,
                lot_1_target=lot_1_target, max_reentries=max_reentries,
                lot_size=lot_size, iv=iv, r=r
            )
        elif action == 'pause':
            playback_manager.pause()
        elif action == 'resume':
            playback_manager.resume()
        elif action == 'stop':
            playback_manager.stop()
        elif action == 'speed':
            speed = data.get('speed', 1.0)
            playback_manager.set_speed(speed)
        else:
            return jsonify({'error': f'Unknown action: {action}'}), 400
            
        return jsonify({'status': 'success', 'manager_status': playback_manager.status})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/playback/stream')
def api_playback_stream():
    q = playback_manager.register_client()
    
    def event_stream():
        try:
            # Yield initial connection status
            yield f"data: {json.dumps({'event': 'status', 'status': playback_manager.status, 'log': 'Connected to Stream.'})}\n\n"
            
            while True:
                try:
                    # Wait up to 5 seconds for a tick update
                    data = q.get(timeout=5.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    # Send heartbeat ping to keep connection alive
                    yield "event: heartbeat\ndata: ping\n\n"
        except GeneratorExit:
            pass
        finally:
            playback_manager.unregister_client(q)
            
    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/live')
def live():
    return render_template('live.html')

@app.route('/api/live/control', methods=['POST'])
def api_live_control():
    try:
        from live_feed import autopilot_scheduler
        data = request.get_json() or {}
        action = data.get('action')
        
        if action == 'start':
            autopilot_scheduler.manually_stopped_today = False
            mode = data.get('mode', 'MOCK')
            capital = data.get('capital', 200000.0)
            strategy_type = data.get('strategy_type', 'BOTH')
            strike_offset_pe = data.get('strike_offset_pe', 100)
            strike_offset_ce = data.get('strike_offset_ce', -100)
            entry_discount = data.get('entry_discount', 10.0)
            lot_1_target = data.get('lot_1_target', 40.0)
            max_reentries = data.get('max_reentries', 5)
            lot_size = data.get('lot_size', 65)
            iv = data.get('iv', 15.0)
            r = data.get('r', 7.0)
            
            live_manager.start(
                mode=mode, capital=capital, strategy_type=strategy_type,
                strike_offset_pe=strike_offset_pe, strike_offset_ce=strike_offset_ce,
                entry_discount=entry_discount, lot_1_target=lot_1_target,
                max_reentries=max_reentries, lot_size=lot_size, iv=iv, r=r
            )
        elif action == 'stop':
            autopilot_scheduler.manually_stopped_today = True
            live_manager.stop()
        else:
            return jsonify({'error': f'Unknown action: {action}'}), 400
            
        return jsonify({'status': 'success', 'manager_status': live_manager.status})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/config', methods=['GET', 'POST'])
def api_live_config():
    try:
        from live_feed import load_strategy_config, save_strategy_config
        if request.method == 'GET':
            return jsonify(load_strategy_config())
        else:
            data = request.get_json() or {}
            config = load_strategy_config()
            
            # Update values
            if 'auto_pilot' in data:
                config['auto_pilot'] = bool(data['auto_pilot'])
            if 'mode' in data:
                config['mode'] = str(data['mode'])
            if 'strategy_type' in data:
                config['strategy_type'] = str(data['strategy_type'])
                
            for int_key in ['strike_offset_pe', 'strike_offset_ce', 'max_reentries', 'lot_size']:
                if int_key in data:
                    config[int_key] = int(data[int_key])
            for float_key in ['capital', 'entry_discount', 'lot_1_target', 'iv', 'r']:
                if float_key in data:
                    config[float_key] = float(data[float_key])
            if 'kite_api_key' in data:
                config['kite_api_key'] = str(data['kite_api_key'])
            if 'kite_api_secret' in data:
                config['kite_api_secret'] = str(data['kite_api_secret'])
            if 'kite_user_id' in data:
                config['kite_user_id'] = str(data['kite_user_id'])
            if 'kite_password' in data:
                config['kite_password'] = str(data['kite_password'])
            if 'kite_totp_secret' in data:
                config['kite_totp_secret'] = str(data['kite_totp_secret'])
                
            save_strategy_config(config)
            
            # Hot-reload in-memory parameters of the active live session
            from live_feed import live_manager
            
            # Recalculate config values
            api_key = config.get('kite_api_key', '')
            api_secret = config.get('kite_api_secret', '')
            user_id = config.get('kite_user_id', '')
            password = config.get('kite_password', '')
            
            live_manager.kite_configured = bool(api_key and api_secret and user_id and password)
            
            # If credentials changed, reset the active client so it re-authenticates on next tick
            cred_keys = ['kite_api_key', 'kite_api_secret', 'kite_user_id', 'kite_password', 'kite_totp_secret']
            if any(k in data for k in cred_keys):
                live_manager.kite = None
                
            if live_manager.status == "RUNNING" or live_manager.status == "FEED_DISRUPTED":
                if 'iv' in data:
                    live_manager.iv = float(data['iv']) / 100.0
                if 'r' in data:
                    live_manager.r = float(data['r']) / 100.0
                if 'capital' in data:
                    live_manager.capital = float(data['capital'])
            
            return jsonify({'status': 'success', 'config': config})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/kite-status', methods=['GET'])
def api_live_kite_status():
    try:
        from live_feed import live_manager
        if getattr(live_manager, 'kite', None) is not None:
            return jsonify({"status": "CONNECTED", "message": "Zerodha API Connected"})
        from kite_auth import STATUS_PATH
        if os.path.exists(STATUS_PATH):
            try:
                with open(STATUS_PATH, 'r') as f:
                    return jsonify(json.load(f))
            except Exception:
                pass
        return jsonify({"status": "IDLE", "message": ""})
    except Exception as e:
        return jsonify({"status": "IDLE", "message": str(e)})

@app.route('/api/live/totp', methods=['POST'])
def api_live_totp():
    try:
        data = request.get_json() or {}
        code = data.get('code')
        if not code or len(code) != 6 or not code.isdigit():
            return jsonify({'error': 'Invalid 6-digit TOTP code.'}), 400
            
        from kite_auth import TOTP_CODE_PATH, SCRATCH_DIR
        os.makedirs(SCRATCH_DIR, exist_ok=True)
        with open(TOTP_CODE_PATH, 'w') as f:
            f.write(code.strip())
        return jsonify({'status': 'success', 'message': 'TOTP code submitted successfully.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/margin-info', methods=['GET'])
def api_live_margin_info():
    try:
        from live_feed import live_manager
        return jsonify(live_manager.get_margin_metrics())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/trades', methods=['GET'])
def api_live_trades():
    try:
        from live_feed import load_live_trades
        return jsonify(load_live_trades())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/inception-summary', methods=['GET'])
def api_live_inception_summary():
    try:
        from live_feed import get_inception_summary, live_manager
        unrealized = 0.0
        for opt_type in ['PE', 'CE']:
            unrealized += live_manager.strategy_state.get(opt_type, {}).get('unrealized', 0.0)
        return jsonify(get_inception_summary(unrealized_pnl=unrealized))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/panic-exit', methods=['POST'])
def api_live_panic_exit():
    try:
        from live_feed import live_manager
        live_manager.panic_square_off("User Emergency Panic Button")
        return jsonify({'status': 'success', 'message': 'Emergency Panic Square-Off executed successfully.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/live/market-status', methods=['GET'])
def api_live_market_status():
    from nse_holidays import get_market_day_status, is_market_hours_now
    now_ist = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
    is_open, status_str = is_market_hours_now(now_ist)
    is_trading_day, day_reason = get_market_day_status(now_ist)
    return jsonify({
        'date': now_ist.strftime('%Y-%m-%d'),
        'time': now_ist.strftime('%H:%M:%S'),
        'is_open': is_open,
        'status': status_str,
        'is_trading_day': is_trading_day,
        'day_reason': day_reason
    })

@app.route('/api/live/stream')
def api_live_stream():
    q = live_manager.register_client()
    
    def event_stream():
        try:
            # Yield initial status packet with historical logs and Auto-Pilot settings
            from live_feed import load_strategy_config, autopilot_scheduler
            from nse_holidays import get_market_day_status, is_market_hours_now
            config = load_strategy_config()
            now_ist = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
            is_open, market_status_str = is_market_hours_now(now_ist)
            is_trading_day, day_reason = get_market_day_status(now_ist)
            
            initial_packet = {
                'event': 'status',
                'status': live_manager.status,
                'mode': live_manager.mode,
                'feed_source': getattr(live_manager, 'feed_source', 'MOCK'),
                'market_status': market_status_str,
                'market_reason': day_reason,
                'is_trading_day': is_trading_day,
                'log': 'Connected to Live Stream.',
                'logs': live_manager.logs,
                'auto_pilot': config.get("auto_pilot", False),
                'manually_stopped_today': autopilot_scheduler.manually_stopped_today
            }
            yield f"data: {json.dumps(initial_packet)}\n\n"
            
            while True:
                try:
                    data = q.get(timeout=5.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    yield "event: heartbeat\ndata: ping\n\n"
        except GeneratorExit:
            pass
        finally:
            live_manager.unregister_client(q)

    resp = Response(event_stream(), mimetype="text/event-stream")
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Cache-Control'] = 'no-cache, no-transform'
    resp.headers['Connection'] = 'keep-alive'
    return resp
            
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# Start scheduler thread if Flask is running in main worker
from live_feed import autopilot_scheduler
if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
    autopilot_scheduler.start()

if __name__ == '__main__':
    acquire_lock()
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', 8080))
    try:
        app.run(host=host, port=port, debug=False)
    except Exception as e:
        print(f"Failed to start on {host}:{port} ({e}). Falling back to port 5001...")
        app.run(host=host, port=5001, debug=False)
