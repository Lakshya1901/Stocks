/* ================================================================
   DayTrader Dashboard — Client-Side JavaScript
   WebSocket + Chart.js + Real-Time Updates
   ================================================================ */

// ============ Socket.IO Connection ============
const socket = io({ transports: ['polling'] });

// ============ State ============
const state = {
    pnlHistory: [],
    maxSignals: 50,
};

// ============ Clock ============
function updateClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    document.getElementById('clock').textContent = `${h}:${m}:${s}`;
}
setInterval(updateClock, 1000);
updateClock();

// ============ P&L Chart ============
const pnlCtx = document.getElementById('pnl-chart').getContext('2d');
const pnlChart = new Chart(pnlCtx, {
    type: 'line',
    data: {
        labels: [],
        datasets: [{
            label: 'P&L',
            data: [],
            borderColor: '#6366f1',
            backgroundColor: 'rgba(99, 102, 241, 0.1)',
            borderWidth: 2,
            fill: true,
            tension: 0.4,
            pointRadius: 0,
            pointHoverRadius: 4,
            pointHoverBackgroundColor: '#818cf8',
        }],
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: 'index' },
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: 'rgba(17, 24, 39, 0.95)',
                titleColor: '#f1f5f9',
                bodyColor: '#94a3b8',
                borderColor: 'rgba(255,255,255,0.1)',
                borderWidth: 1,
                padding: 10,
                displayColors: false,
                callbacks: {
                    label: (ctx) => `P&L: ${ctx.parsed.y.toFixed(2)}`,
                },
            },
        },
        scales: {
            x: {
                display: true,
                grid: { color: 'rgba(255,255,255,0.03)' },
                ticks: { color: '#64748b', font: { size: 10, family: "'JetBrains Mono'" }, maxTicksLimit: 10 },
            },
            y: {
                display: true,
                grid: { color: 'rgba(255,255,255,0.03)' },
                ticks: {
                    color: '#64748b',
                    font: { size: 10, family: "'JetBrains Mono'" },
                    callback: (v) => v.toFixed(0),
                },
            },
        },
    },
});

function addPnlPoint(time, value) {
    state.pnlHistory.push({ time, value });
    if (state.pnlHistory.length > 200) {
        state.pnlHistory.shift();
    }

    pnlChart.data.labels = state.pnlHistory.map(p => p.time);
    pnlChart.data.datasets[0].data = state.pnlHistory.map(p => p.value);

    // Dynamic gradient based on P&L
    const lastValue = state.pnlHistory[state.pnlHistory.length - 1].value;
    if (lastValue >= 0) {
        pnlChart.data.datasets[0].borderColor = '#10b981';
        pnlChart.data.datasets[0].backgroundColor = 'rgba(16, 185, 129, 0.1)';
    } else {
        pnlChart.data.datasets[0].borderColor = '#ef4444';
        pnlChart.data.datasets[0].backgroundColor = 'rgba(239, 68, 68, 0.1)';
    }

    pnlChart.update('none');
}

// ============ Format Helpers ============
function formatPnl(value) {
    const num = parseFloat(value) || 0;
    return num >= 0 ? `+${num.toFixed(2)}` : num.toFixed(2);
}

function pnlClass(value) {
    const num = parseFloat(value) || 0;
    if (num > 0) return 'positive';
    if (num < 0) return 'negative';
    return '';
}

function pnlTdClass(value) {
    const num = parseFloat(value) || 0;
    if (num > 0) return 'pnl-positive';
    if (num < 0) return 'pnl-negative';
    return '';
}

