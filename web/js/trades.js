// 交易记录列表渲染
function updateTrades(trades) {
    const container = document.getElementById('trades-list');

    if (!trades || trades.length === 0) {
        container.innerHTML = `<div style="text-align: center; padding: 40px; color: var(--text-secondary); font-family: var(--font-display);">暂无交易记录</div>`;
        return;
    }

    container.innerHTML = trades.slice().reverse().map(t => {
        const isClose = t.action === 'CLOSE' || t.action === 'TP1_HALF';
        const actionClass = isClose ? '' : 'red';
        const profitClass = t.pnl >= 0 ? 'trade-profit' : '';
        let priceHtml;
        if (isClose && t.entry_price) {
            priceHtml = `$${t.entry_price.toLocaleString()} <span class="arrow">→</span> $${t.price.toLocaleString()}`;
        } else {
            priceHtml = `@ $${t.price.toLocaleString()}`;
        }

        // 市场指标 + 止盈止损（合并到一个框）
        let tradeInfoHtml = '';
        if ((t.market_indicators || t.levels) && (t.action === 'LONG' || t.action === 'SHORT')) {
            const ind = t.market_indicators || {};
            const lvl = t.levels || {};
            tradeInfoHtml = `
                <div class="trade-info-box">
                    ${ind.fear_greed_index !== undefined ? `
                    <div class="indicator-row">
                        <span class="indicator-label">恐惧贪婪:</span>
                        <span class="indicator-value ${ind.fear_greed_index <= 25 ? 'red' : ind.fear_greed_index >= 75 ? 'green' : 'yellow'}">${ind.fear_greed_index}</span>
                    </div>` : ''}
                    ${ind.funding_rate !== undefined ? `
                    <div class="indicator-row">
                        <span class="indicator-label">资金费率:</span>
                        <span class="indicator-value">${ind.funding_rate.toFixed(5)}%</span>
                    </div>` : ''}
                    ${ind.long_short_ratio !== undefined ? `
                    <div class="indicator-row">
                        <span class="indicator-label">多空比:</span>
                        <span class="indicator-value">${ind.long_short_ratio.toFixed(2)}</span>
                    </div>` : ''}
                    ${ind.price_change_percent !== undefined ? `
                    <div class="indicator-row">
                        <span class="indicator-label">价格变化:</span>
                        <span class="indicator-value ${getColorClass(ind.price_change_percent)}">${ind.price_change_percent >= 0 ? '+' : ''}${ind.price_change_percent.toFixed(2)}%</span>
                    </div>` : ''}
                    ${ind.cvd_change_percent !== undefined ? `
                    <div class="indicator-row">
                        <span class="indicator-label">CVD变化:</span>
                        <span class="indicator-value ${getColorClass(ind.cvd_change_percent)}">${ind.cvd_change_percent >= 0 ? '+' : ''}${ind.cvd_change_percent.toFixed(2)}%</span>
                    </div>` : ''}
                    ${ind.divergence_type !== undefined ? `
                    <div class="indicator-row">
                        <span class="indicator-label">背离:</span>
                        <span class="indicator-value ${ind.divergence_type === '底背离' ? 'green' : ind.divergence_type === '顶背离' ? 'red' : ''}">${ind.divergence_type}</span>
                    </div>` : ''}
                    ${lvl.stop_loss ? `
                    <div class="indicator-row levels-row">
                        <span class="indicator-label">止损:</span>
                        <span class="indicator-value red">$${lvl.stop_loss.toLocaleString()}</span>
                    </div>` : ''}
                    ${lvl.tp1_price ? `
                    <div class="indicator-row levels-row">
                        <span class="indicator-label">TP1:</span>
                        <span class="indicator-value green">$${lvl.tp1_price.toLocaleString()}</span>
                    </div>` : ''}
                    ${lvl.tp2_price ? `
                    <div class="indicator-row levels-row">
                        <span class="indicator-label">TP2:</span>
                        <span class="indicator-value green">$${lvl.tp2_price.toLocaleString()}</span>
                    </div>` : ''}
                    ${lvl.liquidation_price ? `
                    <div class="indicator-row levels-row">
                        <span class="indicator-label">强平:</span>
                        <span class="indicator-value yellow">$${lvl.liquidation_price.toLocaleString()}</span>
                    </div>` : ''}
                    ${lvl.atr ? `
                    <div class="indicator-row levels-row">
                        <span class="indicator-label">ATR:</span>
                        <span class="indicator-value">$${lvl.atr.toLocaleString()}</span>
                    </div>` : ''}
                </div>
            `;
        }

        return `
        <div class="trade-item ${profitClass}">
            <div class="trade-main">
                <div class="trade-left">
                    <div class="trade-action ${actionClass}">${t.mode} - ${t.action}</div>
                    <div class="trade-price">${priceHtml}</div>
                    <div class="trade-time">${t.time}</div>
                </div>
                <div class="trade-right">
                    <div class="trade-pnl ${getColorClass(t.pnl)}">${formatPnl(t.pnl)}</div>
                    <div class="trade-amount">${t.amount} BTC</div>
                </div>
            </div>
            ${tradeInfoHtml}
        </div>`;
    }).join('');
}
