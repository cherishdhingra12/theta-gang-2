# Phase 3: Security & Controlled `LIVE_REAL` Rollout Plan

## 1. Security Hardening (Pre-`LIVE_REAL`)
- Do **not** expose `app.py` (`0.0.0.0:5001`) directly over plain HTTP on a public IP before switching to `LIVE_REAL` (Basic Auth credentials and `/api/live/control` + panic endpoints would otherwise traverse unencrypted HTTP).
- Options (pick one):
  1. **SSH Tunnel / Tailscale (Zero Extra Infra)**: Bind/restrict port `5001` via Oracle Cloud Security List / `ufw` to localhost only, and access locally via:
     `ssh -L 5001:127.0.0.1:5001 ubuntu@140.238.241.115`
  2. **HTTPS Reverse Proxy (`deploy/Caddyfile`)**: Install Caddy on the VPS, point a domain to `140.238.241.115`, and run Caddy using `deploy/Caddyfile` for automatic Let's Encrypt TLS.

## 2. Safe Deployment Protocol (Never Interrupt Open Market Hours)
- Deploy **only** outside NSE hours (after `15:30 IST` or before `09:05 IST`) after confirming `live_state.json` has `0` open lots:
  1. Push local verified commits (`Phase 1` + `.planning/` + `deploy/Caddyfile`) to `origin main`.
  2. On VPS (`140.238.241.115`): `git pull` -> run `./venv/bin/python -m unittest discover -s tests -v` -> restart `sudo systemctl restart nifty-bot.service`.
  3. Verify `curl -s http://127.0.0.1:5001/api/live/health` returns `HEALTHY`.

## 3. Capital Funding & Pre-Flight Margin Check
1. Keep `config.json` in `LIVE` mode for 10–15 sessions post-Nov 23 safeguards while validating forward consistency.
2. When ready to fund Zerodha (`NRML` product):
   - **`PE_SELL` Only (Recommended Initial Rollout)**: Fund **₹4,25,000 – ₹4,50,000** (`~₹3.4L–₹3.7L` 2-lot margin + `₹80k–₹1.0L` buffer).
   - **`BOTH` Legs (2 PE + 2 CE)**: Fund **₹6,00,000 – ₹6,50,000** (`~₹4.25L–₹4.75L` strangle margin + `₹1.25L–₹1.75L` VIX/drawdown buffer).
3. Query live `kite.basket_order_margins()` on the funded account during market hours to confirm `available_margin >= margin_req * 1.15`.

## 4. Controlled 2-Week `LIVE_REAL` Burn-In
1. Update `config.json` on VPS:
   - `"mode": "LIVE_REAL"`
   - `"strategy_type": "PE_SELL"` (or `"BOTH"` with `"max_reentries": 3` after Phase 2 validation)
   - `"capital": <funded_amount>`
2. Reconcile Zerodha Console Tradebook / Contract Notes against `live_trades.json` at `15:30 IST` daily for:
   - Entry `LIMIT` fill price vs `target_entry` (verify no chronic non-fills).
   - Exchange `SL-L` trigger & exit prices vs logged PnL.
   - Realized brokerage/STT/exchange drag vs net ledger.
3. Only promote to full `BOTH` sizing after 2+ weeks of zero state/position drift.
