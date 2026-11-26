# Theta Gang 2 — Master Plan to 100% Real-Money (`LIVE_REAL`) Readiness

**Vision**: Run Theta Gang 2 reliably and profitably on **real capital (`LIVE_REAL`)** via Zerodha Kite Connect with institutional-grade execution safety, calibrated risk guardrails, hardened security, and verified net-of-cost expectancy.

**Created**: 2026-11-26  
**Baseline Commit**: `68d9f41` (`BOTH` active in `LIVE` surveillance on Oracle VPS `140.238.241.115`)

---

## Empirical Design Decisions (Backed by 311 Historical Trades)

Before coding any remaining changes, counterfactual simulations on all **311 live-feed trades (Jun 12 – Nov 26, 2026)** established three clear rules:

1. **Remove `2-SL Trend Freeze` (`enable_2sl_trend_freeze = False`) and Keep `3-SL 45-Min Cooldown`**:
   - **Data Proof**: Across 32 historical day-legs where 2 consecutive SLs occurred without a Target 1 win, freezing permanently for the day skipped **+₹20,380.68 in net recovery profits** (`PE`: `+₹7,936.75`, `CE`: `+₹12,443.93`)—including **`+₹10,010.00` on Nov 26 `CE` alone** (because a `₹0.00` breakeven `Stop Loss (Shifted)` also incremented `consec_sl`).
   - **Best Solution**: Disable `enable_2sl_trend_freeze`, never count breakeven/shifted SLs (`pnl >= 0`) as consecutive losses, and rely on the **3-SL 45-min Cooldown** (which only skipped `+₹871` across 6 months while pausing burst trends) + the **+50% Runaway Shield**.
2. **Tighten Daily Drawdown Kill Switch from `20%` (`₹1.30L`) to `6%` (`~₹36k–₹39k`)**:
   - **Data Proof**: Worst historical gross day across 68 sessions is `-₹22,821.50` (Nov 6, `-₹23,398` net). A `20%` limit (`₹1,30,000` on `₹6.5L`) only trips after catastrophic system failure. Setting `6%` leaves ~55% cushion above the worst normal day while capping runaway glitches.
3. **Keep `BOTH` Under 15-Session `LIVE` Surveillance Before Selecting Final `LIVE_REAL` Mode**:
   - `PE` Net = **+₹1,57,188.66** (MaxDD `-₹28,657.63`); `CE` Net = **+₹15,835.82** (MaxDD `-₹81,403.59`), though `CE` won both `Nov 25` (`+₹10.1k`) and `Nov 26` (`+₹5.9k`) post-patch. Let 15 sessions decide `BOTH` vs `PE_SELL` Only.

---

## Execution Roadmap (4 Workstreams)

### Workstream 1: Close Remaining `LIVE_REAL` Execution Bugs *(Immediate Code Sprint)*
*Goal: Eliminate 100% of exchange/local position-desync scenarios.*

1. **Restore `sl_order_id` & Reconcile Broker State on Mid-Day Restart** (`live_bot/live_feed.py`):
   - Restore `leg['sl_order_id'] = s_leg.get('sl_order_id')` inside `_sync_session()` (`L1818-L1829`).
   - Add `_reconcile_broker_state_on_sync()` for `LIVE_REAL`: query `kite.orders()` and `kite.positions()` on startup/restart to verify if an existing `sl_order_id` filled or remains `TRIGGER PENDING`, preventing duplicate `MARKET BUY` orders.
2. **Bulletproof `modify_zerodha_sl_order()` on Target Lot 1 (`2 Lots -> 1 Lot`)**:
   - Check the boolean return value of `modify_zerodha_sl_order()`.
   - If modification fails: inspect `order_history(sl_order_id)`; if still open with `130` qty, immediately `cancel_zerodha_order` and place a fresh `1 * lot_size` `SL-L` (`place_zerodha_sl_order`); if already filled, reconcile via `kite.positions()`. Prevents flipping into an accidental 1-lot naked long.
3. **Periodic In-Trade Exchange Order Sync (`_sync_active_sl_orders`)**:
   - Every `15–20s` while a leg is in `SHORT_2_LOTS` or `SHORT_1_LOT` in `LIVE_REAL`, poll `order_history(sl_order_id)`. If the exchange `SL-L` triggered between WebSocket ticks and snapped back, close the leg cleanly right away.
4. **Optional Entry Upgrade — Resting `LIMIT SELL` at `0.90X`** *(Recommended Best Execution)*:
   - Replace reactive `LIMIT SELL` (submitted *after* LTP drops `<= 0.90X`, risking bid slippage or 3s `OPEN` misses) with a **resting `LIMIT SELL`** placed on Zerodha as soon as `above_entry_threshold == True`, updated only if `Day High X` climbs and cancelled on cooldown/freeze/15:15. Ensures `LIVE_REAL` entry fills match `LIVE` simulated limit capping 1:1.

