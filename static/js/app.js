document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const dataSourceSelect = document.getElementById('data-source-select');
    const simulatedParams = document.getElementById('simulated-params');
    const csvParams = document.getElementById('csv-params');
    const configForm = document.getElementById('config-form');
    const runBtn = document.getElementById('run-backtest-btn');
    const loadingOverlay = document.getElementById('loading-overlay');
    const searchInput = document.getElementById('trade-search');
    
    // KPI Metrics Elements
    const metricPnl = document.getElementById('metric-pnl');
    const metricRom = document.getElementById('metric-rom');
    const metricWinrate = document.getElementById('metric-winrate');
    const metricWinLossRatio = document.getElementById('metric-winloss-ratio');
    const metricTrades = document.getElementById('metric-trades');
    const metricAvgTrades = document.getElementById('metric-avg-trades');
    const metricDrawdown = document.getElementById('metric-drawdown');
    const metricDrawdownAmount = document.getElementById('metric-drawdown-amount');
    const metricProfitFactor = document.getElementById('metric-profit-factor');
    const metricPnlRatio = document.getElementById('metric-pnl-ratio');
    
    const activeSummary = document.getElementById('active-backtest-summary');
    const statusText = document.getElementById('status-text');
    const tableBody = document.getElementById('trade-log-tbody');
    
    let equityChart = null;
    let originalTradesData = []; // Store raw response trades for filtering

    // 1. Toggle Parameters UI based on Data Source Mode
    dataSourceSelect.addEventListener('change', (e) => {
        if (e.target.value === 'simulated') {
            simulatedParams.classList.remove('hidden');
            csvParams.classList.add('hidden');
        } else {
            simulatedParams.classList.add('hidden');
            csvParams.classList.remove('hidden');
        }
    });

    // 2. Handle Form Submission
    configForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        // Show loading
        loadingOverlay.classList.remove('hidden');
        statusText.textContent = 'Running Backtest...';
        
        try {
            const mode = dataSourceSelect.value;
            let responseData = null;
            
            // Gather standard input parameters
            const strategyType = document.getElementById('strategy_type').value;
            const strikeOffsetPe = document.getElementById('strike_offset_pe').value;
            const strikeOffsetCe = document.getElementById('strike_offset_ce').value;
            const entryDiscount = document.getElementById('entry_discount').value;
            const lot1Target = document.getElementById('lot_1_target').value;
            const maxReentries = document.getElementById('max_reentries').value;
            const lotSize = document.getElementById('lot_size').value;
            const capital = document.getElementById('capital').value;
            
            if (mode === 'simulated') {
                // Parse API parameters
                const symbol = document.getElementById('symbol').value;
                const period = document.getElementById('period').value;
                const iv = document.getElementById('iv').value;
                const r = document.getElementById('r').value;
                
                const payload = {
                    symbol, period, strategy_type: strategyType,
                    strike_offset_pe: strikeOffsetPe, strike_offset_ce: strikeOffsetCe,
                    entry_discount: entryDiscount, lot_1_target: lot1Target,
                    max_reentries: maxReentries, lot_size: lotSize,
                    iv, r, capital
                };
                
                const response = await fetch('/api/backtest', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                
                if (!response.ok) {
                    const err = await response.json();
                    throw new Error(err.error || 'Server error running backtest.');
                }
                
                responseData = await response.json();
            } else {
                // CSV upload mode
                const fileInput = document.getElementById('csv-file');
                if (!fileInput.files || fileInput.files.length === 0) {
                    throw new Error('Please select a CSV file to upload.');
                }
                
                const formData = new FormData();
                formData.append('file', fileInput.files[0]);
                formData.append('strategy_type', strategyType);
                formData.append('strike_offset_pe', strikeOffsetPe);
                formData.append('strike_offset_ce', strikeOffsetCe);
                formData.append('entry_discount', entryDiscount);
                formData.append('lot_1_target', lot1Target);
                formData.append('max_reentries', maxReentries);
                formData.append('lot_size', lotSize);
                formData.append('capital', capital);
                
                const response = await fetch('/api/upload-csv', {
                    method: 'POST',
                    body: formData
                });
                
                if (!response.ok) {
                    const err = await response.json();
                    throw new Error(err.error || 'Server error processing CSV file.');
                }
                
                responseData = await response.json();
            }
            
            // Process and display results
            displayResults(responseData);
            statusText.textContent = 'Ready';
            
        } catch (error) {
            console.error(error);
            alert(error.message);
            statusText.textContent = 'Error';
        } finally {
            // Hide loading
            loadingOverlay.classList.add('hidden');
        }
    });

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

    // 3. Display Results
    function displayResults(data) {
        originalTradesData = data.trade_log || [];
        
        // Update header summary text
        const totalDays = [...new Set(originalTradesData.map(t => t.Date))].length || 1;
        activeSummary.textContent = `Asset: ${data.symbol} | Range: ${data.period} | Strategy: ${data.strategy} | Trades Analyzed: ${data.total_trades} over ${totalDays} trading days.`;
        
        // Net PnL Card
        const pnl = data.net_pnl;
        metricPnl.textContent = formatINR(pnl);
        metricPnl.className = pnl >= 0 ? 'metric-value color-success' : 'metric-value color-danger';
        metricRom.textContent = `${data.rom}% Return on Capital`;
        
        // Win Rate Card
        metricWinrate.textContent = `${data.win_rate}%`;
        const winningTrades = originalTradesData.filter(t => t.PnL > 0).length;
        const losingTrades = originalTradesData.filter(t => t.PnL < 0).length;
        metricWinLossRatio.textContent = `${winningTrades} Wins / ${losingTrades} Losses`;
        
        // Total Trades Card
        metricTrades.textContent = data.total_trades;
        const avgDaily = (data.total_trades / totalDays).toFixed(1);
        metricAvgTrades.textContent = `${avgDaily} Avg trades/day`;
        
        // Max Drawdown Card
        metricDrawdown.textContent = `${data.max_drawdown}%`;
        metricDrawdownAmount.textContent = `${formatINR(data.max_drawdown_amount)} Max dip`;
        
        // Profit Factor Card
        metricProfitFactor.textContent = data.profit_factor;
        const grossProf = formatINR(data.gross_profit);
        const grossLoss = formatINR(data.gross_loss);
        metricPnlRatio.textContent = `Profits: ${grossProf} / Loss: ${grossLoss}`;
        
        // Render Chart
        renderChart(data.equity_curve);
        
        // Render Trade Log Table
        renderTable(originalTradesData);
    }

    // 4. Render Chart
    function renderChart(curveData) {
        const ctx = document.getElementById('equity-chart').getContext('2d');
        
        if (equityChart) {
            equityChart.destroy();
        }
        
        const labels = curveData.map(c => c.Date);
        const dataValues = curveData.map(c => c.Cumulative);
        
        // Create Indigo Glowing Gradient
        const gradient = ctx.createLinearGradient(0, 0, 0, 300);
        gradient.addColorStop(0, 'rgba(99, 102, 241, 0.25)');
        gradient.addColorStop(1, 'rgba(99, 102, 241, 0.00)');
        
        equityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Cumulative Portfolio Equity (₹)',
                    data: dataValues,
                    borderColor: '#6366F1',
                    borderWidth: 2,
                    pointBackgroundColor: '#6366F1',
                    pointBorderColor: '#FFF',
                    pointHoverRadius: 5,
                    fill: true,
                    backgroundColor: gradient,
                    tension: 0.15
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        backgroundColor: '#151C2C',
                        titleColor: '#F9FAFB',
                        bodyColor: '#F9FAFB',
                        borderColor: '#222B3E',
                        borderWidth: 1,
                        callbacks: {
                            label: function(context) {
                                return ` Equity: ${formatINR(context.raw)}`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: {
                            color: 'rgba(34, 43, 62, 0.3)'
                        },
                        ticks: {
                            color: '#9CA3AF',
                            font: { size: 10 }
                        }
                    },
                    y: {
                        grid: {
                            color: 'rgba(34, 43, 62, 0.3)'
                        },
                        ticks: {
                            color: '#9CA3AF',
                            font: { size: 10 },
                            callback: function(value) {
                                return formatINR(value).split('.')[0];
                            }
                        }
                    }
                }
            }
        });
    }

    // 5. Render Table
    function renderTable(trades) {
        tableBody.innerHTML = '';
        
        if (trades.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="11" class="no-data">No trades generated. Adjust parameters and run again.</td></tr>`;
            return;
        }
        
        trades.forEach(trade => {
            const tr = document.createElement('tr');
            
            const isProfit = trade.PnL >= 0;
            const pnlClass = isProfit ? 'color-success font-semibold' : 'color-danger font-semibold';
            const optBadge = trade.Type === 'PE' ? 'badge badge-pe' : 'badge badge-ce';
            
            let reasonBadgeClass = 'badge';
            if (trade.Reason.includes('Target')) {
                reasonBadgeClass += ' badge-success';
            } else if (trade.Reason.includes('Stop Loss') || trade.Reason.includes('Trailing')) {
                reasonBadgeClass += ' badge-danger';
            } else {
                reasonBadgeClass += ' badge-warning';
            }
            
            tr.innerHTML = `
                <td>${trade.Date}</td>
                <td><span class="${optBadge}">${trade.Type}</span></td>
                <td>${trade.Strike}</td>
                <td>₹${trade.EntryPrice.toFixed(2)}</td>
                <td>${trade.EntryTime}</td>
                <td>₹${trade.ExitPrice.toFixed(2)}</td>
                <td>${trade.ExitTime}</td>
                <td>${trade.Lots}</td>
                <td class="${pnlClass}">${isProfit ? '+' : ''}${formatINR(trade.PnL)}</td>
                <td><span class="${reasonBadgeClass}">${trade.Reason}</span></td>
                <td><span class="badge badge-pe">${trade.ReEntryNum}</span></td>
            `;
            tableBody.appendChild(tr);
        });
    }

    // 6. Search/Filter Log Table
    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase();
        
        const filtered = originalTradesData.filter(trade => {
            return (
                trade.Date.toLowerCase().includes(query) ||
                trade.Type.toLowerCase().includes(query) ||
                trade.Strike.toString().includes(query) ||
                trade.Reason.toLowerCase().includes(query)
            );
        });
        
        renderTable(filtered);
    });
});
