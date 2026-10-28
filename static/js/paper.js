document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const playbackDateInput = document.getElementById('playback-date');
    const speedSlider = document.getElementById('playback-speed');
    const speedValue = document.getElementById('speed-value');
    
    const startBtn = document.getElementById('start-btn');
    const pauseBtn = document.getElementById('pause-btn');
    const stopBtn = document.getElementById('stop-btn');
    const configForm = document.getElementById('playback-form');
    
    const timeBadge = document.getElementById('playback-time-badge');
    const statusText = document.getElementById('playback-status-text');
    const statusDot = document.getElementById('playback-status-dot');
    
    // Accounts
    const liveNetEquity = document.getElementById('live-net-equity');
    const liveRealizedPnl = document.getElementById('live-realized-pnl');
    const liveUnrealizedPnl = document.getElementById('live-unrealized-pnl');
    const liveSpotPrice = document.getElementById('live-spot-price');
    
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
    
    // Terminal Log Console
    const terminalLog = document.getElementById('terminal-body-log');
    
    // App State
    let evtSource = null;
    let liveChart = null;
    let prevPrices = { spot: 0, pe: 0, ce: 0 };
    let chartTicks = []; // Array of {time, pe, ce}

    // Set default date to 3 days ago (usually a valid trading weekday)
    const defaultDate = new Date();
    defaultDate.setDate(defaultDate.getDate() - 3);
    playbackDateInput.value = defaultDate.toISOString().split('T')[0];

    // Speed Slider Update
    speedSlider.addEventListener('input', (e) => {
        const val = e.target.value;
        speedValue.textContent = `${val}x`;
        updatePlaybackSpeed(val);
    });

    // Start Playback Click
    startBtn.addEventListener('click', async () => {
        if (startBtn.classList.contains('disabled')) return;
        
        const dateVal = playbackDateInput.value;
        if (!dateVal) {
            alert('Please select a valid playback date.');
            return;
        }
        
        writeToTerminal(`>> Initializing playback simulator for ${dateVal}...`, 'system');
        
        // Disable strategy parameters fields
        toggleInputs(true);
        
        // Gather params
        const speed = speedSlider.value;
        const strategyType = document.getElementById('strategy_type').value;
        const strikeOffsetPe = document.getElementById('strike_offset_pe').value;
        const strikeOffsetCe = document.getElementById('strike_offset_ce').value;
        const entryDiscount = document.getElementById('entry_discount').value;
        const lot1Target = document.getElementById('lot_1_target').value;
        const maxReentries = document.getElementById('max_reentries').value;
        const lotSize = document.getElementById('lot_size').value;
        const capital = document.getElementById('capital').value;
        
        const payload = {
            action: 'start', symbol: '^NSEI', date: dateVal, speed, capital,
            strategy_type: strategyType, strike_offset_pe: strikeOffsetPe,
            strike_offset_ce: strikeOffsetCe, entry_discount: entryDiscount,
            lot_1_target: lot1Target, max_reentries: maxReentries,
            lot_size: lotSize, iv: 15, r: 7 // default dynamic falls back
        };
        
        try {
            const res = await fetch('/api/playback/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Failed to start playback.');
            
            // Set buttons active state
            startBtn.classList.add('disabled');
            pauseBtn.classList.remove('disabled');
            pauseBtn.disabled = false;
            stopBtn.classList.remove('disabled');
            stopBtn.disabled = false;
            
            // Clear chart and logs
            chartTicks = [];
            initChart();
            terminalLog.innerHTML = `<div class="terminal-line system-msg">>> SYSTEM: Simulation started. Waiting for ticks...</div>`;
            
            // Connect to Server-Sent Events stream
            connectSSE();
            
        } catch (error) {
            console.error(error);
            writeToTerminal(`>> ERROR starting playback: ${error.message}`, 'exit');
            toggleInputs(false);
        }
    });

    // Pause/Resume Playback Click
    pauseBtn.addEventListener('click', async () => {
        if (pauseBtn.classList.contains('disabled')) return;
        
        const isPaused = pauseBtn.innerHTML.includes('Resume');
        const action = isPaused ? 'resume' : 'pause';
        
        try {
            const res = await fetch('/api/playback/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action })
            });
            
            if (res.ok) {
                if (isPaused) {
                    pauseBtn.innerHTML = '<i class="fa-solid fa-pause"></i> Pause';
                } else {
                    pauseBtn.innerHTML = '<i class="fa-solid fa-play"></i> Resume';
                }
            }
        } catch (error) {
            console.error(error);
        }
    });

    // Stop Playback Click
    stopBtn.addEventListener('click', async () => {
        if (stopBtn.classList.contains('disabled')) return;
        
        try {
            await fetch('/api/playback/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'stop' })
            });
            
            resetSimulationUI();
            writeToTerminal('>> SYSTEM: Simulation stopped by user.', 'system');
        } catch (error) {
            console.error(error);
        }
    });

    // SSE Stream connection
    function connectSSE() {
        if (evtSource) {
            evtSource.close();
        }
        
        evtSource = new EventSource('/api/playback/stream');
        
        evtSource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            
            if (data.event === 'tick') {
                handleTick(data);
            } else if (data.event === 'status') {
                handleStatus(data.status, data.log);
            } else if (data.event === 'log') {
                writeToTerminal(`>> ${data.log}`, 'system');
            }
        };
        
        evtSource.onerror = function() {
            console.log("SSE Stream disconnected. Reconnecting...");
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

    // Process each minute tick
    function handleTick(data) {
        // 1. Update Simulation Clock
        const timeStr = data.datetime.split(' ')[1];
        timeBadge.innerHTML = `<i class="fa-solid fa-clock"></i> Simulation Clock: ${timeStr} IST`;
        
        // 2. Update Accounts Bar
        liveNetEquity.textContent = formatINR(data.account.balance);
        
        const realized = data.account.realized_pnl;
        liveRealizedPnl.textContent = formatINR(realized);
        liveRealizedPnl.className = realized >= 0 ? 'live-acc-value color-success' : 'live-acc-value color-danger';
        
        const unrealized = data.account.unrealized_pnl;
        liveUnrealizedPnl.textContent = formatINR(unrealized);
        liveUnrealizedPnl.className = unrealized >= 0 ? 'live-acc-value color-success' : 'live-acc-value color-danger';
        
        // Flashing animation on Spot Price
        updateWithFlash(liveSpotPrice, data.spot, 'spot');
        
        // 3. Update PE Option
        if (data.pe && data.pe.strike) {
            peStrike.textContent = `Strike: ${data.pe.strike} PE`;
            updateWithFlash(peLivePrice, data.pe.price, 'pe');
            peDayHigh.textContent = `₹${data.pe.day_high.toFixed(2)}`;
            
            const entryTarget = data.pe.day_high * (1 - parseFloat(document.getElementById('entry_discount').value)/100);
            peEntryOrd.textContent = `Sell Limit @ ₹${entryTarget.toFixed(2)}`;
            peSl.textContent = data.pe.sl > 0 ? `₹${data.pe.sl.toFixed(2)}` : '-';
            peTarget.textContent = data.pe.target > 0 ? `₹${data.pe.target.toFixed(2)}` : '-';
            peReentries.textContent = `${data.pe.reentries}/${document.getElementById('max_reentries').value}`;
            
            updatePositionBadge(pePos, data.pe.position);
        }
        
        // 4. Update CE Option
        if (data.ce && data.ce.strike) {
            ceStrike.textContent = `Strike: ${data.ce.strike} CE`;
            updateWithFlash(ceLivePrice, data.ce.price, 'ce');
            ceDayHigh.textContent = `₹${data.ce.day_high.toFixed(2)}`;
            
            const entryTarget = data.ce.day_high * (1 - parseFloat(document.getElementById('entry_discount').value)/100);
            ceEntryOrd.textContent = `Sell Limit @ ₹${entryTarget.toFixed(2)}`;
            ceSl.textContent = data.ce.sl > 0 ? `₹${data.ce.sl.toFixed(2)}` : '-';
            ceTarget.textContent = data.ce.target > 0 ? `₹${data.ce.target.toFixed(2)}` : '-';
            ceReentries.textContent = `${data.ce.reentries}/${document.getElementById('max_reentries').value}`;
            
            updatePositionBadge(cePos, data.ce.position);
        }
        
        // 5. Append Logs to Terminal
        if (data.logs && data.logs.length > 0) {
            data.logs.forEach(log => {
                let type = 'system';
                if (log.includes('[ENTRY')) type = 'fill';
                else if (log.includes('[STOP LOSS')) type = 'exit';
                else if (log.includes('[TARGET')) type = 'fill';
                else if (log.includes('[TRAILING')) type = 'trail';
                else if (log.includes('[SQUARE-OFF')) type = 'order';
                
                writeToTerminal(log, type);
            });
        }
        
        // 6. Push to Chart Data & Update
        chartTicks.push({
            time: timeStr,
            pe: data.pe ? data.pe.price : null,
            ce: data.ce ? data.ce.price : null
        });
        
        // Keep last 30 minutes in sliding window
        if (chartTicks.length > 35) {
            chartTicks.shift();
        }
        
        updateChartData();
    }

    // Flashing Animation Ticks
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

    // Handles simulation status events
    function handleStatus(status, log) {
        statusText.textContent = status;
        
        // Adjust badge styles
        statusDot.className = 'pulse-dot';
        if (status === 'STOPPED') {
            statusDot.classList.add('status-stopped-dot');
            resetSimulationUI();
        } else if (status === 'PAUSED') {
            statusDot.classList.add('status-paused-dot');
        } else if (status === 'RUNNING') {
            statusDot.classList.add('status-running-dot');
        }
        
        if (log) {
            writeToTerminal(`>> ${log}`, 'system');
        }
    }

    // Helper to send real-time speed adjustments
    async function updatePlaybackSpeed(speed) {
        if (stopBtn.classList.contains('disabled')) return; // not running
        
        try {
            await fetch('/api/playback/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'speed', speed })
            });
        } catch (error) {
            console.error('Error setting speed:', error);
        }
    }

    // Helper to write colorized lines to terminal
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
            if (input.id !== 'playback-speed') {
                input.disabled = disabled;
                if (disabled) {
                    input.classList.add('disabled-input');
                } else {
                    input.classList.remove('disabled-input');
                }
            }
        });
    }

    // Reset simulator UI elements on Stop
    function resetSimulationUI() {
        if (evtSource) {
            evtSource.close();
            evtSource = null;
        }
        
        // Enable inputs
        toggleInputs(false);
        
        // Reset buttons
        startBtn.classList.remove('disabled');
        pauseBtn.classList.add('disabled');
        pauseBtn.disabled = true;
        pauseBtn.innerHTML = '<i class="fa-solid fa-pause"></i> Pause';
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
        
        prevPrices = { spot: 0, pe: 0, ce: 0 };
    }

    // Chart.js Initialize
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
});