**Exit Gate**: All new `LIVE_REAL` failure modes covered by unit tests in `tests/test_live.py` (restart with active `sl_order_id`, failed SL qty step-down recovery, mid-tick SL fill sync, resting entry modify/cancel).

---

### Workstream 2: Guardrail Calibration & Control-Plane Hardening *(Same Code Sprint)*

1. **Calibrate Guardrails (`live_bot/live_feed.py` + `config.json`)**:
   - Set `"enable_2sl_trend_freeze": false` by default (keep configurable in `config.json`), ensure `Stop Loss (Shifted)` (`pnl >= 0`) never increments consecutive-loss counters, and lower `max_daily_loss` from `self.capital * 0.20` to `self.capital * 0.06` (configurable `daily_loss_limit_pct`, default `6.0`).
2. **Mask Secrets in `app.py` (`/api/live/config`)**:
   - Redact `kite_api_secret`, `kite_password`, and `kite_totp_secret` in `GET /api/live/config` responses (`"********"`), and ignore masked placeholder strings on `POST /api/live/config` so plaintext secrets are never exposed over the API.
3. **Replace Playwright Chromium in `kite_auth.py` OR Upgrade VPS Memory**:
   - Current VPS has **954 MB RAM** (`363 MB` swap in use).
   - **Recommended Solution**: Replace heavy Playwright Chromium (`~350 MB` RAM, brittle CSS selectors) in `kite_auth.py` with direct Zerodha HTTP API login (`POST https://kite.zerodha.com/api/login` + `POST https://kite.zerodha.com/api/twofa` + redirect `request_token` extraction via `requests.Session` — `~5 MB` RAM, sub-2s execution), keeping Playwright only as fallback if needed.
4. **Enforce Encrypted Transport on Port `8001` (`deploy/`)**:
   - Lock Oracle Cloud Security List / `ufw` to block public `0.0.0.0:8001` HTTP access (use `ssh -L 5001:127.0.0.1:5001` for dashboard), **or** deploy TLS via `deploy/Caddyfile` / Certbot Nginx HTTPS.

**Exit Gate**: Clean `GET /api/live/config` secret redaction test, verified `requests`-based Kite login script test, zero plain HTTP exposure on public IP.

---

### Workstream 3: 15-Session `LIVE` Surveillance & Parameter Decision *(Ongoing — Market Hours)*

1. Run Workstream 1+2 hardened code in `LIVE` mode (`strategy_type: "BOTH"`) for **15 trading sessions** (~3 weeks).
2. Evaluate net-of-cost expectancy (after `₹20/order` + `0.10% STT` + `NSE/GST`):
   - **Scenario A**: `CE` is net positive/breakeven under Limit Capping + Cooldown $\rightarrow$ Keep **`BOTH`** for `LIVE_REAL` (consider `max_reentries = 3` or `4` if worst-day drawdown exceeds comfort).
   - **Scenario B**: `CE` remains net negative $\rightarrow$ Launch `LIVE_REAL` with **`PE_SELL` Only**, keeping `CE` strictly as paper/shadow research.

---

### Workstream 4: Staged `LIVE_REAL` Capital Launch & Reconciliation *(Go-Live)*

1. **Fund Account Properly**:
   - **`BOTH` (`2 PE + 2 CE`)**: **₹6,00,000 – ₹6,50,000** (`~₹4.25L–₹4.75L` peak strangle margin + `₹1.25L–₹1.75L` VIX/drawdown buffer).
   - **`PE_SELL` Only (`2 PE`)**: **₹4,25,000 – ₹4,50,000** (`~₹3.30L–₹3.70L` margin + `₹80k–₹1.0L` buffer).
   - *(Optional Capital Efficiency)*: Add far-OTM ~₹5–₹10 long wings if targeting lower `NRML` margin (~40–50% margin reduction).
2. **Live Margin Dry-Run**: Verify `kite.basket_order_margins()` during market hours confirms `available >= margin_req * 1.15`.
3. **2-Week Controlled `LIVE_REAL` Burn-In + Daily Contract Note Reconciliation**:
   - Optional extra safety: Start Week 1 with `lots_per_entry = 1` (half-size burn-in, ₹2.5L–₹3.5L margin) before scaling to `2` lots.
   - Reconcile `kite.trades()` / Zerodha Contract Notes against `live_trades.json` at `15:30 IST` daily (`0` open overnight qty, fill prices, and net brokerage/STT).
