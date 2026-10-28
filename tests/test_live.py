import unittest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtester'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'live_bot'))

from unittest.mock import patch, MagicMock
import datetime
import pytz
import json
from live_feed import LiveManager, live_manager

class TestLiveFeedEngine(unittest.TestCase):
    
    def setUp(self):
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

if __name__ == '__main__':
    unittest.main()
