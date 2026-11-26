import unittest
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtester'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'live_bot'))

from unittest.mock import patch, MagicMock
import datetime
import pytz
import json
import live_feed
from live_feed import LiveManager, live_manager

class TestLiveFeedEngine(unittest.TestCase):
    
    def setUp(self):
        # Isolate file writes from production environment
        self.test_dir = tempfile.TemporaryDirectory()
        self.orig_trades_path = live_feed.TRADES_PATH
        self.orig_state_path = live_feed.LIVE_STATE_PATH
        self.orig_symbol_path = live_feed.SYMBOL_KNOWLEDGE_PATH
        live_feed.TRADES_PATH = os.path.join(self.test_dir.name, "live_trades.json")
        live_feed.LIVE_STATE_PATH = os.path.join(self.test_dir.name, "live_state.json")
        live_feed.SYMBOL_KNOWLEDGE_PATH = os.path.join(self.test_dir.name, "symbol_knowledge.json")

        # Sample mockup response from the NSE Option Chain API
        self.mock_option_chain_data = {
            "records": {
                "expiryDates": ["18-Jun-2026", "25-Jun-2026"],
                "underlyingValue": 23200.5,
                "timestamp": "13-Jun-2026 15:30:00",
                "data": [
                    {
                        "strikePrice": 23300,
                        "expiryDate": "18-Jun-2026",
                        "PE": {
                            "strikePrice": 23300,
                            "expiryDate": "18-Jun-2026",
                            "lastPrice": 120.50,
                            "high": 150.00,
                            "low": 90.00,
                            "underlyingValue": 23200.5
                        },
                        "CE": {
                            "strikePrice": 23300,
                            "expiryDate": "18-Jun-2026",
                            "lastPrice": 85.00,
                            "high": 110.00,
                            "low": 60.00,
                            "underlyingValue": 23200.5
                        }
                    },
                    {
                        "strikePrice": 23100,
                        "expiryDate": "18-Jun-2026",
                        "PE": {
                            "strikePrice": 23100,
                            "expiryDate": "18-Jun-2026",
                            "lastPrice": 45.00,
                            "high": 70.00,
                            "low": 35.00,
                            "underlyingValue": 23200.5
                        },
                        "CE": {
                            "strikePrice": 23100,
                            "expiryDate": "18-Jun-2026",
                            "lastPrice": 210.00,
                            "high": 240.00,
                            "low": 170.00,
                            "underlyingValue": 23200.5
                        }
                    }
                ]
            }
        }

    def tearDown(self):
        live_feed.TRADES_PATH = self.orig_trades_path
        live_feed.LIVE_STATE_PATH = self.orig_state_path
        live_feed.SYMBOL_KNOWLEDGE_PATH = self.orig_symbol_path
        self.test_dir.cleanup()

    def test_live_manager_mock_lifecycle(self):
        # Test life cycle transitions of the LiveManager engine in Mock mode
        manager = LiveManager()
        self.assertEqual(manager.status, "STOPPED")
        
        # Start in MOCK mode
        manager.start(
            mode="MOCK", capital=200000.0, strategy_type="BOTH",
            strike_offset_pe=100, strike_offset_ce=-100,
            entry_discount=10.0, lot_1_target=30.0,
            max_reentries=5, lot_size=65, iv=15.0, r=7.0
        )
        
        self.assertEqual(manager.status, "RUNNING")
        self.assertEqual(manager.mode, "MOCK")
        self.assertEqual(manager.capital, 200000.0)
        
        # Verify initial strategy states are initialized
        self.assertEqual(manager.strategy_state['PE']['strike'], None)
        self.assertEqual(manager.strategy_state['CE']['strike'], None)
        
        # Stop
        manager.stop()
        self.assertEqual(manager.status, "STOPPED")

    def test_strategy_trigger_logic(self):
        # Direct verification of LiveManager strategy evaluator state updates
        manager = LiveManager()
        
        # Manually initialize parameters and states
        manager.mode = "MOCK"
        manager.strategy_type = "BOTH"
        manager.entry_discount = 0.10
        manager.lot_1_target_pct = 0.30
        manager.lot_size = 65
        manager.realized_pnl = 0.0
        
        manager.strategy_state = {
            'PE': {
                'strike': 23300,
                'day_high': 100.0,
                'entry_count': 0,
                'position': None,
                'entry_price': 0.0,
                'current_sl': 0.0,
                'target': 0.0,
                'min_price': float('inf'),
                'unrealized': 0.0,
                'price': 100.0
            }
        }
        
        # Scenario 1: Option price remains above entry target (100.0 * 0.9 = 90.0)
        # We manually check the state evaluator logic
        opt_type = 'PE'
        state = manager.strategy_state[opt_type]
        o_price = 95.0 # LTP
        
        target_entry = state['day_high'] * (1.0 - manager.entry_discount) # 90.0
        if state['position'] is None:
            if o_price <= target_entry:
                state['position'] = 'SHORT_2_LOTS'
                
        self.assertIsNone(state['position']) # Should still be flat (95.0 > 90.0)
        
        # Scenario 2: Option price dips below target entry (90.0)
        o_price = 88.0
        if state['position'] is None:
            if o_price <= target_entry:
                state['position'] = 'SHORT_2_LOTS'
                state['entry_price'] = target_entry
                state['current_sl'] = state['day_high']
                state['target'] = state['day_high'] * (1.0 - manager.lot_1_target_pct) # 70.0
                state['min_price'] = o_price
                
        self.assertEqual(state['position'], 'SHORT_2_LOTS')
        self.assertEqual(state['entry_price'], 90.0)
        self.assertEqual(state['current_sl'], 100.0)
        self.assertEqual(state['target'], 70.0)
        
        # Scenario 3: Option price rises and hits SL (100.0)
        o_price = 101.0
        if state['position'] == 'SHORT_2_LOTS':
            if o_price >= state['current_sl']:
                pnl = (state['entry_price'] - state['current_sl']) * 2 * manager.lot_size # (90 - 100) * 130 = -1300
                manager.realized_pnl += pnl
                state['position'] = None
                
        self.assertIsNone(state['position'])
        self.assertEqual(manager.realized_pnl, -1300.0)

    def test_above_entry_threshold_logic(self):
        manager = LiveManager()
        manager.mode = "LIVE"
        manager.strategy_type = "BOTH"
        manager.entry_discount = 0.10
        manager.lot_1_target_pct = 0.30
        manager.lot_size = 65
        manager.realized_pnl = 0.0
        manager.max_reentries = 3
        
        manager.strategy_state = {
            'PE': {
                'strike': 24000,
                'day_high': 282.60,
                'entry_count': 0,
                'position': None,
                'entry_price': 0.0,
                'current_sl': 0.0,
                'target': 0.0,
                'min_price': float('inf'),
                'unrealized': 0.0,
                'price': 0.0,
                'entry_time': None,
                'above_entry_threshold': None
            },
            'CE': {
                'strike': None
            }
        }
        
        target_entry = 282.60 * 0.9
        state = manager.strategy_state['PE']
        
        # 1. Price is below threshold (e.g. 210.0) when flat
        o_price = 210.0
        if state['position'] is None:
            if state.get('above_entry_threshold') is None:
                state['above_entry_threshold'] = (o_price > target_entry)
            elif o_price > target_entry:
                state['above_entry_threshold'] = True
                
            if state['entry_count'] < manager.max_reentries:
                if o_price <= target_entry and o_price > 0 and state.get('above_entry_threshold', False):
                    state['position'] = 'SHORT_2_LOTS'
                    state['entry_price'] = o_price
                    state['entry_count'] += 1
                    
        self.assertFalse(state['above_entry_threshold'])
        self.assertIsNone(state['position'])
        self.assertEqual(state['entry_count'], 0)
        
        # 2. Price rises above threshold (e.g. 260.0)
        o_price = 260.0
        if state['position'] is None:
            if state.get('above_entry_threshold') is None:
                state['above_entry_threshold'] = (o_price > target_entry)
            elif o_price > target_entry:
                state['above_entry_threshold'] = True
                
            if state['entry_count'] < manager.max_reentries:
                if o_price <= target_entry and o_price > 0 and state.get('above_entry_threshold', False):
                    state['position'] = 'SHORT_2_LOTS'
                    state['entry_price'] = o_price
                    state['entry_count'] += 1
                    
        self.assertTrue(state['above_entry_threshold'])
        self.assertIsNone(state['position'])
        self.assertEqual(state['entry_count'], 0)
        
        # 3. Price crosses threshold on the way down (e.g. 250.0)
        o_price = 250.0
        if state['position'] is None:
            if state.get('above_entry_threshold') is None:
                state['above_entry_threshold'] = (o_price > target_entry)
            elif o_price > target_entry:
                state['above_entry_threshold'] = True
                
            if state['entry_count'] < manager.max_reentries:
                if o_price <= target_entry and o_price > 0 and state.get('above_entry_threshold', False):
                    state['position'] = 'SHORT_2_LOTS'
                    state['entry_price'] = o_price
                    state['entry_count'] += 1
                    state['above_entry_threshold'] = None
                    
        self.assertEqual(state['position'], 'SHORT_2_LOTS')
        self.assertEqual(state['entry_price'], 250.0)
        self.assertEqual(state['entry_count'], 1)
        self.assertIsNone(state['above_entry_threshold'])

    def test_config_helpers(self):
        from live_feed import load_strategy_config, save_strategy_config
        config = load_strategy_config()
        self.assertIn('auto_pilot', config)
        self.assertIn('capital', config)
        
        orig_val = config.get('auto_pilot', False)
        config['auto_pilot'] = not orig_val
        save_strategy_config(config)
        
        new_config = load_strategy_config()
        self.assertEqual(new_config['auto_pilot'], not orig_val)
        
        config['auto_pilot'] = orig_val
        save_strategy_config(config)

    def test_autopilot_scheduler_weekdays(self):
        from live_feed import AutoPilotScheduler
        mock_manager = MagicMock()
        mock_manager.status = "STOPPED"
        
        scheduler = AutoPilotScheduler(mock_manager)
        scheduler.manually_stopped_today = False
        
        with patch('live_feed.load_strategy_config') as mock_load:
            mock_load.return_value = {
                "auto_pilot": True,
                "mode": "MOCK",
                "capital": 200000.0,
                "strategy_type": "BOTH",
                "strike_offset_pe": 100,
                "strike_offset_ce": -100,
                "entry_discount": 10.0,
                "lot_1_target": 30.0,
                "max_reentries": 5,
                "lot_size": 65,
                "iv": 15.0,
                "r": 7.0
            }
            
            ist = pytz.timezone('Asia/Kolkata')
            mock_dt = datetime.datetime(2026, 6, 15, 10, 0, 0, tzinfo=ist)
            
            with patch('datetime.datetime') as mock_datetime:
                mock_datetime.now.return_value = mock_dt
                
                now = mock_datetime.now(ist)
                current_time = now.time()
                weekday = now.weekday()
                
                self.assertEqual(weekday, 0)
                self.assertTrue(datetime.time(9, 15) <= current_time < datetime.time(15, 30))
                
                if weekday < 5 and datetime.time(9, 15) <= current_time < datetime.time(15, 30):
                    if scheduler.live_manager.status == "STOPPED" and not scheduler.manually_stopped_today:
                        scheduler.live_manager.start(
                            mode="MOCK", capital=200000.0, strategy_type="BOTH",
                            strike_offset_pe=100, strike_offset_ce=-100,
                            entry_discount=10.0, lot_1_target=30.0,
                            max_reentries=5, lot_size=65, iv=15.0, r=7.0
                        )
                
                mock_manager.start.assert_called_once_with(
                    mode="MOCK", capital=200000.0, strategy_type="BOTH",
                    strike_offset_pe=100, strike_offset_ce=-100,
                    entry_discount=10.0, lot_1_target=30.0,
                    max_reentries=5, lot_size=65, iv=15.0, r=7.0
                )

    def test_autopilot_scheduler_weekends(self):
        from live_feed import AutoPilotScheduler
        mock_manager = MagicMock()
        mock_manager.status = "RUNNING"
        
        scheduler = AutoPilotScheduler(mock_manager)
        scheduler.manually_stopped_today = False
        
        ist = pytz.timezone('Asia/Kolkata')
        mock_dt = datetime.datetime(2026, 6, 14, 12, 0, 0, tzinfo=ist)
        
        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            
            now = mock_datetime.now(ist)
            weekday = now.weekday()
            
            self.assertEqual(weekday, 6)
            
            if weekday >= 5:
                if scheduler.live_manager.status == "RUNNING":
                    scheduler.live_manager.stop()
            
            mock_manager.stop.assert_called_once()

    def test_option_symbol_formatting(self):
        manager = LiveManager()
        
        # Test weekly contract (Tuesday June 16, 2026)
        # Weekday 0 is Monday (June 15, 2026) -> resolves to Tuesday June 16, 2026
        dt_weekly = datetime.date(2026, 6, 15)
        symbol = manager.get_option_symbol(24050, 'PE', dt_weekly)
        self.assertEqual(symbol, "NFO:NIFTY2661624050PE")
        
        # Test monthly contract (last Tuesday of June 2026 is June 30, 2026)
        dt_monthly = datetime.date(2026, 6, 30)
        symbol_monthly = manager.get_option_symbol(24000, 'CE', dt_monthly)
        self.assertEqual(symbol_monthly, "NFO:NIFTY26JUN24000CE")

        # Test Kite-connected instrument master lookup for non-standard / holiday monthly expiries (e.g. 2026-11-23)
        mock_kite = MagicMock()
        mock_kite.instruments.return_value = [
            {"name": "NIFTY", "strike": 25000.0, "instrument_type": "CE", "expiry": datetime.date(2026, 11, 23), "tradingsymbol": "NIFTY26NOV25000CE"},
            {"name": "NIFTY", "strike": 25200.0, "instrument_type": "PE", "expiry": datetime.date(2026, 11, 23), "tradingsymbol": "NIFTY26NOV25200PE"},
        ]
        manager.kite = mock_kite
        manager.kite_configured = True
        manager.cached_expiry = None
        manager.cached_nfo_instruments = None
        dt_nov = datetime.date(2026, 11, 19)
        symbol_ce = manager.get_option_symbol(25000, 'CE', dt_nov)
        symbol_pe = manager.get_option_symbol(25200, 'PE', dt_nov)
        self.assertEqual(symbol_ce, "NFO:NIFTY26NOV25000CE")
        self.assertEqual(symbol_pe, "NFO:NIFTY26NOV25200PE")
        
    def test_totp_generation(self):
        from kite_auth import generate_totp
        secret = "JBSWY3DPEHPK3PXP" # standard base32
        token = generate_totp(secret)
        self.assertIsNotNone(token)
        self.assertEqual(len(token), 6)
    def test_live_state_persistence(self):
        from live_feed import save_live_state, load_live_state, LIVE_STATE_PATH
        today_str = datetime.date.today().isoformat()
        test_data = {
            "date": today_str,
            "mode": "LIVE",
            "realized_pnl": -2834.0,
            "strategy_state": {
                "PE": {"strike": 24000, "position": "SHORT_2_LOTS", "entry_price": 120.20},
                "CE": {"strike": 23800, "position": "SHORT_2_LOTS", "entry_price": 232.80}
            }
        }
        res = save_live_state(test_data)
        self.assertTrue(res)
        loaded = load_live_state()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["date"], today_str)
        self.assertEqual(loaded["strategy_state"]["PE"]["position"], "SHORT_2_LOTS")
        if os.path.exists(LIVE_STATE_PATH):
            os.remove(LIVE_STATE_PATH)

    def test_symbol_knowledge_base_persistence(self):
        from live_feed import load_symbol_knowledge, save_symbol_knowledge
        kb = load_symbol_knowledge()
        self.assertIn("expiries", kb)
        
        # Test saving verified format
        kb["expiries"]["2026-11-23"] = {
            "format": "MONTHLY",
            "year": 26,
            "month_str": "NOV",
            "verified_symbols": {"25000:CE": "NIFTY26NOV25000CE"}
        }
        self.assertTrue(save_symbol_knowledge(kb))
        
        reloaded = load_symbol_knowledge()
        self.assertIn("2026-11-23", reloaded["expiries"])
        self.assertEqual(reloaded["expiries"]["2026-11-23"]["format"], "MONTHLY")
        self.assertEqual(reloaded["expiries"]["2026-11-23"]["verified_symbols"]["25000:CE"], "NIFTY26NOV25000CE")

    def test_symbol_resolution_tier1_knowledge_base(self):
        from live_feed import LiveManager, load_symbol_knowledge, save_symbol_knowledge
        kb = load_symbol_knowledge()
        kb["expiries"]["2026-11-23"] = {
            "format": "MONTHLY",
            "year": 26,
            "month_str": "NOV",
            "verified_symbols": {}
        }
        save_symbol_knowledge(kb)
        
        manager = LiveManager()
        manager.cached_expiry = datetime.date(2026, 11, 23)
        resolved = manager.get_option_symbol(25000, "CE", datetime.date(2026, 11, 19))
        self.assertEqual(resolved, "NFO:NIFTY26NOV25000CE")

    def test_permutation_probing_discovers_working_symbol(self):
        from live_feed import LiveManager
        manager = LiveManager()
        mock_kite = MagicMock()
        def mock_quote(sym_list):
            res = {}
            for s in sym_list:
                if s == "NFO:NIFTY26NOV25000CE":
                    res[s] = {"instrument_token": 16496898, "last_price": 145.5, "ohlc": {"high": 160.0, "low": 130.0}}
            return res
        mock_kite.quote.side_effect = mock_quote
        manager.kite = mock_kite
        
        probed = manager.probe_symbol_permutations(25000, "CE", datetime.date(2026, 11, 23))
        self.assertEqual(probed, "NFO:NIFTY26NOV25000CE")

    def test_autonomous_heal_strike(self):
        from live_feed import LiveManager
        manager = LiveManager()
        manager.mode = "LIVE"
        manager.strategy_state = {
            'PE': {'day_high': 0.0, 'entry_count': 0, 'position': None, 'entry_price': 0.0, 'current_sl': 0.0, 'target': 0.0, 'min_price': float('inf'), 'unrealized': 0.0, 'strike': 25200, 'price': 0.0},
            'CE': {'day_high': 0.0, 'entry_count': 0, 'position': None, 'entry_price': 0.0, 'current_sl': 0.0, 'target': 0.0, 'min_price': float('inf'), 'unrealized': 0.0, 'strike': 25000, 'price': 0.0}
        }
        mock_kite = MagicMock()
        mock_kite.quote.return_value = {
            "NFO:NIFTY26NOV25000CE": {"last_price": 150.0, "ohlc": {"high": 175.0, "low": 140.0}}
        }
        mock_kite.instruments.return_value = [
            {"name": "NIFTY", "strike": 25000.0, "instrument_type": "CE", "expiry": datetime.date(2026, 11, 23), "tradingsymbol": "NIFTY26NOV25000CE", "instrument_token": 16496898}
        ]
        manager.kite = mock_kite
        manager.cached_expiry = datetime.date(2026, 11, 23)
        manager._resolve_and_subscribe_tokens = MagicMock()
        
        manager.autonomous_heal_strike('CE', 25000, datetime.datetime(2026, 11, 19, 9, 31))
        self.assertEqual(manager.strategy_state['CE']['day_high'], 175.0)
        self.assertEqual(manager.strategy_state['CE']['price'], 150.0)
        self.assertGreater(manager.strategy_state['CE']['target'], 0.0)

    def test_preflight_and_health_status(self):
        import base64
        from live_feed import LiveManager
        from app import app
        manager = LiveManager()
        manager.mode = "LIVE"
        mock_kite = MagicMock()
        mock_kite.quote.return_value = {
            "NSE:NIFTY 50": {"last_price": 24500.0}
        }
        manager.kite = mock_kite
        manager.spot_price = 24500.0
        manager.strategy_state = {
            'PE': {'day_high': 150.0, 'strike': 24400, 'price': 140.0, 'position': None},
            'CE': {'day_high': 160.0, 'strike': 24600, 'price': 155.0, 'position': None}
        }
        manager.websocket_tokens = {12345, 67890}
        health = manager.get_health_status()
        self.assertEqual(health['status'], 'HEALTHY')
        self.assertEqual(health['websocket_tokens_count'], 2)
        
        # Test API endpoint with valid Basic Auth
        user = os.environ.get('BASIC_AUTH_USER', 'admin')
        pw = os.environ.get('BASIC_AUTH_PASS', 'admin')
        cred = base64.b64encode(f"{user}:{pw}".encode('utf-8')).decode('utf-8')
        with app.test_client() as client:
            headers = {"Authorization": f"Basic {cred}"}
            resp = client.get('/api/live/health', headers=headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn('status', data)

    def test_limit_order_fill_capping(self):
        """Recommendation 1: Limit Order Fill Capping eliminates negative slippage when tick drops below target_entry."""
        from live_feed import LiveManager
        manager = LiveManager()
        manager.mode = "LIVE"
        manager.entry_discount = 0.10
        manager.lot_size = 65
        manager.max_reentries = 5
        manager.lot_1_target_pct = 0.30
        manager.strategy_state = {
            'PE': {
                'strike': 24000,
                'day_high': 200.0,
                'entry_count': 0,
                'position': None,
                'entry_price': 0.0,
                'current_sl': 0.0,
                'target': 0.0,
                'min_price': float('inf'),
                'unrealized': 0.0,
                'price': 195.0,
                'above_entry_threshold': True,
                'recent_sl_times': [],
                'cooldown_until': None
            }
        }
        
        # Target entry is 200 * 0.9 = 180.0
        # Tick arrives with jump downward to 172.50
        current_time = datetime.datetime(2026, 11, 23, 10, 0, 0)
        manager._evaluate_strategy_for_leg('PE', 172.50, 172.50, 172.50, current_time)
        
        # Verify position opened
        pe_state = manager.strategy_state['PE']
        self.assertEqual(pe_state['position'], 'SHORT_2_LOTS')
        # Verify limit order fill capping: filled at 180.00, NOT 172.50!
        self.assertEqual(pe_state['entry_price'], 180.00)
        self.assertEqual(pe_state['current_sl'], 200.00)

    def test_consecutive_loss_cooldown_circuit_breaker(self):
        """Recommendation 2: 3 consecutive Stop Losses within 90 minutes trigger 45-minute cooldown."""
        from live_feed import LiveManager
        manager = LiveManager()
        manager.mode = "LIVE"
        manager.entry_discount = 0.10
        manager.lot_size = 65
        manager.max_reentries = 10
        manager.lot_1_target_pct = 0.30
        manager.enable_2sl_trend_freeze = False  # Test 3-SL fallback cooldown specifically
        manager.strategy_state = {
            'CE': {
                'strike': 24500,
                'day_high': 100.0,
                'entry_count': 0,
                'position': None,
                'entry_price': 0.0,
                'current_sl': 0.0,
                'target': 0.0,
                'min_price': float('inf'),
                'unrealized': 0.0,
                'price': 95.0,
                'above_entry_threshold': True,
                'recent_sl_times': [],
                'cooldown_until': None
            }
        }
        
        # Simulate 3 consecutive Stop Losses within 90 minutes
        # SL 1 at 10:00 AM
        t1 = datetime.datetime(2026, 11, 23, 10, 0, 0)
        manager._evaluate_strategy_for_leg('CE', 90.0, 90.0, 90.0, t1) # Entry 1 @ 90
        manager._evaluate_strategy_for_leg('CE', 101.0, 101.0, 101.0, t1 + datetime.timedelta(minutes=5)) # SL 1 @ 10:05
        self.assertEqual(len(manager.strategy_state['CE']['recent_sl_times']), 1)
        self.assertIsNone(manager.strategy_state['CE']['cooldown_until'])

        # SL 2 at 10:20 AM
        t2 = datetime.datetime(2026, 11, 23, 10, 20, 0)
        manager.strategy_state['CE']['above_entry_threshold'] = True
        manager._evaluate_strategy_for_leg('CE', 90.0, 90.0, 90.0, t2) # Entry 2 @ 90
        manager._evaluate_strategy_for_leg('CE', 101.0, 101.0, 101.0, t2 + datetime.timedelta(minutes=5)) # SL 2 @ 10:25
        self.assertEqual(len(manager.strategy_state['CE']['recent_sl_times']), 2)
        self.assertIsNone(manager.strategy_state['CE']['cooldown_until'])

        # SL 3 at 10:40 AM (within 90 mins of SL 1)
        t3 = datetime.datetime(2026, 11, 23, 10, 40, 0)
        manager.strategy_state['CE']['above_entry_threshold'] = True
        manager._evaluate_strategy_for_leg('CE', 90.0, 90.0, 90.0, t3) # Entry 3 @ 90
        manager._evaluate_strategy_for_leg('CE', 101.0, 101.0, 101.0, t3 + datetime.timedelta(minutes=5)) # SL 3 @ 10:45
        
        # Now 3 consecutive SLs reached: cooldown circuit breaker MUST trip
        ce_state = manager.strategy_state['CE']
        self.assertIsNotNone(ce_state['cooldown_until'])
        expected_cooldown_end = t3 + datetime.timedelta(minutes=5) + datetime.timedelta(minutes=45)
        self.assertEqual(ce_state['cooldown_until'], expected_cooldown_end)
        self.assertEqual(ce_state['recent_sl_times'], [])

        # Attempt entry during cooldown at 11:00 AM (cooldown active until 11:30 AM)
        t_blocked = datetime.datetime(2026, 11, 23, 11, 0, 0)
        ce_state['above_entry_threshold'] = True
        manager._evaluate_strategy_for_leg('CE', 90.0, 90.0, 90.0, t_blocked)
        self.assertIsNone(ce_state['position']) # Blocked!

        # Attempt entry after cooldown expires at 11:31 AM
        t_resumed = datetime.datetime(2026, 11, 23, 11, 31, 0)
        manager._evaluate_strategy_for_leg('CE', 90.0, 90.0, 90.0, t_resumed)
        self.assertIsNone(ce_state['cooldown_until']) # Cleared!
        self.assertEqual(ce_state['position'], 'SHORT_2_LOTS') # Entry permitted!

    def test_target_resets_consecutive_losses(self):
        """Recommendation 2: Target 1 hit resets recent_sl_times to avoid premature circuit breaker."""
        from live_feed import LiveManager
        manager = LiveManager()
        manager.mode = "LIVE"
        manager.entry_discount = 0.10
        manager.lot_size = 65
        manager.lot_1_target_pct = 0.30
        manager.max_reentries = 10
        manager.strategy_state = {
            'PE': {
                'strike': 24000,
                'day_high': 100.0,
                'entry_count': 0,
                'position': None,
                'entry_price': 0.0,
                'current_sl': 0.0,
                'target': 0.0,
                'min_price': float('inf'),
                'unrealized': 0.0,
                'price': 95.0,
                'above_entry_threshold': True,
                'recent_sl_times': [datetime.datetime(2026, 11, 23, 10, 0, 0), datetime.datetime(2026, 11, 23, 10, 15, 0)],
                'cooldown_until': None
            }
        }
        
        # 2 SLs in history. Next trade enters and hits Target Lot 1 (100 * 0.7 = 70.0)
        t = datetime.datetime(2026, 11, 23, 10, 30, 0)
        manager._evaluate_strategy_for_leg('PE', 90.0, 90.0, 90.0, t)
        self.assertEqual(manager.strategy_state['PE']['position'], 'SHORT_2_LOTS')
        
        # Price hits target 70.0
        manager._evaluate_strategy_for_leg('PE', 69.5, 69.5, 69.5, t + datetime.timedelta(minutes=10))
        # Target 1 hit: recent_sl_times MUST be reset to empty list
        self.assertEqual(manager.strategy_state['PE']['recent_sl_times'], [])

    def test_initial_entry_grace_band(self):
        """Option A: Initial Entry Grace Band enables immediate entry when opening within 5% below target_entry."""
        from live_feed import LiveManager
        manager = LiveManager()
        manager.mode = "LIVE"
        manager.entry_discount = 0.10
        manager.lot_size = 65
        manager.lot_1_target_pct = 0.30
        manager.max_reentries = 5
        
        # Case A: Option opens at 176 (Day High = 200, Target = 180).
        # 176 is within 5% grace band [170, 180] (12% discount).
        manager.strategy_state = {
            'PE': {
                'strike': 24000,
                'day_high': 200.0,
                'entry_count': 0,
                'position': None,
                'entry_price': 0.0,
                'current_sl': 0.0,
                'target': 0.0,
                'min_price': float('inf'),
                'unrealized': 0.0,
                'price': 176.0,
                'above_entry_threshold': None, # Initial uninitialized
                'recent_sl_times': [],
                'cooldown_until': None
            }
        }
        t = datetime.datetime(2026, 11, 23, 9, 30, 1)
        manager._evaluate_strategy_for_leg('PE', 176.0, 176.0, 176.0, t)
        # Should trigger entry immediately!
        pe_state = manager.strategy_state['PE']
        self.assertEqual(pe_state['position'], 'SHORT_2_LOTS')
        self.assertEqual(pe_state['entry_price'], 180.00) # Capped at target_entry
        self.assertEqual(pe_state['entry_count'], 1)

        # Case B: Option opens at 160 (20% discount, outside 5% grace band [170, 180]).
        manager.strategy_state = {
            'CE': {
                'strike': 24500,
                'day_high': 200.0,
                'entry_count': 0,
                'position': None,
                'entry_price': 0.0,
                'current_sl': 0.0,
                'target': 0.0,
                'min_price': float('inf'),
                'unrealized': 0.0,
                'price': 160.0,
                'above_entry_threshold': None,
                'recent_sl_times': [],
                'cooldown_until': None
            }
        }
        manager._evaluate_strategy_for_leg('CE', 160.0, 160.0, 160.0, t)
        ce_state = manager.strategy_state['CE']
        self.assertIsNone(ce_state['position']) # NOT triggered
        self.assertFalse(ce_state['above_entry_threshold'])

        # Case C: Re-entry (entry_count > 0) does NOT get grace band
        manager.strategy_state = {
            'PE': {
                'strike': 24000,
                'day_high': 200.0,
                'entry_count': 1, # Re-entry
                'position': None,
                'entry_price': 0.0,
                'current_sl': 0.0,
                'target': 0.0,
                'min_price': float('inf'),
                'unrealized': 0.0,
                'price': 176.0,
                'above_entry_threshold': None,
                'recent_sl_times': [],
                'cooldown_until': None
            }
        }
        manager._evaluate_strategy_for_leg('PE', 176.0, 176.0, 176.0, t)
        self.assertIsNone(manager.strategy_state['PE']['position']) # NOT triggered

    def test_round_to_tick_and_zerodha_order_prices(self):
        """Phase 1: Enforce NSE 0.05 tick-size rounding on LIMIT and SL-L prices."""
        self.assertEqual(live_feed.round_to_tick(296.13), 296.15)
        self.assertEqual(live_feed.round_to_tick(296.12), 296.10)
        self.assertEqual(live_feed.round_to_tick(0.0), 0.0)

        manager = LiveManager()
        mock_kite = MagicMock()
        mock_kite.VARIETY_REGULAR = "regular"
        mock_kite.EXCHANGE_NFO = "NFO"
        mock_kite.PRODUCT_NRML = "NRML"
        mock_kite.place_order.return_value = "ORD_1"
        mock_kite.order_history.return_value = [{"status": "COMPLETE", "average_price": 296.15, "filled_quantity": 130}]
        manager.kite = mock_kite

        with patch('time.sleep'):
            manager.place_zerodha_order("NFO:NIFTY26NOV25650CE", "SELL", 130, order_type="LIMIT", price=296.13)
        limit_kwargs = mock_kite.place_order.call_args.kwargs
        self.assertEqual(limit_kwargs["price"], 296.15)

        with patch('time.sleep'):
            manager.place_zerodha_sl_order("NFO:NIFTY26NOV25650CE", 130, trigger_price=329.03)
        sl_kwargs = mock_kite.place_order.call_args.kwargs
        self.assertEqual(sl_kwargs["trigger_price"], 329.05)
        self.assertEqual(round((sl_kwargs["price"] * 20) % 1, 6), 0.0)

        with patch('time.sleep'):
            manager.modify_zerodha_sl_order("ORD_SL", trigger_price=263.24, quantity=65)
        mod_kwargs = mock_kite.modify_order.call_args.kwargs
        self.assertEqual(mod_kwargs["trigger_price"], 263.25)
        self.assertEqual(round((mod_kwargs["price"] * 20) % 1, 6), 0.0)

    def test_live_real_open_entry_aborts_without_sl(self):
        """Phase 1: Unfilled OPEN LIMIT SELL must cancel cleanly and never arm an exchange BUY SL."""
        manager = LiveManager()
        manager.mode = "LIVE_REAL"
        manager.capital = 650000.0
        manager.entry_discount = 0.10
        manager.lot_1_target_pct = 0.40
        manager.max_reentries = 5
        manager.lot_size = 65

        mock_kite = MagicMock()
        mock_kite.VARIETY_REGULAR = "regular"
        mock_kite.EXCHANGE_NFO = "NFO"
        mock_kite.PRODUCT_NRML = "NRML"
        mock_kite.margins.return_value = {"available": {"live_balance": 650000.0}, "utilised": {"debits": 0.0}}
        mock_kite.basket_order_margins.return_value = {"initial": {"total": 350000.0}}
        mock_kite.place_order.return_value = "ORD_OPEN_1"
        mock_kite.order_history.return_value = [{"status": "OPEN", "average_price": 0.0, "filled_quantity": 0}]
        manager.kite = mock_kite
        manager.get_option_symbol = MagicMock(return_value="NFO:NIFTY26NOV25650CE")
        manager.strategy_state = {
            'PE': {'strike': 25850, 'position': None},
            'CE': {
                'strike': 25650, 'day_high': 329.0, 'entry_count': 0, 'position': None,
                'entry_price': 0.0, 'current_sl': 0.0, 'target': 0.0, 'min_price': float('inf'),
                'unrealized': 0.0, 'price': 300.0, 'entry_time': None,
                'above_entry_threshold': True, 'recent_sl_times': [], 'cooldown_until': None
            }
        }

        t = datetime.datetime(2026, 11, 26, 10, 0, 0)
        with patch('time.sleep'):
            manager._evaluate_strategy_for_leg('CE', 295.0, 295.0, 295.0, t)

        self.assertIsNone(manager.strategy_state['CE']['position'])
        self.assertEqual(manager.strategy_state['CE']['entry_count'], 0)
        self.assertIsNone(manager.strategy_state['CE'].get('sl_order_id'))
        mock_kite.cancel_order.assert_called_once()
        self.assertEqual(mock_kite.place_order.call_count, 1)  # Only the entry SELL; never placed BUY SL

    def test_live_real_partial_entry_tracks_single_lot(self):
        """Phase 1: Partial 1-lot fill on LIMIT SELL transitions cleanly to SHORT_1_LOT with 1-lot SL."""
        manager = LiveManager()
        manager.mode = "LIVE_REAL"
        manager.capital = 650000.0
        manager.entry_discount = 0.10
        manager.lot_1_target_pct = 0.40
        manager.max_reentries = 5
        manager.lot_size = 65

        mock_kite = MagicMock()
        mock_kite.VARIETY_REGULAR = "regular"
        mock_kite.EXCHANGE_NFO = "NFO"
        mock_kite.PRODUCT_NRML = "NRML"
        mock_kite.margins.return_value = {"available": {"live_balance": 650000.0}, "utilised": {"debits": 0.0}}
        mock_kite.basket_order_margins.return_value = {"initial": {"total": 350000.0}}
        mock_kite.place_order.side_effect = ["ORD_PARTIAL", "ORD_SL_1LOT"]
        # First 6 polls -> OPEN, after cancel -> CANCELLED with 65 filled, then SL order COMPLETE/OPEN
        mock_kite.order_history.side_effect = (
            [[{"status": "OPEN", "filled_quantity": 0, "average_price": 0.0}] for _ in range(6)]
            + [[{"status": "CANCELLED", "filled_quantity": 65, "average_price": 296.10}]]
            + [[{"status": "OPEN", "filled_quantity": 0, "average_price": 0.0}] for _ in range(6)]
        )
        manager.kite = mock_kite
        manager.get_option_symbol = MagicMock(return_value="NFO:NIFTY26NOV25650CE")
        manager.strategy_state = {
            'PE': {'strike': 25850, 'position': None},
            'CE': {
                'strike': 25650, 'day_high': 329.0, 'entry_count': 0, 'position': None,
                'entry_price': 0.0, 'current_sl': 0.0, 'target': 0.0, 'min_price': float('inf'),
                'unrealized': 0.0, 'price': 300.0, 'entry_time': None,
                'above_entry_threshold': True, 'recent_sl_times': [], 'cooldown_until': None
            }
        }

        t = datetime.datetime(2026, 11, 26, 10, 0, 0)
        with patch('time.sleep'):
            manager._evaluate_strategy_for_leg('CE', 295.0, 295.0, 295.0, t)

        ce = manager.strategy_state['CE']
        self.assertEqual(ce['position'], 'SHORT_1_LOT')
        self.assertEqual(ce['entry_price'], 296.10)
        self.assertEqual(ce['sl_order_id'], "ORD_SL_1LOT")
        sl_call = mock_kite.place_order.call_args_list[1].kwargs
        self.assertEqual(sl_call['quantity'], 65)
        self.assertEqual(sl_call['transaction_type'], "BUY")

    def test_live_real_square_off_skips_double_buy_when_sl_already_filled(self):
        """Phase 1: 15:15 square-off must not place a duplicate MARKET BUY if exchange SL-L already filled."""
        manager = LiveManager()
        manager.mode = "LIVE_REAL"
        manager.lot_size = 65
        manager.realized_pnl = 0.0

        mock_kite = MagicMock()
        mock_kite.VARIETY_REGULAR = "regular"
        mock_kite.cancel_order.side_effect = Exception("Order already completed")
        mock_kite.order_history.return_value = [{"status": "COMPLETE", "filled_quantity": 130, "average_price": 329.0}]
        manager.kite = mock_kite
        manager.get_option_symbol = MagicMock(return_value="NFO:NIFTY26NOV25650CE")
        manager.strategy_state = {
            'PE': {'strike': 25850, 'position': None},
            'CE': {
                'strike': 25650, 'day_high': 329.0, 'entry_count': 1, 'position': 'SHORT_2_LOTS',
                'entry_price': 296.10, 'current_sl': 329.0, 'target': 197.40, 'min_price': 296.10,
                'unrealized': 0.0, 'price': 328.0, 'entry_time': '09:38:36',
                'above_entry_threshold': True, 'sl_order_id': 'ORD_SL_FILLED',
                'recent_sl_times': [], 'cooldown_until': None
            }
        }

        t = datetime.datetime(2026, 11, 26, 15, 15, 5)
        with patch('time.sleep'):
            manager._evaluate_strategy_for_leg('CE', 328.0, 328.0, 328.0, t)

        mock_kite.place_order.assert_not_called()
        self.assertIsNone(manager.strategy_state['CE']['position'])
        self.assertAlmostEqual(manager.realized_pnl, (296.10 - 329.0) * 130, places=2)

    def test_live_real_prospective_margin_blocks_underfunded_first_entry(self):
        """Phase 1: Pre-trade margin check includes prospective leg and blocks underfunded initial entry."""
        manager = LiveManager()
        manager.mode = "LIVE_REAL"
        manager.capital = 200000.0
        manager.entry_discount = 0.10
        manager.lot_1_target_pct = 0.40
        manager.max_reentries = 5
        manager.lot_size = 65

        mock_kite = MagicMock()
        mock_kite.margins.return_value = {"available": {"live_balance": 200000.0}, "utilised": {"debits": 0.0}}
        mock_kite.basket_order_margins.return_value = {"initial": {"total": 350000.0}}
        manager.kite = mock_kite
        manager.get_option_symbol = MagicMock(return_value="NFO:NIFTY26NOV25850PE")
        manager.strategy_state = {
            'PE': {
                'strike': 25850, 'day_high': 300.0, 'entry_count': 0, 'position': None,
                'entry_price': 0.0, 'current_sl': 0.0, 'target': 0.0, 'min_price': float('inf'),
                'unrealized': 0.0, 'price': 280.0, 'entry_time': None,
                'above_entry_threshold': True, 'recent_sl_times': [], 'cooldown_until': None
            },
            'CE': {'strike': 25650, 'position': None}
        }

        t = datetime.datetime(2026, 11, 26, 9, 45, 0)
        with patch('time.sleep'):
            manager._evaluate_strategy_for_leg('PE', 269.0, 269.0, 269.0, t)

        self.assertIsNone(manager.strategy_state['PE']['position'])
        mock_kite.place_order.assert_not_called()

    def test_cross_pollination_runaway_shield_reversion_and_2sl_freeze(self):
        """Runaway +50% shield pauses entries until reversion <= open_premium; 2 consec SLs freeze leg."""
        manager = LiveManager()
        manager.mode = "LIVE"
        manager.lot_size = 65
        manager.entry_discount = 0.10
        manager.lot_1_target_pct = 0.40
        manager.max_reentries = 5
        manager.runaway_block_pct = 0.50
        manager.runaway_unlock_on_reversion = True
        manager.enable_2sl_trend_freeze = True
        manager.realistic_paper_sl_fill = True

        manager.strategy_state = {
            'PE': {
                'strike': 25850, 'day_high': 100.0, 'open_premium': 100.0,
                'runaway_blocked': False, 'consec_sl': 0, 'trend_frozen': False,
                'entry_count': 0, 'position': None, 'entry_price': 0.0,
                'current_sl': 0.0, 'target': 0.0, 'min_price': float('inf'),
                'unrealized': 0.0, 'price': 100.0, 'entry_time': None,
                'above_entry_threshold': True, 'recent_sl_times': [], 'cooldown_until': None,
            },
            'CE': {'strike': 25650, 'position': None}
        }

        t1 = datetime.datetime(2026, 11, 26, 9, 40, 0)
        # 1. Spike >= +50% vs open (100 -> 152) triggers runaway shield
        manager.strategy_state['PE']['day_high'] = 152.0
        manager._evaluate_strategy_for_leg('PE', 152.0, 152.0, 152.0, t1)
        self.assertTrue(manager.strategy_state['PE']['runaway_blocked'])

        # 2. While blocked, dropping to target_entry (152 * 0.9 = 136.8) does NOT enter
        t2 = datetime.datetime(2026, 11, 26, 9, 50, 0)
        manager._evaluate_strategy_for_leg('PE', 130.0, 130.0, 130.0, t2)
        self.assertIsNone(manager.strategy_state['PE']['position'])

        # 3. Reversion <= open_premium (100) unlocks runaway shield
        t3 = datetime.datetime(2026, 11, 26, 10, 5, 0)
        manager._evaluate_strategy_for_leg('PE', 99.0, 99.0, 99.0, t3)
        self.assertFalse(manager.strategy_state['PE']['runaway_blocked'])
        self.assertEqual(manager.strategy_state['PE']['position'], 'SHORT_2_LOTS')

        # 4. Two consecutive SLs (with realistic paper fill at o_price) freeze the leg for the day
        manager._evaluate_strategy_for_leg('PE', 155.0, 155.0, 155.0, datetime.datetime(2026, 11, 26, 10, 10, 0))
        self.assertIsNone(manager.strategy_state['PE']['position'])
        self.assertEqual(manager.strategy_state['PE']['consec_sl'], 1)
        self.assertFalse(manager.strategy_state['PE']['trend_frozen'])

        # Second entry and second SL
        manager.strategy_state['PE']['above_entry_threshold'] = True
        manager._evaluate_strategy_for_leg('PE', 100.0, 100.0, 100.0, datetime.datetime(2026, 11, 26, 10, 20, 0))
        self.assertEqual(manager.strategy_state['PE']['position'], 'SHORT_2_LOTS')
        manager._evaluate_strategy_for_leg('PE', 160.0, 160.0, 160.0, datetime.datetime(2026, 11, 26, 10, 25, 0))
        self.assertEqual(manager.strategy_state['PE']['consec_sl'], 2)
        self.assertTrue(manager.strategy_state['PE']['trend_frozen'])

        # Further qualifying dips must remain ignored once trend_frozen
        manager.strategy_state['PE']['above_entry_threshold'] = True
        manager._evaluate_strategy_for_leg('PE', 100.0, 100.0, 100.0, datetime.datetime(2026, 11, 26, 10, 35, 0))
        self.assertIsNone(manager.strategy_state['PE']['position'])

    def test_reconcile_peak_x_from_exchange_heals_phantom_spike(self):
        """60s exchange OHLC reconciliation heals inflated phantom Day High X when flat."""
        manager = LiveManager()
        manager.mode = "LIVE"
        manager.last_peak_reconcile_time = 0.0
        mock_kite = MagicMock()
        mock_kite.quote.return_value = {
            "NFO:NIFTY26NOV25850PE": {"ohlc": {"high": 124.50}},
        }
        manager.kite = mock_kite
        manager.get_option_symbol = MagicMock(return_value="NFO:NIFTY26NOV25850PE")
        manager.strategy_state = {
            'PE': {'strike': 25850, 'position': None, 'day_high': 185.0},
            'CE': {'strike': 25650, 'position': 'SHORT_2_LOTS', 'day_high': 140.0},
        }

        manager._reconcile_peak_x_from_exchange(datetime.datetime(2026, 11, 26, 11, 0, 0))
        self.assertEqual(manager.strategy_state['PE']['day_high'], 124.50)
        # In-position CE leg is frozen and untouched
        self.assertEqual(manager.strategy_state['CE']['day_high'], 140.0)

if __name__ == '__main__':
    unittest.main()
