"""
NSE Trading Holidays Calendar & Helpers
Contains official trading holidays declared by the National Stock Exchange of India (NSE).
Supports equities, equity derivatives (F&O), and currency derivatives.
"""
import datetime
import pytz

NSE_HOLIDAYS_2025 = frozenset({
    "2025-01-01", "2025-01-26", "2025-02-26", "2025-03-14",
    "2025-03-31", "2025-04-03", "2025-04-14", "2025-04-18",
    "2025-05-01", "2025-06-07", "2025-07-17", "2025-08-15",
    "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22",
    "2025-11-05", "2025-12-25",
})

HOLIDAY_NAMES_2025 = {
    "2025-01-01": "New Year",
    "2025-01-26": "Republic Day",
    "2025-02-26": "Maha Shivaratri",
    "2025-03-14": "Holi",
    "2025-03-31": "Id-ul-Fitr",
    "2025-04-03": "Ram Navami",
    "2025-04-14": "Dr. Ambedkar Jayanti",
    "2025-04-18": "Good Friday",
    "2025-05-01": "Maharashtra Day",
    "2025-06-07": "Bakri Id",
    "2025-07-17": "Muharram",
    "2025-08-15": "Independence Day",
    "2025-08-27": "Ganesh Chaturthi",
    "2025-10-02": "Mahatma Gandhi Jayanti",
    "2025-10-21": "Dussehra",
    "2025-10-22": "Diwali Laxmi Pujan",
    "2025-11-05": "Prakash Gurpurab",
    "2025-12-25": "Christmas",
}

NSE_HOLIDAYS_2026 = frozenset({
    "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28",
    "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20",
    "2026-11-09", "2026-11-10", "2026-11-24", "2026-12-25",
})

HOLIDAY_NAMES_2026 = {
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Ram Navami",
    "2026-03-31": "Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-09": "Diwali (Muhurat Trading)",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Guru Nanak Jayanti",
    "2026-12-25": "Christmas",
}

# Combined holidays
NSE_HOLIDAYS = NSE_HOLIDAYS_2025 | NSE_HOLIDAYS_2026
HOLIDAY_NAMES = {**HOLIDAY_NAMES_2025, **HOLIDAY_NAMES_2026}


def _normalize_date_str(dt=None):
    if dt is None:
        dt = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
    if hasattr(dt, 'date'):
        dt = dt.date()
    if isinstance(dt, datetime.date):
        return dt.strftime('%Y-%m-%d'), dt
    elif isinstance(dt, str):
        try:
            parsed = datetime.datetime.strptime(dt, '%Y-%m-%d').date()
            return dt, parsed
        except Exception:
            return dt, None
    return str(dt), None


def is_trading_holiday(dt=None):
    """Returns True if the specified date (or current IST date) is an NSE trading holiday."""
    d_str, _ = _normalize_date_str(dt)
    return d_str in NSE_HOLIDAYS


def get_holiday_name(dt=None):
    """Returns the holiday name if dt is an NSE holiday, else None."""
    d_str, _ = _normalize_date_str(dt)
    return HOLIDAY_NAMES.get(d_str, None)


def get_market_day_status(dt=None):
    """
    Evaluates whether the specified day (or current IST day) is an active trading day.
    Returns: (is_trading_day: bool, reason: str)
    e.g. (False, 'Weekend (Saturday)'), (False, 'Holiday: Ganesh Chaturthi'), (True, 'Trading Day')
    """
    d_str, date_obj = _normalize_date_str(dt)
    if date_obj is None:
        return True, 'Trading Day'
        
    weekday = date_obj.weekday()
    if weekday == 5:
        return False, 'Weekend (Saturday)'
    elif weekday == 6:
        return False, 'Weekend (Sunday)'
        
    if d_str in NSE_HOLIDAYS:
        h_name = HOLIDAY_NAMES.get(d_str, 'Holiday')
        return False, f'Holiday: {h_name}'
        
    return True, 'Trading Day'


def is_market_hours_now(dt=None):
    """
    Checks if current IST time is within NSE market trading hours (09:08 to 15:30 IST)
    on an active trading day.
    Returns: (is_open: bool, status_str: str)
    """
    if dt is None:
        dt = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
    is_trading_day, reason = get_market_day_status(dt)
    if not is_trading_day:
        return False, f'CLOSED ({reason})'
        
    t_time = dt.time()
    if t_time < datetime.time(9, 8):
        return False, 'PRE_OPEN'
    elif t_time > datetime.time(15, 30):
        return False, 'CLOSED (After Hours)'
        
    return True, 'OPEN'
