document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const feedModeSelect = document.getElementById('feed-mode');
    const startBtn = document.getElementById('start-btn');
    const stopBtn = document.getElementById('stop-btn');
    const configForm = document.getElementById('live-form');
    
    const timeBadge = document.getElementById('live-time-badge');
    const statusText = document.getElementById('live-status-text');
    const statusDot = document.getElementById('live-status-dot');
    const feedModeText = document.getElementById('feed-mode-text');
    const feedModeBadge = document.getElementById('feed-mode-badge');
    
    // Accounts
    const liveNetEquity = document.getElementById('live-net-equity');
    const liveRealizedPnl = document.getElementById('live-realized-pnl');
    const liveUnrealizedPnl = document.getElementById('live-unrealized-pnl');
    const liveSpotPrice = document.getElementById('live-spot-price');
    const liveExpiry = document.getElementById('live-expiry');
    const marketStatusVal = document.getElementById('market-status-val');
    
    // Tickers
    const peStrike = document.getElementById('pe-strike-lbl');
    const peLivePrice = document.getElementById('pe-live-price');
    const peDayHigh = document.getElementById('pe-day-high');
    const peEntryOrd = document.getElementById('pe-entry-ord');
    const peSl = document.getElementById('pe-sl');
    const peTarget = document.getElementById('pe-target');
    const peReentries = document.getElementById('pe-reentries');
    const pePos = document.getElementById('pe-pos');
    
    const ceStrike = document.getElementById('ce-strike-lbl');
    const ceLivePrice = document.getElementById('ce-live-price');
    const ceDayHigh = document.getElementById('ce-day-high');
    const ceEntryOrd = document.getElementById('ce-entry-ord');
    const ceSl = document.getElementById('ce-sl');
    const ceTarget = document.getElementById('ce-target');
    const ceReentries = document.getElementById('ce-reentries');
    const cePos = document.getElementById('ce-pos');
    
    // Active Positions Table
    const activePositionsPanel = document.getElementById('active-positions-panel');
    const activePositionsTbody = document.getElementById('active-positions-tbody');
    const activePositionsMarginBar = document.getElementById('active-positions-margin-bar');
    
    // Terminal Log Console
    const terminalLog = document.getElementById('terminal-body-log');
    
    // App State
    let evtSource = null;
    let liveChart = null;
    let prevPrices = { spot: 0, pe: 0, ce: 0 };
    let chartTicks = []; // Array of {time, pe, ce}
    let liveManagerRunningState = false;
    let configIv = 15.0;
    let configR = 7.0;

    // Load configuration from server
    async function loadConfig() {
        try {
            const res = await fetch('/api/live/config');
            const config = await res.json();
            
            document.getElementById('feed-mode').value = config.mode || 'MOCK';
            document.getElementById('strategy_type').value = config.strategy_type || 'BOTH';
            document.getElementById('strike_offset_pe').value = config.strike_offset_pe ?? 100;
            document.getElementById('strike_offset_ce').value = config.strike_offset_ce ?? -100;
            document.getElementById('entry_discount').value = config.entry_discount ?? 10;
            document.getElementById('lot_1_target').value = config.lot_1_target ?? 40;
            document.getElementById('max_reentries').value = config.max_reentries ?? 5;
            document.getElementById('lot_size').value = config.lot_size ?? 65;
            document.getElementById('capital').value = config.capital ?? 500000;
            if (liveNetEquity) {
                liveNetEquity.textContent = formatINR(config.capital ?? 500000);
            }
            configIv = config.iv ?? 15.0;
            configR = config.r ?? 7.0;
            
            const autoPilotToggle = document.getElementById('auto-pilot-toggle');
            autoPilotToggle.checked = !!config.auto_pilot;
            
            // Zerodha config loading
            document.getElementById('kite_api_key').value = config.kite_api_key || '';
            document.getElementById('kite_api_secret').value = config.kite_api_secret || '';
            document.getElementById('kite_user_id').value = config.kite_user_id || '';
            document.getElementById('kite_password').value = config.kite_password || '';
            document.getElementById('kite_totp_secret').value = config.kite_totp_secret || '';
            
            updateAutopilotBadge(!!config.auto_pilot, liveManagerRunningState);
        } catch (error) {
            console.error('Error loading config:', error);
        }
    }

    function updateAutopilotBadge(enabled, running = false) {
        const badge = document.getElementById('autopilot-status-badge');
        const dot = document.getElementById('autopilot-status-dot');
        const text = document.getElementById('autopilot-status-text');
        
        if (enabled) {
            badge.style.borderColor = '#F59E0B';
            badge.style.color = '#F59E0B';
            dot.className = 'pulse-dot status-autopilot-active-dot';
            text.textContent = running ? 'Auto-Pilot: ACTIVE' : 'Auto-Pilot: STANDBY';
        } else {
            badge.style.borderColor = '#4B5563';
            badge.style.color = '#9CA3AF';
            dot.className = 'pulse-dot status-autopilot-off-dot';
            text.textContent = 'Auto-Pilot Off';
        }
    }

    // Bind Auto-Pilot toggle change
    document.getElementById('auto-pilot-toggle').addEventListener('change', async (e) => {
        const enabled = e.target.checked;
        writeToTerminal(`>> SYSTEM: Saving Auto-Pilot configuration...`, 'system');
        try {
            const res = await fetch('/api/live/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ auto_pilot: enabled })
            });
            const data = await res.json();
            if (res.ok) {
                updateAutopilotBadge(enabled, liveManagerRunningState);
                writeToTerminal(`>> SYSTEM: Auto-Pilot is now ${enabled ? 'ENABLED' : 'DISABLED'}.`, 'system');
            } else {
                throw new Error(data.error);
            }
        } catch (error) {
            writeToTerminal(`>> ERROR updating Auto-Pilot: ${error.message}`, 'exit');
            e.target.checked = !enabled;
        }
    });

    // Bind Save Config button click
    document.getElementById('save-config-btn').addEventListener('click', async () => {
        writeToTerminal('>> SYSTEM: Saving strategy parameters to configuration...', 'system');
        const payload = {
            mode: document.getElementById('feed-mode').value,
            strategy_type: document.getElementById('strategy_type').value,
            strike_offset_pe: parseInt(document.getElementById('strike_offset_pe').value),
            strike_offset_ce: parseInt(document.getElementById('strike_offset_ce').value),
            entry_discount: parseFloat(document.getElementById('entry_discount').value),
            lot_1_target: parseFloat(document.getElementById('lot_1_target').value),
            max_reentries: parseInt(document.getElementById('max_reentries').value),
            lot_size: parseInt(document.getElementById('lot_size').value),
            capital: parseFloat(document.getElementById('capital').value),
            iv: configIv,
            r: configR,
            auto_pilot: document.getElementById('auto-pilot-toggle').checked,
            kite_api_key: document.getElementById('kite_api_key').value,
            kite_api_secret: document.getElementById('kite_api_secret').value,
            kite_user_id: document.getElementById('kite_user_id').value,
            kite_password: document.getElementById('kite_password').value,
            kite_totp_secret: document.getElementById('kite_totp_secret').value
        };
        try {
            const res = await fetch('/api/live/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (res.ok) {
                writeToTerminal('>> SYSTEM: Configuration saved successfully.', 'system');
                const saveBtn = document.getElementById('save-config-btn');
                const origBg = saveBtn.style.background;
                saveBtn.style.background = '#10B981';
                setTimeout(() => { saveBtn.style.background = origBg; }, 1000);
            } else {
                throw new Error(data.error);
            }
        } catch (error) {
            writeToTerminal(`>> ERROR saving configuration: ${error.message}`, 'exit');
        }
    });

    // Bind Modal actions
    const tradeLogModal = document.getElementById('trade-log-modal');
    const tradeLogTriggerBtn = document.getElementById('trade-log-trigger-btn');
    const closeModalBtn = document.getElementById('close-modal-btn');
    
    tradeLogTriggerBtn.addEventListener('click', () => {
        tradeLogModal.classList.add('open');
        fetchAndRenderTrades();
    });
    
    closeModalBtn.addEventListener('click', () => {
        tradeLogModal.classList.remove('open');
    });
    
    tradeLogModal.addEventListener('click', (e) => {
        if (e.target === tradeLogModal) {
            tradeLogModal.classList.remove('open');
        }
    });

    // Real Money Safety Confirmation Modal Logic
    const realMoneyModal = document.getElementById('real-money-confirm-modal');
    const confirmRealMoneyBtn = document.getElementById('confirm-real-money-btn');
    const cancelRealMoneyBtn = document.getElementById('cancel-real-money-btn');
    let previousFeedMode = feedModeSelect.value;

    feedModeSelect.addEventListener('change', (e) => {
        if (feedModeSelect.value === 'LIVE_REAL') {
            if (realMoneyModal) {
                realMoneyModal.classList.add('open');
            }
        } else {
            previousFeedMode = feedModeSelect.value;
        }
    });

    if (confirmRealMoneyBtn) {
        confirmRealMoneyBtn.addEventListener('click', () => {
            if (realMoneyModal) realMoneyModal.classList.remove('open');
            previousFeedMode = 'LIVE_REAL';
            writeToTerminal('⚠️ SYSTEM: LIVE REAL MONEY MODE CONFIRMED. Real broker orders are armed.', 'system');
        });
    }

    if (cancelRealMoneyBtn) {
        cancelRealMoneyBtn.addEventListener('click', () => {
            if (realMoneyModal) realMoneyModal.classList.remove('open');
            feedModeSelect.value = (previousFeedMode && previousFeedMode !== 'LIVE_REAL') ? previousFeedMode : 'LIVE';
            writeToTerminal('>> SYSTEM: Real money mode canceled. Maintained Paper Trading mode.', 'system');
        });
    }

    if (realMoneyModal) {
        realMoneyModal.addEventListener('click', (e) => {
            if (e.target === realMoneyModal) {
                realMoneyModal.classList.remove('open');
                feedModeSelect.value = (previousFeedMode && previousFeedMode !== 'LIVE_REAL') ? previousFeedMode : 'LIVE';
            }
        });
    }

    // Start Live Engine Click
    startBtn.addEventListener('click', async () => {
        if (startBtn.classList.contains('disabled')) return;
        
        const mode = feedModeSelect.value;
        const hasKiteCreds = !!(
            document.getElementById('kite_api_key').value.trim() &&
            document.getElementById('kite_api_secret').value.trim() &&
            document.getElementById('kite_user_id').value.trim() &&
            document.getElementById('kite_password').value.trim()
        );
        
        // Auto-save the config values first
        writeToTerminal('>> SYSTEM: Saving configuration details...', 'system');
        const payloadConfig = {
            mode: document.getElementById('feed-mode').value,
            strategy_type: document.getElementById('strategy_type').value,
            strike_offset_pe: parseInt(document.getElementById('strike_offset_pe').value),
            strike_offset_ce: parseInt(document.getElementById('strike_offset_ce').value),
            entry_discount: parseFloat(document.getElementById('entry_discount').value),
            lot_1_target: parseFloat(document.getElementById('lot_1_target').value),
            max_reentries: parseInt(document.getElementById('max_reentries').value),
            lot_size: parseInt(document.getElementById('lot_size').value),
            capital: parseFloat(document.getElementById('capital').value),
            iv: configIv,
            r: configR,
            auto_pilot: document.getElementById('auto-pilot-toggle').checked,
            kite_api_key: document.getElementById('kite_api_key').value,
            kite_api_secret: document.getElementById('kite_api_secret').value,
            kite_user_id: document.getElementById('kite_user_id').value,
            kite_password: document.getElementById('kite_password').value,
            kite_totp_secret: document.getElementById('kite_totp_secret').value
        };
        try {
            await fetch('/api/live/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payloadConfig)
            });
        } catch (e) {
            console.error("Failed to auto-save config before start:", e);
        }
        
        const msg = (mode === 'LIVE' || mode === 'LIVE_REAL') 
            ? '>> Connecting to live NSE market data stream...' 
            : '>> Starting mock market tick generator...';
            
        writeToTerminal(msg, 'system');
        
        // Disable strategy parameters fields
        toggleInputs(true);
        
        // Gather parameters
        const strategyType = document.getElementById('strategy_type').value;
        const strikeOffsetPe = document.getElementById('strike_offset_pe').value;
        const strikeOffsetCe = document.getElementById('strike_offset_ce').value;
        const entryDiscount = document.getElementById('entry_discount').value;
        const lot1Target = document.getElementById('lot_1_target').value;
        const maxReentries = document.getElementById('max_reentries').value;
        const lotSize = document.getElementById('lot_size').value;
        const capital = document.getElementById('capital').value;
        const iv = configIv;
        const r = configR;
        
        const payload = {
            action: 'start', mode, capital,
            strategy_type: strategyType, strike_offset_pe: strikeOffsetPe,
            strike_offset_ce: strikeOffsetCe, entry_discount: entryDiscount,
            lot_1_target: lot1Target, max_reentries: maxReentries,
            lot_size: lotSize, iv: iv, r: r
        };
        
        try {
            const res = await fetch('/api/live/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Failed to start live engine.');
            
            // Set buttons active state
            startBtn.classList.add('disabled');
            stopBtn.classList.remove('disabled');
            stopBtn.disabled = false;
            
            // Set Feed Mode text/badge styling
            if (mode === 'LIVE_REAL') {
                feedModeText.textContent = '⚡ LIVE REAL MONEY';
                feedModeBadge.style.borderColor = '#EF4444';
                feedModeBadge.style.color = '#EF4444';
            } else if (mode === 'LIVE') {
                feedModeText.textContent = 'PAPER TRADING MODE';
                feedModeBadge.style.borderColor = '#3B82F6';
                feedModeBadge.style.color = '#60A5FA';
            } else {
                feedModeText.textContent = 'MOCK SIMULATION';
                feedModeBadge.style.borderColor = '';
                feedModeBadge.style.color = '';
            }
            
            // Clear chart and logs
            chartTicks = [];
            initChart();
            terminalLog.innerHTML = `<div class="terminal-line system-msg">>> SYSTEM: Engine started in ${mode} mode. Connecting stream...</div>`;
            
            // Connect to SSE stream
            connectSSE();
            
            // If LIVE/LIVE_REAL mode and Zerodha is configured, start polling auth status
            if ((mode === 'LIVE' || mode === 'LIVE_REAL') && hasKiteCreds) {
                startKiteStatusPolling();
            }
            
        } catch (error) {
            console.error(error);
            writeToTerminal(`>> ERROR starting live engine: ${error.message}`, 'exit');
            toggleInputs(false);
        }
    });

    // Stop Live Engine Click
    stopBtn.addEventListener('click', async () => {
        if (stopBtn.classList.contains('disabled')) return;
        
        try {
            await fetch('/api/live/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'stop' })
            });
            
            resetLiveUI();
            writeToTerminal('>> SYSTEM: Engine stopped by user.', 'system');
        } catch (error) {
            console.error(error);
        }
    });

    // Emergency Panic Exit Click
    const panicBtn = document.getElementById('panic-btn');
    if (panicBtn) {
        panicBtn.addEventListener('click', async () => {
            if (!confirm('⚠️ EMERGENCY PANIC EXIT: Are you sure you want to market square-off all active positions and halt the trading engine?')) {
                return;
            }
            try {
                writeToTerminal('⚡ EMERGENCY PANIC EXIT TRIPPED: Sending market square-off orders to Zerodha...', 'exit');
                const res = await fetch('/api/live/panic-exit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await res.json();
                if (res.ok) {
                    writeToTerminal(`>> EMERGENCY: ${data.message}`, 'exit');
                    resetLiveUI();
                } else {
                    alert(`Error executing emergency panic exit: ${data.error}`);
                }
            } catch (err) {
                alert(`Error: ${err.message}`);
            }
        });
    }

    // SSE Stream connection
    function connectSSE() {
        if (evtSource) {
            evtSource.close();
        }
        
        evtSource = new EventSource('/api/live/stream');
        
        evtSource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            
            if (data.event === 'tick') {
                handleTick(data);
            } else if (data.event === 'status') {
                handleStatus(data.status, data.log, data.logs, data.mode, data.auto_pilot, data.feed_source);
                if (data.market_status) {
                    handleMarketStatus(data.market_status);
                }
            } else if (data.event === 'log') {
                // Check if message belongs to order/fill/exit/trail for color-coding
                let type = 'system';
                if (data.log.includes('[ENTRY')) type = 'fill';
                else if (data.log.includes('[STOP LOSS')) type = 'exit';
                else if (data.log.includes('[TARGET')) type = 'fill';
                else if (data.log.includes('[TRAILING')) type = 'trail';
                else if (data.log.includes('[SQUARE-OFF')) type = 'order';
                writeToTerminal(`>> ${data.log}`, type);
            } else if (data.event === 'market_status') {
                handleMarketStatus(data.status);
            }
        };
        
        evtSource.onerror = function() {
            console.log("SSE Stream disconnected. Reconnecting...");
            statusText.textContent = "Connecting...";
            statusDot.className = "pulse-dot status-paused-dot";
        };
    }

    // Helper to format currency in INR
    function formatINR(number) {
        const sign = number < 0 ? '-' : '';
        const absVal = Math.abs(number);
        const formatter = new Intl.NumberFormat('en-IN', {
            style: 'currency',
            currency: 'INR',
            maximumFractionDigits: 2
        });
        return formatter.format(absVal).replace('INR', '₹');
    }

    function updateMarginMetrics(info) {
        if (!info) return;
        
        const utilizedVal = document.getElementById('margin-utilized-val');
        const maxReqVal = document.getElementById('margin-max-required-val');
        const hedgingBenefitVal = document.getElementById('margin-hedging-benefit-val');
        const availableVal = document.getElementById('margin-available-val');
        const barFill = document.getElementById('margin-bar-fill');
        const utilPctText = document.getElementById('margin-utilization-pct');
        const badgeText = document.getElementById('margin-account-type-text');
        const badgeBg = document.getElementById('margin-account-type-badge');

        if (utilizedVal) utilizedVal.textContent = formatINR(info.utilized_margin || 0);
        if (maxReqVal) maxReqVal.textContent = formatINR(info.max_strategy_required_margin || 0);
        if (hedgingBenefitVal) hedgingBenefitVal.textContent = formatINR(info.hedging_benefit_amount || 0);
        if (availableVal) availableVal.textContent = formatINR(info.available_margin || 0);

        const pct = Math.min(100, Math.max(0, info.utilization_pct || 0));
        if (barFill) barFill.style.width = `${pct}%`;
        if (utilPctText) utilPctText.textContent = `${pct.toFixed(1)}% of Capital Utilized`;

        if (badgeText && badgeBg) {
            if (info.is_real_account) {
                badgeText.textContent = "⚡ ZERODHA LIVE ACCOUNT";
                badgeBg.style.background = "rgba(239, 68, 68, 0.15)";
                badgeBg.style.color = "#EF4444";
                badgeBg.style.borderColor = "rgba(239, 68, 68, 0.35)";
            } else if (info.mode === "LIVE") {
                badgeText.textContent = "PAPER TRADING (ZERODHA MARGIN RULES)";
                badgeBg.style.background = "rgba(59, 130, 246, 0.15)";
                badgeBg.style.color = "#60A5FA";
                badgeBg.style.borderColor = "rgba(59, 130, 246, 0.35)";
            } else {
                badgeText.textContent = "MOCK SIMULATION";
                badgeBg.style.background = "rgba(107, 114, 128, 0.15)";
                badgeBg.style.color = "#9CA3AF";
                badgeBg.style.borderColor = "rgba(107, 114, 128, 0.35)";
            }
        }
    }

    async function fetchMarginInfo() {
        try {
            const res = await fetch('/api/live/margin-info');
            if (res.ok) {
                const data = await res.json();
                updateMarginMetrics(data);
            }
        } catch (e) {
            console.error('Error fetching margin info:', e);
        }
    }

    // Handle incoming price ticks
    function handleTick(data) {
        // 1. Update Time Badge and Expiry
        const timeStr = data.datetime.split(' ')[1];
        timeBadge.innerHTML = `<i class="fa-solid fa-satellite"></i> Last Updated: ${timeStr} IST`;
        liveExpiry.textContent = data.expiry || 'Loading...';
        
        // Market Status
        handleMarketStatus(data.market_status);
        
        // 2. Update Accounts Bar
        liveNetEquity.textContent = formatINR(data.account.balance);
        
        const realized = data.account.realized_pnl;
        liveRealizedPnl.textContent = formatINR(realized);
        liveRealizedPnl.className = realized >= 0 ? 'live-acc-value text-green' : 'live-acc-value text-red';
        
        const unrealized = data.account.unrealized_pnl;
        liveUnrealizedPnl.textContent = formatINR(unrealized);
        liveUnrealizedPnl.className = unrealized >= 0 ? 'live-acc-value text-green' : 'live-acc-value text-red';
        
        if (data.inception) {
            updateInceptionMetrics(data.inception);
        }
        
        if (data.margin_info) {
            updateMarginMetrics(data.margin_info);
        }
        
        // Spot Price Flashing
        updateWithFlash(liveSpotPrice, data.spot, 'spot');
        
        // 3. Update PE Option Card
        if (data.pe && data.pe.strike) {
            peStrike.textContent = `Strike: ${data.pe.strike} PE`;
            updateWithFlash(peLivePrice, data.pe.price, 'pe');
            peDayHigh.textContent = `₹${data.pe.day_high.toFixed(2)}`;
            
            const entryTarget = data.pe.day_high * (1 - parseFloat(document.getElementById('entry_discount').value)/100);
            peEntryOrd.textContent = `Sell Target: ₹${entryTarget.toFixed(2)}`;
            peSl.textContent = data.pe.sl > 0 ? `₹${data.pe.sl.toFixed(2)}` : '-';
            peTarget.textContent = data.pe.target > 0 ? `₹${data.pe.target.toFixed(2)}` : '-';
            peReentries.textContent = `${data.pe.reentries}/${document.getElementById('max_reentries').value}`;
            
            updatePositionBadge(pePos, data.pe.position);
        } else {
            peStrike.textContent = 'Strike: -';
            peLivePrice.textContent = '₹0.00';
            peDayHigh.textContent = '-';
            peEntryOrd.textContent = '-';
            peSl.textContent = '-';
            peTarget.textContent = '-';
            peReentries.textContent = '-';
            pePos.textContent = 'None';
            pePos.className = 'badge';
        }
        
        // 4. Update CE Option Card
        if (data.ce && data.ce.strike) {
            ceStrike.textContent = `Strike: ${data.ce.strike} CE`;
            updateWithFlash(ceLivePrice, data.ce.price, 'ce');
            ceDayHigh.textContent = `₹${data.ce.day_high.toFixed(2)}`;
            
            const entryTarget = data.ce.day_high * (1 - parseFloat(document.getElementById('entry_discount').value)/100);
            ceEntryOrd.textContent = `Sell Target: ₹${entryTarget.toFixed(2)}`;
            ceSl.textContent = data.ce.sl > 0 ? `₹${data.ce.sl.toFixed(2)}` : '-';
            ceTarget.textContent = data.ce.target > 0 ? `₹${data.ce.target.toFixed(2)}` : '-';
            ceReentries.textContent = `${data.ce.reentries}/${document.getElementById('max_reentries').value}`;
            
            updatePositionBadge(cePos, data.ce.position);
        } else {
            ceStrike.textContent = 'Strike: -';
            ceLivePrice.textContent = '₹0.00';
            ceDayHigh.textContent = '-';
            ceEntryOrd.textContent = '-';
            ceSl.textContent = '-';
            ceTarget.textContent = '-';
            ceReentries.textContent = '-';
            cePos.textContent = 'None';
            cePos.className = 'badge';
        }
        
        // 4.5. Update Active Positions Section
        let activeRowsHtml = '';
        const lotSize = parseInt(document.getElementById('lot_size').value) || 65;

        // PE active position check
        if (data.pe && data.pe.position && data.pe.position !== 'None') {
            let qty = lotSize;
            if (data.pe.position === 'SHORT_2_LOTS') qty = lotSize * 2;
            
            const pnl = data.pe.unrealized ?? 0.0;
            const pnlClass = pnl >= 0 ? 'text-green' : 'text-red';
            const pnlStr = formatINR(pnl);

            activeRowsHtml += `
                <tr>
                    <td><strong>Nifty ${data.expiry || ''} ${data.pe.strike} PE</strong></td>
                    <td>${data.pe.entry_time || '-'}</td>
                    <td>${qty} (${data.pe.position === 'SHORT_2_LOTS' ? '2 Lots' : '1 Lot'})</td>
                    <td>₹${data.pe.entry_price.toFixed(2)}</td>
                    <td>₹${data.pe.price.toFixed(2)}</td>
                    <td>₹${data.pe.sl.toFixed(2)}</td>
                    <td>₹${data.pe.target.toFixed(2)}</td>
                    <td class="${pnlClass}" style="text-align: right; font-weight: 600;">${pnlStr}</td>
                </tr>
            `;
        }

        // CE active position check
        if (data.ce && data.ce.position && data.ce.position !== 'None') {
            let qty = lotSize;
            if (data.ce.position === 'SHORT_2_LOTS') qty = lotSize * 2;
            
            const pnl = data.ce.unrealized ?? 0.0;
            const pnlClass = pnl >= 0 ? 'text-green' : 'text-red';
            const pnlStr = formatINR(pnl);

            activeRowsHtml += `
                <tr>
                    <td><strong>Nifty ${data.expiry || ''} ${data.ce.strike} CE</strong></td>
                    <td>${data.ce.entry_time || '-'}</td>
                    <td>${qty} (${data.ce.position === 'SHORT_2_LOTS' ? '2 Lots' : '1 Lot'})</td>
                    <td>₹${data.ce.entry_price.toFixed(2)}</td>
                    <td>₹${data.ce.price.toFixed(2)}</td>
                    <td>₹${data.ce.sl.toFixed(2)}</td>
                    <td>₹${data.ce.target.toFixed(2)}</td>
                    <td class="${pnlClass}" style="text-align: right; font-weight: 600;">${pnlStr}</td>
                </tr>
            `;
        }

        if (activeRowsHtml) {
            activePositionsTbody.innerHTML = activeRowsHtml;
            // Render active margin metrics bar
            if (activePositionsMarginBar && data.margin) {
                const required = data.margin.required || 0.0;
                const safe = data.margin.safe || 0.0;
                const utilised = data.margin.utilised || 0.0;
                const accountCapital = data.account ? (data.account.capital || 500000.0) : 500000.0;
                const openPnl = data.account ? (data.account.unrealized_pnl || 0.0) : 0.0;
                
                const excess = Math.max(0.0, accountCapital - required);
                const shortfall = Math.max(0.0, required - accountCapital);
                const balanceAfterMtm = accountCapital + openPnl;
                
                const isSafe = safe <= accountCapital;
                
                activePositionsMarginBar.style.marginTop = '15px';
                activePositionsMarginBar.style.padding = '16px 20px';
                activePositionsMarginBar.style.background = 'rgba(255, 255, 255, 0.05)';
                activePositionsMarginBar.style.borderRadius = '8px';
                activePositionsMarginBar.style.display = 'block';
                activePositionsMarginBar.style.borderLeft = `4px solid ${isSafe ? '#10B981' : '#EF4444'}`;
                
                activePositionsMarginBar.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px;">
                        <span style="font-weight: 600; font-size: 0.9rem; letter-spacing: 0.5px; text-transform: uppercase; color: var(--text-secondary);">Live Risk & Margin Metrics</span>
                        <div class="badge ${isSafe ? 'badge-success' : 'badge-danger'}" style="font-weight: 600; padding: 4px 10px; border-radius: 20px; font-size: 0.75rem;">${isSafe ? '🟢 SAFE' : '🔴 MARGIN RISK'}</div>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px 20px;">
                        <div><span style="color: var(--text-secondary); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.3px;">Strategy Margin Required:</span> <strong style="font-size: 0.95rem; margin-left: 5px; color: var(--accent);">${formatINR(required)}</strong></div>
                        <div><span style="color: var(--text-secondary); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.3px;">Account Margin Utilized:</span> <strong style="font-size: 0.95rem; margin-left: 5px;">${formatINR(utilised)}</strong></div>
                        <div><span style="color: var(--text-secondary); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.3px;">Margin Excess:</span> <strong style="font-size: 0.95rem; margin-left: 5px; color: #10B981;">${formatINR(excess)}</strong></div>
                        <div><span style="color: var(--text-secondary); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.3px;">Margin Shortfall:</span> <strong style="font-size: 0.95rem; margin-left: 5px; color: #EF4444;">${formatINR(shortfall)}</strong></div>
                        <div><span style="color: var(--text-secondary); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.3px;">Account Cash Balance:</span> <strong style="font-size: 0.95rem; margin-left: 5px;">${formatINR(accountCapital)}</strong></div>
                        <div><span style="color: var(--text-secondary); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.3px;">Balance After MTM:</span> <strong style="font-size: 0.95rem; margin-left: 5px; color: var(--accent);">${formatINR(balanceAfterMtm)}</strong></div>
                    </div>
                `;
            }
        } else {
            activePositionsTbody.innerHTML = `
                <tr>
                    <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 24px; font-style: italic;">
                        No active positions. Open trades will appear here during live market hours.
                    </td>
                </tr>
            `;
            // Render static peak margin recommendation bar
            if (activePositionsMarginBar) {
                const currentCap = parseFloat(document.getElementById('capital')?.value) || 500000.0;
                activePositionsMarginBar.style.marginTop = '15px';
                activePositionsMarginBar.style.padding = '12px 20px';
                activePositionsMarginBar.style.background = 'rgba(255, 255, 255, 0.05)';
                activePositionsMarginBar.style.borderRadius = '8px';
                activePositionsMarginBar.style.display = 'block';
                activePositionsMarginBar.style.borderLeft = '4px solid #3B82F6';
                
                activePositionsMarginBar.innerHTML = `
                    <div>
                        <i class="fa-solid fa-circle-info" style="color: #3B82F6; margin-right: 8px;"></i>
                        <span style="color: var(--text-secondary); font-size: 0.85rem;">Peak Margin Estimate (2 Lots CE & PE Short):</span> <strong style="font-size: 0.95rem; margin-left: 5px;">~₹2,20,000</strong>
                        <span style="color: var(--text-secondary); font-size: 0.85rem; margin-left: 15px;">Configured Starting Capital:</span> <strong style="font-size: 0.95rem; margin-left: 5px; color: #3B82F6;">${formatINR(currentCap)}</strong>
                    </div>
                `;
            }
        }
        
        // 5. Append to Chart
        chartTicks.push({
            time: timeStr,
            pe: data.pe && data.pe.strike ? data.pe.price : null,
            ce: data.ce && data.ce.strike ? data.ce.price : null
        });
        
        if (chartTicks.length > 50) {
            chartTicks.shift();
        }
        
        updateChartData();
    }

    // Update market status elements
    function handleMarketStatus(status) {
        marketStatusVal.textContent = status;
        if (status === 'OPEN') {
            marketStatusVal.className = 'text-green';
        } else {
            marketStatusVal.className = 'text-red';
        }
    }

    // Ticker values flashing effect
    function updateWithFlash(element, newVal, key) {
        const oldVal = prevPrices[key] || 0;
        element.textContent = `₹${newVal.toFixed(2)}`;
        
        if (oldVal !== 0 && newVal !== oldVal) {
            const flashClass = newVal > oldVal ? 'flash-up' : 'flash-down';
            element.classList.add(flashClass);
            setTimeout(() => {
                element.classList.remove(flashClass);
            }, 500);
        }
        prevPrices[key] = newVal;
    }

    // Update position status badge
    function updatePositionBadge(badge, position) {
        badge.textContent = position;
        if (position === 'None') {
            badge.className = 'badge';
        } else if (position.includes('SHORT')) {
            badge.className = 'badge badge-danger';
        } else {
            badge.className = 'badge badge-success';
        }
    }

    // Handle status events from stream
    function handleStatus(status, log, historicalLogs, mode, autoPilotEnabled, feedSource) {
        statusText.textContent = status;
        statusDot.className = 'pulse-dot';
        
        if (status === 'STOPPED') {
            liveManagerRunningState = false;
            statusDot.classList.add('status-stopped-dot');
            resetLiveUI();
        } else if (status === 'RUNNING' || status === 'FEED_DISRUPTED') {
            liveManagerRunningState = true;
            if (status === 'RUNNING') {
                statusDot.classList.add('status-running-dot');
            } else {
                statusDot.classList.add('status-disrupted-dot');
            }
            
            // Adjust buttons active state
            startBtn.classList.add('disabled');
            stopBtn.classList.remove('disabled');
            stopBtn.disabled = false;
            
            // Disable inputs
            toggleInputs(true);
            
            // Update Feed Mode Text and Badge
            let feedText = 'MOCK SIMULATION';
            if (mode === 'LIVE_REAL') {
                feedText = '⚡ LIVE REAL MONEY';
                feedModeBadge.style.borderColor = '#EF4444';
                feedModeBadge.style.color = '#EF4444';
            } else if (mode === 'LIVE') {
                if (feedSource === 'ZERODHA') {
                    feedText = 'PAPER TRADING (Zerodha)';
                } else if (feedSource === 'NSE') {
                    feedText = 'PAPER TRADING (NSE Web)';
                } else {
                    feedText = 'PAPER TRADING MODE';
                }
                feedModeBadge.style.borderColor = '#3B82F6';
                feedModeBadge.style.color = '#60A5FA';
            } else {
                feedModeBadge.style.borderColor = '';
                feedModeBadge.style.color = '';
            }
            feedModeText.textContent = feedText;
            
            // Initialize chart if needed
            if (!liveChart) {
                initChart();
            }
        }
        
        if (autoPilotEnabled !== undefined) {
            document.getElementById('auto-pilot-toggle').checked = autoPilotEnabled;
            updateAutopilotBadge(autoPilotEnabled, status === 'RUNNING');
        }
        
        // Load historical logs into terminal if provided and empty
        if (historicalLogs && historicalLogs.length > 0 && terminalLog.childElementCount <= 1) {
            terminalLog.innerHTML = ''; // Clear initial message
            historicalLogs.forEach(hLog => {
                let type = 'system';
                if (hLog.includes('[ENTRY')) type = 'fill';
                else if (hLog.includes('[STOP LOSS')) type = 'exit';
                else if (hLog.includes('[TARGET')) type = 'fill';
                else if (hLog.includes('[TRAILING')) type = 'trail';
                else if (hLog.includes('[SQUARE-OFF')) type = 'order';
                writeToTerminal(hLog, type);
            });
        } else if (log) {
            writeToTerminal(`>> ${log}`, 'system');
        }
    }

    // Write log lines to terminal console
    function writeToTerminal(message, type) {
        const line = document.createElement('div');
        line.className = 'terminal-line';
        
        if (type === 'system') line.classList.add('system-msg');
        else if (type === 'order') line.classList.add('order-msg');
        else if (type === 'fill') line.classList.add('fill-msg');
        else if (type === 'exit') line.classList.add('exit-msg');
        else if (type === 'trail') line.classList.add('trail-msg');
        
        line.textContent = message;
        terminalLog.appendChild(line);
        
        // Auto scroll to bottom
        terminalLog.scrollTop = terminalLog.scrollHeight;
    }

    // Toggle forms fields inputs
    function toggleInputs(disabled) {
        const inputs = configForm.querySelectorAll('input, select');
        inputs.forEach(input => {
            input.disabled = disabled;
            if (disabled) {
                input.classList.add('disabled-input');
            } else {
                input.classList.remove('disabled-input');
            }
        });
    }

    // Reset Live UI states on stop
    function resetLiveUI() {
        if (evtSource) {
            evtSource.close();
            evtSource = null;
        }
        
        toggleInputs(false);
        
        startBtn.classList.remove('disabled');
        stopBtn.classList.add('disabled');
        stopBtn.disabled = true;
        
        statusText.textContent = 'Stopped';
        statusDot.className = 'pulse-dot status-stopped-dot';
        
        // Reset Tickers Labels
        peStrike.textContent = 'Strike: -';
        peLivePrice.textContent = '₹0.00';
        peDayHigh.textContent = '-';
        peEntryOrd.textContent = '-';
        peSl.textContent = '-';
        peTarget.textContent = '-';
        peReentries.textContent = '-';
        pePos.textContent = 'None';
        pePos.className = 'badge';
        
        ceStrike.textContent = 'Strike: -';
        ceLivePrice.textContent = '₹0.00';
        ceDayHigh.textContent = '-';
        ceEntryOrd.textContent = '-';
        ceSl.textContent = '-';
        ceTarget.textContent = '-';
        ceReentries.textContent = '-';
        cePos.textContent = 'None';
        cePos.className = 'badge';
        
        if (activePositionsTbody) {
            activePositionsTbody.innerHTML = `
                <tr>
                    <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 24px; font-style: italic;">
                        No active positions. Open trades will appear here during live market hours.
                    </td>
                </tr>
            `;
        }
        if (activePositionsMarginBar) {
            activePositionsMarginBar.style.marginTop = '15px';
            activePositionsMarginBar.style.padding = '12px 20px';
            activePositionsMarginBar.style.background = 'rgba(255, 255, 255, 0.05)';
            activePositionsMarginBar.style.borderRadius = '8px';
            activePositionsMarginBar.style.display = 'block';
            activePositionsMarginBar.style.borderLeft = '4px solid #3B82F6';
            
            activePositionsMarginBar.innerHTML = `
                <div>
                    <i class="fa-solid fa-circle-info" style="color: #3B82F6; margin-right: 8px;"></i>
                    <span style="color: var(--text-secondary); font-size: 0.85rem;">Peak Margin Estimate (2 Lots CE & PE Short):</span> <strong style="font-size: 0.95rem; margin-left: 5px;">~₹2,50,000</strong>
                    <span style="color: var(--text-secondary); font-size: 0.85rem; margin-left: 15px;">Recommended Safe Capital:</span> <strong style="font-size: 0.95rem; margin-left: 5px; color: #3B82F6;">₹3,00,000</strong>
                </div>
            `;
        }
        
        if (liveNetEquity) {
            const currentCapital = parseFloat(document.getElementById('capital').value) || 500000.0;
            liveNetEquity.textContent = formatINR(currentCapital);
        }
        if (liveRealizedPnl) {
            liveRealizedPnl.textContent = '₹0.00';
            liveRealizedPnl.className = 'live-acc-value';
        }
        if (liveUnrealizedPnl) {
            liveUnrealizedPnl.textContent = '₹0.00';
            liveUnrealizedPnl.className = 'live-acc-value';
        }
        
        liveExpiry.textContent = 'Loading...';
        marketStatusVal.textContent = 'UNKNOWN';
        marketStatusVal.className = 'text-orange';
        
        prevPrices = { spot: 0, pe: 0, ce: 0 };
    }

    // Initialize Chart.js
    function initChart() {
        const ctx = document.getElementById('live-premium-chart').getContext('2d');
        
        if (liveChart) {
            liveChart.destroy();
        }
        
        liveChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        label: 'PE Put Price (₹)',
                        data: [],
                        borderColor: '#06B6D4',
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: false,
                        tension: 0.15
                    },
                    {
                        label: 'CE Call Price (₹)',
                        data: [],
                        borderColor: '#6366F1',
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: false,
                        tension: 0.15
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'top',
                        labels: { color: '#9CA3AF', boxWidth: 12, font: { size: 10 } }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(34, 43, 62, 0.2)' },
                        ticks: { color: '#9CA3AF', font: { size: 9 }, maxRotation: 0 }
                    },
                    y: {
                        grid: { color: 'rgba(34, 43, 62, 0.2)' },
                        ticks: { color: '#9CA3AF', font: { size: 9 } }
                    }
                }
            }
        });
    }

    // Update Chart Ticks
    function updateChartData() {
        if (!liveChart) return;
        
        liveChart.data.labels = chartTicks.map(t => t.time);
        liveChart.data.datasets[0].data = chartTicks.map(t => t.pe);
        liveChart.data.datasets[1].data = chartTicks.map(t => t.ce);
        liveChart.update('none'); // Update without full recalculation animation for performance
    }

    // Fetch and render date-grouped bifurcated trades log
    async function fetchAndRenderTrades() {
        const container = document.getElementById('modal-trade-list-container');
        container.innerHTML = '<div style="color: #9CA3AF; text-align: center; padding: 20px; font-family: \'Inter\', sans-serif;">Loading trade history...</div>';
        
        try {
            const res = await fetch('/api/live/trades');
            const trades = await res.json();
            
            if (!trades || trades.length === 0) {
                container.innerHTML = '<div style="color: #9CA3AF; text-align: center; padding: 20px; font-style: italic; font-family: \'Inter\', sans-serif;">No completed paper trades recorded yet.</div>';
                return;
            }
            
            // Group trades by date
            const grouped = {};
            trades.forEach(t => {
                if (!grouped[t.date]) {
                    grouped[t.date] = [];
                }
                grouped[t.date].push(t);
            });
            
            // Sort dates descending
            const sortedDates = Object.keys(grouped).sort((a, b) => new Date(b) - new Date(a));
            
            container.innerHTML = '';
            
            sortedDates.forEach(dateStr => {
                const dayTrades = grouped[dateStr];
                
                // Calculate day PnL
                const dayPnl = dayTrades.reduce((sum, t) => sum + (t.pnl || 0), 0);
                const pnlClass = dayPnl >= 0 ? 'text-green' : 'text-red';
                const pnlSign = dayPnl >= 0 ? '+' : '';
                
                const dateGroupDiv = document.createElement('div');
                dateGroupDiv.className = 'log-date-group';
                
                // Format date nicely
                const formattedDate = new Date(dateStr).toLocaleDateString('en-IN', {
                    day: 'numeric', month: 'short', year: 'numeric'
                });
                
                // Header with date and summary
                dateGroupDiv.innerHTML = `
                    <div class="log-date-header">
                        <span><i class="fa-solid fa-calendar-day"></i> ${formattedDate}</span>
                        <span class="log-date-summary">Day PnL: <strong class="${pnlClass}">${pnlSign}${formatINR(dayPnl)}</strong></span>
                    </div>
                    <div class="log-bifurcated-columns">
                        <div class="log-col">
                            <div class="log-col-title pe-title"><i class="fa-solid fa-circle-down"></i> Put Option (PE) Trades</div>
                            <div class="log-trade-cards" id="pe-cards-${dateStr}"></div>
                        </div>
                        <div class="log-col">
                            <div class="log-col-title ce-title"><i class="fa-solid fa-circle-up"></i> Call Option (CE) Trades</div>
                            <div class="log-trade-cards" id="ce-cards-${dateStr}"></div>
                        </div>
                    </div>
                `;
                
                container.appendChild(dateGroupDiv);
                
                const peCardsContainer = document.getElementById(`pe-cards-${dateStr}`);
                const ceCardsContainer = document.getElementById(`ce-cards-${dateStr}`);
                
                const peTrades = dayTrades.filter(t => t.type === 'PE');
                const ceTrades = dayTrades.filter(t => t.type === 'CE');
                
                if (peTrades.length === 0) {
                    peCardsContainer.innerHTML = '<div class="log-empty-state">No PE trades executed on this date.</div>';
                } else {
                    peTrades.forEach(t => {
                        peCardsContainer.appendChild(createTradeCard(t));
                    });
                }
                
                if (ceTrades.length === 0) {
                    ceCardsContainer.innerHTML = '<div class="log-empty-state">No CE trades executed on this date.</div>';
                } else {
                    ceTrades.forEach(t => {
                        ceCardsContainer.appendChild(createTradeCard(t));
                    });
                }
            });
        } catch (error) {
            container.innerHTML = `<div style="color: #EF4444; text-align: center; padding: 20px; font-family: 'Inter', sans-serif;">Error loading trades: ${error.message}</div>`;
        }
    }
    
    function createTradeCard(t) {
        const card = document.createElement('div');
        card.className = 'log-trade-card';
        
        const pnlClass = t.pnl >= 0 ? 'text-green' : 'text-red';
        const pnlSign = t.pnl >= 0 ? '+' : '';
        const cardClass = t.type === 'PE' ? 'badge-pe' : 'badge-ce';
        
        card.innerHTML = `
            <div class="log-card-header">
                <span class="badge ${cardClass}">${t.strike} ${t.type}</span>
                <span style="color: var(--text-muted); font-size: 10px; font-family: 'Inter', sans-serif;">${t.reason}</span>
            </div>
            <div class="log-card-grid" style="font-family: 'Inter', sans-serif;">
                <div class="log-card-item"><span>Lots</span><span>${t.lots}</span></div>
                <div class="log-card-item"><span>Entry</span><span>₹${t.entry_price.toFixed(2)} (${t.entry_time})</span></div>
                <div class="log-card-item"><span>Exit</span><span>₹${t.exit_price.toFixed(2)} (${t.exit_time})</span></div>
                <div class="log-card-item"><span>SL Price</span><span>₹${t.sl.toFixed(2)}</span></div>
                <div class="log-card-item" style="grid-column: span 2;"><span>Day High X (At Entry)</span><span>₹${t.day_high.toFixed(2)}</span></div>
            </div>
            <div class="log-card-pnl-row" style="font-family: 'Inter', sans-serif;">
                <span>Net PnL:</span>
                <span class="${pnlClass}">${pnlSign}${formatINR(t.pnl)}</span>
            </div>
        `;
        return card;
    }

    let kiteStatusInterval = null;
    let lastLoggedKiteStatus = null;
    
    function startKiteStatusPolling() {
        if (kiteStatusInterval) clearInterval(kiteStatusInterval);
        lastLoggedKiteStatus = null;
        
        const totpModal = document.getElementById('totp-modal');
        const totpInput = document.getElementById('totp-code-input');
        const submitBtn = document.getElementById('submit-totp-btn');
        const cancelBtn = document.getElementById('cancel-totp-btn');
        const promptMsg = document.getElementById('totp-prompt-message');
        
        totpInput.value = '';
        
        kiteStatusInterval = setInterval(async () => {
            try {
                const res = await fetch('/api/live/kite-status');
                const data = await res.json();
                
                if (data.status === 'AWAITING_TOTP') {
                    if (!totpModal.classList.contains('open')) {
                        totpModal.classList.add('open');
                        totpInput.focus();
                        promptMsg.textContent = data.message || "Please enter the 6-digit TOTP code.";
                        writeToTerminal(`>> ZERODHA: Awaiting manual TOTP input...`, 'system');
                    }
                } else if (data.status === 'FAILED') {
                    clearInterval(kiteStatusInterval);
                    kiteStatusInterval = null;
                    totpModal.classList.remove('open');
                    writeToTerminal(`>> ZERODHA LOGIN FAILED: ${data.message}`, 'exit');
                    await fetch('/api/live/control', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ action: 'stop' })
                    });
                    resetLiveUI();
                } else if (data.status === 'CONNECTED') {
                    clearInterval(kiteStatusInterval);
                    kiteStatusInterval = null;
                    totpModal.classList.remove('open');
                    if (lastLoggedKiteStatus !== 'CONNECTED') {
                        writeToTerminal(`>> ZERODHA: Connection successfully established.`, 'system');
                        lastLoggedKiteStatus = 'CONNECTED';
                    }
                } else if (data.status && data.status !== 'IDLE' && lastLoggedKiteStatus !== data.status) {
                    writeToTerminal(`>> ZERODHA: Authentication status - ${data.status} (${data.message || ''})`, 'system');
                    lastLoggedKiteStatus = data.status;
                }
            } catch (error) {
                console.error('Error polling Zerodha status:', error);
            }
        }, 1500);
        
        submitBtn.onclick = async () => {
            const code = totpInput.value.trim();
            if (code.length !== 6 || isNaN(code)) {
                alert("Please enter a valid 6-digit number.");
                return;
            }
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Submitting...';
            try {
                const res = await fetch('/api/live/totp', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ code })
                });
                const responseData = await res.json();
                if (res.ok) {
                    writeToTerminal(`>> ZERODHA: TOTP code submitted. Waiting for authentication...`, 'system');
                } else {
                    alert(`Error submitting code: ${responseData.error}`);
                }
            } catch (err) {
                alert(`Error: ${err.message}`);
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="fa-solid fa-check"></i> Submit Code';
            }
        };
        
        cancelBtn.onclick = async () => {
            clearInterval(kiteStatusInterval);
            kiteStatusInterval = null;
            totpModal.classList.remove('open');
            writeToTerminal(`>> ZERODHA: Authentication cancelled by user.`, 'exit');
            await fetch('/api/live/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'stop' })
            });
            resetLiveUI();
        };
    }

    function updateInceptionMetrics(inc) {
        if (!inc) return;
        const liveInceptionPnl = document.getElementById('live-inception-pnl');
        const liveInceptionSub = document.getElementById('live-inception-sub');
        const incStartDate = document.getElementById('inc-start-date');
        const incTotalPnlVal = document.getElementById('inc-total-pnl-val');
        const incBadgePnl = document.getElementById('inc-badge-pnl');
        const incNetPnl = document.getElementById('inc-net-pnl');
        const incTotalTrades = document.getElementById('inc-total-trades');
        const incWinRate = document.getElementById('inc-win-rate');
        const incProfitFactor = document.getElementById('inc-profit-factor');
        const incGrossProfit = document.getElementById('inc-gross-profit');
        const incGrossLoss = document.getElementById('inc-gross-loss');

        const totalPnl = inc.total_pnl || 0;
        if (liveInceptionPnl) {
            liveInceptionPnl.textContent = formatINR(totalPnl);
            liveInceptionPnl.className = totalPnl >= 0 ? 'live-acc-value text-green' : 'live-acc-value text-red';
        }
        if (liveInceptionSub) {
            liveInceptionSub.textContent = `${inc.total_trades || 0} Trades | ${inc.win_rate || 0}% Win Rate`;
        }
        if (incStartDate) incStartDate.textContent = inc.start_date || 'N/A';
        if (incTotalPnlVal) {
            incTotalPnlVal.textContent = formatINR(totalPnl);
            incTotalPnlVal.className = totalPnl >= 0 ? 'inc-badge-value text-green' : 'inc-badge-value text-red';
        }
        if (incBadgePnl) {
            incBadgePnl.style.background = totalPnl >= 0 ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)';
            incBadgePnl.style.borderColor = totalPnl >= 0 ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)';
        }
        if (incNetPnl) {
            incNetPnl.textContent = formatINR(totalPnl);
            incNetPnl.className = totalPnl >= 0 ? 'inc-stat-value text-green' : 'inc-stat-value text-red';
        }
        if (incTotalTrades) incTotalTrades.textContent = inc.total_trades || 0;
        if (incWinRate) incWinRate.textContent = `${inc.win_rate || 0}%`;
        if (incProfitFactor) incProfitFactor.textContent = inc.profit_factor || 'N/A';
        if (incGrossProfit) incGrossProfit.textContent = formatINR(inc.gross_profit || 0);
        if (incGrossLoss) incGrossLoss.textContent = formatINR(inc.gross_loss || 0);
    }

    async function loadInceptionSummary() {
        try {
            const res = await fetch('/api/live/inception-summary');
            if (res.ok) {
                const data = await res.json();
                updateInceptionMetrics(data);
            }
        } catch (e) {
            console.error('Error fetching inception summary:', e);
        }
    }

    // Check if the live engine is already running on page load
    async function checkActiveSession() {
        // Load configs
        await loadConfig();
        await loadInceptionSummary();
        await fetchMarginInfo();
        // Start EventSource stream immediately to see if it is running
        connectSSE();
    }
    
    checkActiveSession();
});
