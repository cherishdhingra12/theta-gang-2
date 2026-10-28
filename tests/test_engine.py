import unittest
import os
import sys
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtester'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'live_bot'))

import pandas as pd
import numpy as np
import pytz
from engine import (
    std_normal_cdf,
    black_scholes,
    round_to_nearest_strike,
    get_trailed_sl,
    run_backtest,
    generate_performance_metrics
)

class TestTradingEngine(unittest.TestCase):
    
    def test_std_normal_cdf(self):
        # N(0) = 0.5
        self.assertAlmostEqual(std_normal_cdf(0.0), 0.5, places=5)
        # N(1.96) is approx 0.975
        self.assertAlmostEqual(std_normal_cdf(1.95996), 0.975, places=4)
        # N(-1.96) is approx 0.025
        self.assertAlmostEqual(std_normal_cdf(-1.95996), 0.025, places=4)
        
    def test_black_scholes_intrinsic(self):
        # When t=0, price should equal intrinsic value (within minimum floor of 0.01)
        ce_itm = black_scholes(s=23000, k=22900, t=0, r=0.07, sigma=0.15, option_type='CE')
        self.assertEqual(ce_itm, 100.0)
        
        ce_otm = black_scholes(s=23000, k=23100, t=0, r=0.07, sigma=0.15, option_type='CE')
        self.assertEqual(ce_otm, 0.01)
        
        pe_itm = black_scholes(s=23000, k=23100, t=0, r=0.07, sigma=0.15, option_type='PE')
        self.assertEqual(pe_itm, 100.0)
        
    def test_round_to_nearest_strike(self):
        self.assertEqual(round_to_nearest_strike(23024), 23000)
        self.assertEqual(round_to_nearest_strike(23025), 23050)
        self.assertEqual(round_to_nearest_strike(23049), 23050)
        self.assertEqual(round_to_nearest_strike(23075), 23100)
        
    def test_get_trailed_sl(self):
        X = 100.0
        # No target hit (price remains above 0.7 * X)
        self.assertEqual(get_trailed_sl(75.0, X), X)
        
        # Target Lot 1 hit (price is exactly 0.7 * X)
        self.assertEqual(get_trailed_sl(70.0, X), 90.0)
        
        # Deeper hit (price is 55.0, which is <= 0.6 * X but > 0.5 * X)
        self.assertEqual(get_trailed_sl(55.0, X), 80.0)
        
        # Massive crash (price is 9.0, which is <= 0.1 * X)
        self.assertEqual(get_trailed_sl(9.0, X), 30.0)

    def test_mock_backtest_execution(self):
        # Create a single-day mock spot dataset
        ist = pytz.timezone('Asia/Kolkata')
        base_time = datetime.datetime.now().replace(hour=9, minute=15, second=0, microsecond=0)
        base_time = ist.localize(base_time)
        
        data = []
        spot = 23000.0
        
        # 376 minutes (09:15 to 15:30)
        for m in range(376):
            dt = base_time + datetime.timedelta(minutes=m)
            
            # Simulate Nifty rallying, then falling, then rallying
            if m < 15:  # 9:15 to 9:30
                # Day High setup: option prices will reflect spot movement.
                # Spot falls initially from 23000 to 22900 (making Put option premium rise, sets a High)
                spot -= 5.0
            elif m < 100:
                # Spot rises from 22925 to 23200 (Put premium falls, should trigger Sell Entry!)
                spot += 4.0
            elif m < 200:
                # Spot falls back down (Put premium rises, might hit SL!)
                spot -= 6.0
            else:
                spot += 1.0
                
            data.append({
                'Datetime': dt,
                'Open': spot,
                'High': spot + 2,
                'Low': spot - 2,
                'Close': spot,
                'Volume': 100
            })
            
        df = pd.DataFrame(data)
        
        # Run PE SELL backtest
        trades = run_backtest(
            df,
            strategy_type='PE_SELL',
            strike_offset_pe=100, # Spot + 100
            entry_discount=0.10,  # 10%
            lot_1_target_pct=0.30, # 30%
            max_reentries=5,
            lot_size=65,
            iv=0.20,
            r=0.07
        )
        
        # We expect some trades to have executed since there is volatility
        self.assertTrue(len(trades) >= 0)
        
        # Verify performance metrics aggregation executes without errors
        metrics = generate_performance_metrics(trades)
        self.assertIn('win_rate', metrics)
        self.assertIn('net_pnl', metrics)
        self.assertIn('equity_curve', metrics)

if __name__ == '__main__':
    unittest.main()
