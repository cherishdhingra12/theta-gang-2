import unittest
import os
import sys
import datetime
import pytz

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtester'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'live_bot'))

from nse_holidays import (
    is_trading_holiday,
    get_holiday_name,
    get_market_day_status,
    is_market_hours_now,
    NSE_HOLIDAYS_2026
)
from live_feed import LiveManager, AutoPilotScheduler


class TestNSEHolidays(unittest.TestCase):
    def test_holiday_detection(self):
        # 2026-09-14 is Ganesh Chaturthi
        self.assertTrue(is_trading_holiday('2026-09-14'))
        self.assertEqual(get_holiday_name('2026-09-14'), 'Ganesh Chaturthi')

        # 2026-10-02 is Gandhi Jayanti
        self.assertTrue(is_trading_holiday('2026-10-02'))
        self.assertEqual(get_holiday_name('2026-10-02'), 'Gandhi Jayanti')

        # 2026-09-15 is regular trading day (Tuesday)
        self.assertFalse(is_trading_holiday('2026-09-15'))
        self.assertIsNone(get_holiday_name('2026-09-15'))

    def test_market_day_status(self):
        # Ganesh Chaturthi
        dt_holiday = datetime.date(2026, 9, 14)
        is_open, reason = get_market_day_status(dt_holiday)
        self.assertFalse(is_open)
        self.assertEqual(reason, 'Holiday: Ganesh Chaturthi')

        # Saturday
        dt_sat = datetime.date(2026, 9, 12)
        is_open, reason = get_market_day_status(dt_sat)
        self.assertFalse(is_open)
        self.assertEqual(reason, 'Weekend (Saturday)')

        # Sunday
        dt_sun = datetime.date(2026, 9, 13)
        is_open, reason = get_market_day_status(dt_sun)
        self.assertFalse(is_open)
        self.assertEqual(reason, 'Weekend (Sunday)')

        # Regular weekday (Tuesday)
        dt_tue = datetime.date(2026, 9, 15)
        is_open, reason = get_market_day_status(dt_tue)
        self.assertTrue(is_open)
        self.assertEqual(reason, 'Trading Day')

    def test_market_hours_now(self):
        ist = pytz.timezone('Asia/Kolkata')
        
        # Holiday at 10:00 AM IST
        dt_holiday_10am = ist.localize(datetime.datetime(2026, 9, 14, 10, 0, 0))
        is_open, status_str = is_market_hours_now(dt_holiday_10am)
        self.assertFalse(is_open)
        self.assertEqual(status_str, 'CLOSED (Holiday: Ganesh Chaturthi)')

        # Regular day at 08:30 AM IST (Pre-open)
        dt_tue_830am = ist.localize(datetime.datetime(2026, 9, 15, 8, 30, 0))
        is_open, status_str = is_market_hours_now(dt_tue_830am)
        self.assertFalse(is_open)
        self.assertEqual(status_str, 'PRE_OPEN')

        # Regular day at 10:00 AM IST (Open)
        dt_tue_10am = ist.localize(datetime.datetime(2026, 9, 15, 10, 0, 0))
        is_open, status_str = is_market_hours_now(dt_tue_10am)
        self.assertTrue(is_open)
        self.assertEqual(status_str, 'OPEN')

        # Regular day at 16:00 PM IST (Closed after hours)
        dt_tue_4pm = ist.localize(datetime.datetime(2026, 9, 15, 16, 0, 0))
        is_open, status_str = is_market_hours_now(dt_tue_4pm)
        self.assertFalse(is_open)
        self.assertEqual(status_str, 'CLOSED (After Hours)')

    def _start_manager(self, manager, mode):
        manager.start(
            mode=mode,
            capital=500000.0,
            strategy_type='BOTH',
            strike_offset_pe=100,
            strike_offset_ce=-100,
            entry_discount=10.0,
            lot_1_target=40.0,
            max_reentries=5,
            lot_size=65,
            iv=15.0,
            r=7.0
        )

    def test_live_manager_refuses_start_on_holiday(self):
        manager = LiveManager()
        # Mock get_current_time to return 2026-09-14 10:00 AM IST
        ist = pytz.timezone('Asia/Kolkata')
        manager.get_current_time = lambda: ist.localize(datetime.datetime(2026, 9, 14, 10, 0, 0))
        
        # Attempt to start in LIVE mode
        self._start_manager(manager, mode='LIVE')
        self.assertEqual(manager.status, 'STOPPED')
        self.assertIn('Market is closed today (Holiday: Ganesh Chaturthi)', manager.logs[-1])

    def test_live_manager_allows_mock_on_holiday(self):
        manager = LiveManager()
        # Mock mode should still be allowed for local testing/simulation
        self._start_manager(manager, mode='MOCK')
        self.assertEqual(manager.status, 'RUNNING')
        manager.stop()
        self.assertEqual(manager.status, 'STOPPED')


if __name__ == '__main__':
    unittest.main()
