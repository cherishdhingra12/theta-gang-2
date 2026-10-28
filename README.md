# Theta Gang 2 — Nifty Intraday Trading Bot & Backtester

An institutional-grade algorithmic option selling engine and intraday backtester for Nifty 50 Index options, built with Python, Flask, Zerodha Kite Connect, and Black-Scholes pricing.

---

## 🚀 Features

- **Historical Backtesting Engine**:
  - Simulates 1-minute bar-by-bar Nifty options backtests using Black-Scholes pricing or historical CSV data.
  - Generates comprehensive equity curves, win rate, profit factor, Sharpe ratio, and drawdown analysis.
- **Playback Simulator (Historical Paper Trading)**:
  - Step through any historical day minute-by-minute with an interactive speed slider (1x real-time to 60x fast-forward).
- **Live Trading Terminal & Real-Time Engine**:
  - Live tick streaming via Zerodha Kite Connect WebSocket (`KiteTicker`).
  - Automated strike selection (ATM/OTM call/put strikes).
  - Multi-lot target scaling (40% profit target on Lot 1) and trailing stop losses on remaining lots.
  - Re-entry execution on stop-loss triggers up to configurable limits.
  - Real-time Server-Sent Events (SSE) telemetry and MTM dashboard.
- **Autonomous Auto-Pilot Scheduler**:
  - Automatic market holiday calendar tracking (NSE trading holidays).
  - Wakes up automatically at 09:08 AM IST on trading days.
  - Automatically squares off positions at 15:30 PM IST.

---

## 🛠️ Architecture

```
Theta_Gang_2/
├── app.py                  # Main Flask application and REST/SSE API endpoints
├── config.json             # Runtime strategy parameters (lot size, offsets, targets)
├── requirements.txt        # Production dependencies
├── backtester/
│   ├── engine.py           # Black-Scholes pricing model & core backtesting logic
│   └── playback.py         # Historical minute-by-minute playback simulator
├── live_bot/
│   ├── live_feed.py        # Real-time WebSocket manager & Auto-Pilot scheduler
│   ├── kite_auth.py        # Kite authentication helper
│   └── nse_holidays.py     # NSE holiday calendar & market hours verification
├── static/                 # CSS & JavaScript UI assets
├── templates/              # Dashboard interfaces (index, paper, live)
└── tests/                  # Unit test suite (19 test cases)
```

---

## 📦 Getting Started

### 1. Clone the repository
```bash
git clone https://github.com/cherishdhingra12/theta-gang-2.git
cd theta-gang-2
```

### 2. Setup Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Configure your credentials in `.env`:
```env
KITE_API_KEY=your_api_key
KITE_API_SECRET=your_api_secret
KITE_USER_ID=your_user_id
KITE_PASSWORD=your_password
KITE_TOTP_SECRET=your_totp_secret

BASIC_AUTH_USER=admin
BASIC_AUTH_PASS=your_secure_password
```

### 4. Run Unit Tests
```bash
python3 -m unittest discover tests/
```

### 5. Launch the Web Console
```bash
python3 app.py
```
Open your browser at `http://127.0.0.1:8080` (or `http://127.0.0.1:5001`).

---

## 🔒 Security Notice

Never commit your `.env` file or broker credentials to version control. Keep credentials safe and enable 2FA / TOTP on your broker account.
