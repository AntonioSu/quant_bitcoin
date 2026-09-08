// 数据拉取 + 预设切换 + WebSocket 推送
async function selectPreset(preset) {
    console.log('[selectPreset] 切换到预设:', preset);
    selectedPreset = preset;
    document.querySelectorAll('.config-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.preset === selectedPreset);
    });

    try {
        await fetch(`${API_BASE}/api/config/${preset}`, { method: 'POST' });

        await Promise.all([
            loadPortfolio(),
            loadStatus(),
            loadTrades(),
            loadPerformance(),
        ]);
        console.log('[selectPreset] 数据加载完成');
    } catch (e) {
        console.error('加载预设数据失败:', e);
    }
}

async function loadPortfolio() {
    try {
        const resp = await fetch(`${API_BASE}/api/portfolio?preset=${selectedPreset}`);
        updatePortfolio(await resp.json());
    } catch (e) {
        console.error('加载资产组合失败:', e);
    }
}

async function loadIndicators() {
    try {
        const resp = await fetch(`${API_BASE}/api/indicators`);
        const data = await resp.json();
        applyDashboardUpdate({ indicators: data });
    } catch (e) {
        console.error('加载指标失败:', e);
    }
}

async function loadStatus() {
    try {
        const resp = await fetch(`${API_BASE}/api/status?preset=${selectedPreset}`);
        updateStatus(await resp.json());
    } catch (e) {
        console.error('加载状态失败:', e);
    }
}

async function loadTrades() {
    try {
        const resp = await fetch(`${API_BASE}/api/trades?preset=${selectedPreset}&limit=50`);
        updateTrades(await resp.json());
    } catch (e) {
        console.error('加载交易记录失败:', e);
    }
}

async function loadPerformance() {
    try {
        const resp = await fetch(`${API_BASE}/api/performance?preset=${selectedPreset}`);
        updatePerformance(await resp.json());
    } catch (e) {
        console.error('加载绩效失败:', e);
    }
}

function applyDashboardUpdate(data) {
    if (data.indicators) {
        updateIndicators(data.indicators);
        updateAiAnalysis(data.indicators);
    }
    if (data.portfolio) {
        updatePortfolio(data.portfolio);
    }
    if (data.status) {
        updateStatus(data.status);
    }
    const el = document.getElementById('last-update');
    if (el) {
        el.textContent = new Date().toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false,
        });
    }
}

function connectWebSocket() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${window.location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WebSocket 已连接');
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'update') {
            applyDashboardUpdate(data);
        }
    };

    ws.onclose = () => {
        console.log('WebSocket 断开，5秒后重连...');
        setTimeout(connectWebSocket, 5000);
    };

    ws.onerror = (e) => {
        console.error('WebSocket 错误:', e);
    };
}