// ============ Update Stats Bar ============
function updateStats(metrics, risk) {
    const totalPnl = document.getElementById('total-pnl');
    const realizedPnl = document.getElementById('realized-pnl');
    const unrealizedPnl = document.getElementById('unrealized-pnl');
    const winRate = document.getElementById('win-rate');
    const totalTrades = document.getElementById('total-trades');
    const openPositions = document.getElementById('open-positions');

    if (metrics) {
        totalPnl.textContent = formatPnl(metrics.total_pnl);
        totalPnl.className = `stat-value ${pnlClass(metrics.total_pnl)}`;

        realizedPnl.textContent = formatPnl(metrics.realized_pnl);
        realizedPnl.className = `stat-value ${pnlClass(metrics.realized_pnl)}`;

        unrealizedPnl.textContent = formatPnl(metrics.unrealized_pnl);
        unrealizedPnl.className = `stat-value ${pnlClass(metrics.unrealized_pnl)}`;

        winRate.textContent = `${(metrics.win_rate || 0).toFixed(0)}%`;
        totalTrades.textContent = metrics.total_trades || 0;
    }

    if (risk) {
        openPositions.textContent = risk.open_positions || 0;
        document.getElementById('position-count').textContent = risk.open_positions || 0;

        // Update risk progress bars
        const lossUsed = Math.abs(Math.min(risk.pnl || 0, 0));
        const lossLimit = 400;  // from config
        const lossPct = Math.min((lossUsed / lossLimit) * 100, 100);
        document.getElementById('loss-progress').style.width = `${lossPct}%`;
        document.getElementById('loss-progress').className =
            `progress-fill ${lossPct > 70 ? 'progress-danger' : ''}`;
        document.getElementById('loss-limit-text').textContent =
            `${lossUsed.toFixed(0)} / ${lossLimit}`;

        const posPct = ((risk.open_positions || 0) / 6) * 100;
        document.getElementById('position-progress').style.width = `${posPct}%`;
        document.getElementById('position-slots-text').textContent =
            `${risk.open_positions || 0} / 6`;

        const capitalPct = ((risk.capital_used || 0) / 20000) * 100;
        document.getElementById('capital-progress').style.width = `${capitalPct}%`;
        document.getElementById('capital-used-text').textContent =
            `${(risk.capital_used || 0).toFixed(0)} / 20000`;
    }
}

// ============ Update Positions Table ============
function updatePositions(positions) {
    const tbody = document.getElementById('positions-body');

    if (!positions || positions.length === 0) {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="9">No open positions</td></tr>';
        return;
    }

    tbody.innerHTML = positions.map(p => `
        <tr>
            <td style="color: var(--accent-light); font-weight: 600;">${p.symbol}</td>
            <td><span class="type-${p.type.toLowerCase()}">${p.type}</span></td>
            <td>${p.quantity}</td>
            <td>${p.entry_price.toFixed(2)}</td>
            <td>${p.current_price.toFixed(2)}</td>
            <td class="${pnlTdClass(p.pnl)}">${formatPnl(p.pnl)}</td>
            <td class="${pnlTdClass(p.pnl_pct)}">${formatPnl(p.pnl_pct)}%</td>
            <td>${p.stop_loss.toFixed(2)}</td>
            <td style="color: var(--text-muted); font-family: var(--font-sans); font-size: 0.7rem;">${p.strategy}</td>
        </tr>
    `).join('');
}

// ============ Update Trade History ============
function updateHistory(trades) {
    const tbody = document.getElementById('history-body');

    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="8">No trades yet</td></tr>';
        return;
    }

    tbody.innerHTML = trades.map(t => `
        <tr>
            <td style="color: var(--accent-light); font-weight: 600;">${t.symbol}</td>
            <td><span class="type-${t.type.toLowerCase()}">${t.type}</span></td>
            <td>${t.quantity}</td>
            <td>${t.entry_price.toFixed(2)}</td>
            <td>${t.exit_price.toFixed(2)}</td>
            <td class="${pnlTdClass(t.pnl)}">${formatPnl(t.pnl)}</td>
            <td>${t.holding_mins.toFixed(0)}m</td>
            <td style="color: var(--text-muted); font-family: var(--font-sans); font-size: 0.7rem;">${t.strategy}</td>
        </tr>
    `).join('');
}

