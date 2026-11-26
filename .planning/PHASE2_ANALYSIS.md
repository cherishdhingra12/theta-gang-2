# Phase 2: Empirical & Backtest Strategy Analysis (CE vs. PE)

**Date**: 2026-11-26

## 1. Empirical Counterfactuals (`live_trades.json`, 304 Live-Feed Trades, Jun–Nov 2026)

| Configuration | Trades | Gross PnL | Trading Friction (STT/Brok/Exch/GST/Stamp) | Net Realized PnL |
| :--- | :---: | :---: | :---: | :---: |
| **`BOTH` (Actual)** | 304 | +₹1,81,856.99 | ₹24,843.42 | **+₹1,57,013.57** |
| **`PE_SELL` Only** | 159 | +₹1,58,946.44 | ₹12,208.73 | **+₹1,46,737.71** |
| **`CE_SELL` Only** | 145 | +₹22,910.55 | ₹12,634.69 | **+₹10,275.86** |

* **Finding**: `PE_SELL` captured **93.5%** of total net profits with roughly half the order friction and avoided the 3-month CE bleed (-₹49,947.30 gross across Aug, Oct, Nov).

## 2. 1-Month Nifty Spot Backtest Variants (Nov 1 – Nov 26, 2026 | 6,000 1-min bars, `iv=11.5%`, `lot_size=65`, `capital=₹6L`)

| Variant | Trades | Win Rate | Backtest Gross PnL | Max Drawdown | Profit Factor |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **`PE_SELL` Only (`+100`, 5 re)** | **36** | **52.78%** | **+₹41,908.00** | **1.35% (-₹8,532.64)** | **2.00** |
| `BOTH` Cap Re-entries (`3 re`) | 71 | 43.66% | +₹42,778.87 | 2.03% (-₹12,908.56) | 1.42 |
| `BOTH` Baseline (`-100/+100`, 5 re) | 89 | 43.82% | +₹39,830.28 | 3.80% (-₹24,640.55) | 1.29 |
| `BOTH` OTM CE (`+100/+100`, 5 re) | 91 | 36.26% | +₹19,695.48 | 2.73% (-₹17,294.96) | 1.21 |
| `BOTH` ATM CE (`0/+100`, 5 re) | 89 | 33.71% | -₹6,378.78 | 4.94% (-₹30,798.88) | 0.95 |

*(Note: `backtester/engine.py` evaluates gross option point PnL before brokerage/STT; `PE_SELL` also saves ~₹2,000–₹3,000/mo in friction vs `BOTH`.)*

## 3. Recommendation for Phase 3 Rollout
1. **Recommended Initial `LIVE_REAL` Preset**: `"strategy_type": "PE_SELL"` (Profit Factor `2.00`, lowest drawdown `1.35%`, lower margin `₹4.25L–₹4.50L`).
2. **If Running `BOTH`**: Lower `"max_reentries"` from `5` to `3` (nearly halves MaxDD from `3.80%` to `2.03%` and raises PF from `1.29` to `1.42`).
3. **Ongoing Gate**: Continue `LIVE` paper mode for 10–15 post-Nov 23 sessions to finish forward validation before toggling `"mode": "LIVE_REAL"`.
