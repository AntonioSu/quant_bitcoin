// 资产组合 + 系统状态栏渲染
function updatePortfolio(data) {
    setIfExists('btc-price', formatUSD(data.btc_price));
    setIfExists('total-usd', formatUSD(data.total_usd));
    setIfExists('total-btc', formatNumber(data.total_btc, 4));
    setIfExists('spot-btc', formatNumber(data.spot_btc, 4));
    setIfExists('futures-btc', formatNumber(data.futures_btc, 4));
    setIfExists('position-dir', data.position_direction || '--');
    setIfExists('unrealized-pnl', formatPnl(data.unrealized_pnl), 'portfolio-value ' + getColorClass(data.unrealized_pnl));
}

function updateStatus(data) {
    const badge = document.getElementById('mode-badge');
    badge.textContent = data.mode.toUpperCase();
    badge.className = 'status-badge status-' + data.mode;

    document.getElementById('uptime').textContent = data.uptime_hours.toFixed(1) + 'h';
    document.getElementById('total-trades').textContent = data.total_trades;

    const pnlEl = document.getElementById('total-pnl');
    pnlEl.textContent = formatPnl(data.total_pnl);
    pnlEl.className = getColorClass(data.total_pnl);
}