// ============ Update Scanner Results ============
function updateScanner(results) {
    const container = document.getElementById('scanner-results');

    if (!results || results.length === 0) {
        container.innerHTML = '<p class="muted">Scanner results will appear here at 9:00 AM</p>';
        return;
    }

    container.innerHTML = results.map(r => `
        <div class="scanner-item">
            <div class="scanner-symbol">${r.symbol}</div>
            <div class="scanner-price">${(r.price || 0).toFixed(2)}</div>
            <div class="scanner-score">Score: ${(r.score || 0).toFixed(2)}</div>
            <span class="scanner-tier tier-${r.tier || 'mid'}">${r.tier || '?'}</span>
        </div>
    `).join('');
}

// ============ Add Signal to Feed ============
function addSignal(signal) {
    const feed = document.getElementById('signals-feed');

    // Remove "waiting" message
    const muted = feed.querySelector('.muted');
    if (muted) muted.remove();

    const type = (signal.signal_type || signal.type || 'hold').toLowerCase();
    const div = document.createElement('div');
    div.className = `signal-item signal-${type}`;
    div.innerHTML = `
        <span class="signal-time">${signal.time || new Date().toLocaleTimeString()}</span>
        <span class="signal-symbol">${signal.symbol || '---'}</span>
        <span class="signal-text">${signal.reason || signal.strategy || ''}</span>
        <span class="signal-confidence" style="color: var(--${type === 'buy' ? 'green' : type === 'sell' ? 'red' : 'text-muted'})">
            ${((signal.confidence || 0) * 100).toFixed(0)}%
        </span>
    `;

    feed.insertBefore(div, feed.firstChild);

    // Limit feed size
    while (feed.children.length > state.maxSignals) {
        feed.removeChild(feed.lastChild);
    }
}

// ============ Socket Events ============
socket.on('connect', () => {
    document.getElementById('status-badge').className = 'badge badge-online';
    document.getElementById('status-text').textContent = 'Connected';
});

socket.on('disconnect', () => {
    document.getElementById('status-badge').className = 'badge badge-offline';
    document.getElementById('status-text').textContent = 'Disconnected';
});

socket.on('tick', (data) => {
    // Add P&L data point
    if (data.pnl !== undefined) {
        addPnlPoint(data.time, data.pnl);
    }
});

socket.on('trade', (data) => {
    // Refresh portfolio
    fetchPortfolio();
});

socket.on('signal', (data) => {
    addSignal(data);
});

socket.on('portfolio', (data) => {
    if (data.open_positions) updatePositions(data.open_positions);
    if (data.closed_positions) updateHistory(data.closed_positions);
    if (data.metrics) updateStats(data.metrics, null);
});

// ============ API Polling ============
async function fetchPortfolio() {
    try {
        const res = await fetch('/api/portfolio');
        const data = await res.json();
        if (data.open_positions) updatePositions(data.open_positions);
        if (data.closed_positions) updateHistory(data.closed_positions);
        if (data.metrics) updateStats(data.metrics, null);
    } catch (e) { /* silent */ }
}

async function fetchMetrics() {
    try {
        const res = await fetch('/api/metrics');
        const data = await res.json();
        updateStats(data.performance, data.risk);

        // Update mode badge
        const modeBadge = document.getElementById('mode-badge');
        if (data.mode === 'live') {
            modeBadge.className = 'badge badge-live';
            modeBadge.textContent = 'LIVE';
        } else {
            modeBadge.className = 'badge badge-paper';
            modeBadge.textContent = 'PAPER';
        }
    } catch (e) { /* silent */ }
}

async function fetchScanner() {
    try {
        const res = await fetch('/api/scanner');
        const data = await res.json();
        updateScanner(data);
    } catch (e) { /* silent */ }
}

