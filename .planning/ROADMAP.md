# Theta Gang 2 — Production Rollout Roadmap

**Goal**: Safely transition `Theta_Gang_2` from `LIVE` (live-feed paper trading) to `LIVE_REAL` (real capital on Zerodha Kite Connect) with verified execution safety, resolved CE-leg drag, and secure operations.

## Phases

### Phase 1: `LIVE_REAL` Execution Hardening *(Completed)*
- [x] Add NSE `0.05` tick-size rounding (`round_to_tick`) across all `LIMIT` and `SL-L` prices/triggers (`live_bot/live_feed.py`).
- [x] Add Entry Fill Verification Gate (`_resolve_entry_order_real`): only transition state and arm `SL-L` if entry fills; cancel stale `OPEN` orders cleanly and handle 1-lot partial fills safely.
- [x] Add SL-L Completion Verification & Double-Buy Guard (`_close_position_real`): query `kite.order_history(sl_order_id)` on SL hits, 15:15 square-off, and panic exit so already-filled exchange SLs never double-buy.
- [x] Fix Prospective Pre-Trade Margin Guard (`calculate_margins` with `extra_leg`/`extra_lots` + realistic `₹1.70L/lot` SPAN+Exposure fallback aligned with `get_margin_metrics`).
- [x] Add 5 mocked `LIVE_REAL` unit tests in `tests/test_live.py` (33/33 suite passing).

### Phase 2: Forward Validation & CE-Leg Backtest Analysis *(Code/Backtest Done; Forward Sample Ongoing)*
- [x] Run empirical + 1-month spot backtest comparison in `.planning/PHASE2_ANALYSIS.md` (`PE_SELL` PF `2.00` / MaxDD `1.35%` vs `BOTH` PF `1.29` / MaxDD `3.80%`).
- [ ] Accumulate 10–15 `LIVE` forward sessions on Nov 23 safeguards before switching `config.json` to `LIVE_REAL`.

### Phase 3: Security & Controlled Real-Money Rollout *(Artifacts Prepared; Deployed Phase 1 Post-Market)*
- [x] Add HTTPS reverse proxy template (`deploy/Caddyfile`) and step-by-step rollout checklist (`deploy/PHASE3_ROLLOUT.md`).
- [x] Deploy Phase 1 code to Oracle VPS (`140.238.241.115`) post-market (Nov 26 `16:02 IST`, 35/35 VPS tests passing, `nifty-bot.service` healthy).
- [ ] Enable HTTPS/SSH tunnel for dashboard access, verify live `basket_order_margins()` with funded capital (`₹4.25L–₹4.50L` for `PE_SELL` or `₹6.00L–₹6.50L` for `BOTH`), and start 2-week `LIVE_REAL` burn-in after the 15-session `BOTH` surveillance window.
