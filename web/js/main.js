// 入口：事件绑定 + 初始化
document.querySelectorAll('.config-btn').forEach(btn => {
    btn.addEventListener('click', () => selectPreset(btn.dataset.preset));
});

document.querySelectorAll('#kline-tabs .kline-tab').forEach(btn => {
    btn.addEventListener('click', () => applyOverlay(btn.dataset.overlay));
});

document.querySelectorAll('#kline-interval-tabs .kline-tab').forEach(btn => {
    btn.addEventListener('click', () => applyInterval(btn.dataset.interval));
});

async function init() {
    // 从 HTML 中读取默认激活的预设
    const activeBtn = document.querySelector('.config-btn.active');
    if (activeBtn) {
        selectedPreset = activeBtn.dataset.preset;
        console.log('[init] 初始预设:', selectedPreset);
    }

    initChart();
    initMacdChart();
    initVolChart();
    syncCharts();
    initEquityChart();
    initAiRefreshButton();

    await Promise.all([
        loadKlines(),
        loadPortfolio(),
        loadIndicators(),
        loadStatus(),
        loadTrades(),
        loadPerformance(),
    ]);

    connectWebSocket();

    setInterval(() => {
        loadKlines();
        if (activeInterval === '1d') {
            loadExchangeNetflow();
            loadEtfFlow();
        }
    }, 60000);
    setInterval(loadPortfolio, 10000);
    setInterval(loadIndicators, 10000);
    setInterval(loadStatus, 10000);
    setInterval(loadTrades, 30000);
    setInterval(loadPerformance, 30000);
}

init();