async function fetchSignals() {
    try {
        const res = await fetch('/api/signals');
        const data = await res.json();
        // Only populate on initial load
        if (data.length > 0 && document.querySelector('#signals-feed .muted')) {
            data.reverse().forEach(s => {
                addSignal({
                    symbol: s.symbol,
                    type: s.signals ? 'info' : 'hold',
                    confidence: s.buy_score || s.sell_score || 0,
                    time: new Date(s.timestamp).toLocaleTimeString(),
                    reason: s.signals ? s.signals.map(x => x.join(':')).join(', ') : '',
                });
            });
        }
    } catch (e) { /* silent */ }
}

// ============ Emergency Square-off ============
document.getElementById('emergency-btn').addEventListener('click', async () => {
    if (!confirm('Are you sure you want to SQUARE OFF all open positions?')) return;

    try {
        const res = await fetch('/api/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'emergency_squareoff' }),
        });
        const data = await res.json();
        alert(data.message);
    } catch (e) {
        alert('Failed to send square-off command');
    }
});

// ============ Initial Load & Polling ============
fetchPortfolio();
fetchMetrics();
fetchScanner();
fetchSignals();

// Poll every 5 seconds
setInterval(fetchPortfolio, 5000);
setInterval(fetchMetrics, 10000);
setInterval(fetchScanner, 60000);

// ============ Export Button ============
const exportBtn = document.getElementById('sim-btn');

if (exportBtn) {
    exportBtn.addEventListener('click', () => {
        window.location.href = `/api/export_trades`;
    });
}

// ============ Settings Modal ============
const settingsBtn = document.getElementById('settings-btn');
const settingsModal = document.getElementById('settings-modal');
const settingsCancel = document.getElementById('settings-cancel');
const settingsSave = document.getElementById('settings-save');
const settingsAlert = document.getElementById('settings-alert');

if (settingsBtn && settingsModal) {
    // Open modal and fetch config
    settingsBtn.addEventListener('click', async () => {
        try {
            const res = await fetch('/api/config');
            if (res.ok) {
                const config = await res.json();
                const trading = config.trading || {};
                document.getElementById('config-mode').value = trading.mode || 'paper';
                document.getElementById('config-capital').value = trading.capital || 20000;
                document.getElementById('config-max-trade').value = trading.max_per_trade || 4000;
                document.getElementById('config-max-loss').value = trading.max_daily_loss || 400;
                
                settingsAlert.style.display = 'none';
                settingsModal.style.display = 'flex';
            }
        } catch (e) {
            alert('Failed to load settings');
        }
    });

    // Handle mode change warning
    document.getElementById('config-mode').addEventListener('change', (e) => {
        settingsAlert.style.display = 'block';
    });

    // Close modal
    settingsCancel.addEventListener('click', () => {
        settingsModal.style.display = 'none';
    });

    // Save config
    settingsSave.addEventListener('click', async () => {
        const mode = document.getElementById('config-mode').value;
        const capital = parseFloat(document.getElementById('config-capital').value);
        const max_per_trade = parseFloat(document.getElementById('config-max-trade').value);
        const max_daily_loss = parseFloat(document.getElementById('config-max-loss').value);
        
        try {
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    mode,
                    capital,
                    max_per_trade,
                    max_daily_loss
                })
            });
            
            const data = await res.json();
            if (res.ok) {
                settingsModal.style.display = 'none';
                // Show brief success alert
                if (settingsAlert.style.display === 'block') {
                    alert('Settings saved! Please restart the bot using `sudo systemctl restart trader` for the trading mode change to take effect.');
                }
                // Update badge if mode changed visually (though restart is required)
                document.getElementById('mode-badge').textContent = mode.toUpperCase();
                document.getElementById('mode-badge').className = `badge badge-${mode}`;
            } else {
                alert('Error saving settings: ' + data.error);
            }
        } catch (e) {
            alert('Failed to save settings');
        }
    });
}
