// ── 交易指标渲染辅助 ──

function _indCell(label, value, colorClass) {
    if (value === undefined || value === null) return '';
    return `<div class="indicator-row">
        <span class="indicator-label">${label}</span>
        <span class="indicator-value ${colorClass || ''}">${value}</span>
    </div>`;
}

function _fmtPct(v) {
    if (v === undefined || v === null) return undefined;
    return `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
}

function _signalColor(sig) {
    if (!sig) return '';
    if (/golden|bullish|surge_up|bottom/.test(sig)) return 'green';
    if (/death|bearish|surge_down|top|overbought/.test(sig)) return 'red';
    if (/squeeze|oversold/.test(sig)) return 'yellow';
    return '';
}

function _signalLabel(sig) {
    const map = {
        golden_cross: '金叉', death_cross: '死叉', none: '-',
        overbought: '超买', oversold: '超卖',
        bullish_divergence: '底背离', bearish_divergence: '顶背离',
        breakout_upper: '突破上轨', breakout_lower: '突破下轨', squeeze: '收窄',
        bullish_alignment: '多头排列', bearish_alignment: '空头排列',
        surge_up: '放量上涨', surge_down: '放量下跌', dry_up: '缩量',
        divergence_top: '量价顶背离', divergence_bottom: '量价底背离',
    };
    return map[sig] || sig || '-';
}

function _buildIndicatorRows(ind) {
    if (!ind) return '';
    const rows = [];

    // 兼容新旧字段名
    const pricePct = ind.price_change_pct ?? ind.price_change_percent;
    const cvdPct = ind.cvd_change_pct ?? ind.cvd_change_percent;

    // 情绪 / 资金面
    rows.push(_indCell('恐贪', ind.fear_greed_index,
        ind.fear_greed_index <= 25 ? 'red' : ind.fear_greed_index >= 75 ? 'green' : 'yellow'));
    rows.push(_indCell('费率', ind.funding_rate !== undefined ? `${ind.funding_rate.toFixed(5)}%` : undefined, ''));
    rows.push(_indCell('多空比', ind.long_short_ratio !== undefined ? ind.long_short_ratio.toFixed(2) : undefined, ''));
    rows.push(_indCell('价格变化', _fmtPct(pricePct), getColorClass(pricePct)));
    rows.push(_indCell('CVD变化', _fmtPct(cvdPct), getColorClass(cvdPct)));
    rows.push(_indCell('背离', ind.divergence_type,
        ind.divergence_type === '底背离' ? 'green' : ind.divergence_type === '顶背离' ? 'red' : ''));

    // 技术指标
    if (ind.macd_signal !== undefined) {
        rows.push(_indCell('MACD', _signalLabel(ind.macd_signal), _signalColor(ind.macd_signal)));
    }
    if (ind.rsi_value !== undefined) {
        const rsiColor = ind.rsi_value >= 70 ? 'red' : ind.rsi_value <= 30 ? 'green' : '';
        rows.push(_indCell('RSI', ind.rsi_value.toFixed(1), rsiColor));
    }
    if (ind.boll_signal !== undefined) {
        rows.push(_indCell('布林', _signalLabel(ind.boll_signal), _signalColor(ind.boll_signal)));
    }
    if (ind.ma_signal !== undefined) {
        rows.push(_indCell('均线', _signalLabel(ind.ma_signal), _signalColor(ind.ma_signal)));
    }
    if (ind.vol_signal !== undefined) {
        rows.push(_indCell('成交量', _signalLabel(ind.vol_signal), _signalColor(ind.vol_signal)));
    }
    if (ind.taker_buy_ratio !== undefined) {
        const tbr = ind.taker_buy_ratio;
        rows.push(_indCell('主买占比', (tbr * 100).toFixed(1) + '%', tbr > 0.55 ? 'green' : tbr < 0.45 ? 'red' : ''));
    }

    // ETF / 持仓量 / 爆仓
    if (ind.etf_daily_flow_usd !== undefined && ind.etf_daily_flow_usd !== null) {
        const etfM = (ind.etf_daily_flow_usd / 1e6).toFixed(1);
        rows.push(_indCell('ETF流入', `${etfM}M`, ind.etf_daily_flow_usd > 0 ? 'green' : ind.etf_daily_flow_usd < 0 ? 'red' : ''));
    }
    if (ind.oi_change_4h_pct !== undefined && ind.oi_change_4h_pct !== null) {
        rows.push(_indCell('OI 4h', _fmtPct(ind.oi_change_4h_pct), getColorClass(ind.oi_change_4h_pct)));
    }
    if (ind.news_score !== undefined && ind.news_score !== null) {
        rows.push(_indCell('新闻', `${ind.news_score}`, ind.news_score > 20 ? 'green' : ind.news_score < -20 ? 'red' : ''));
    }

    // AI 综合研判
    if (ind.ai_bias) {
        const aiColor = ind.ai_bias === 'LONG' ? 'green' : ind.ai_bias === 'SHORT' ? 'red' : 'yellow';
        const aiText = `${ind.ai_bias}${ind.ai_confidence ? ' ' + ind.ai_confidence + '%' : ''}`;
        rows.push(_indCell('AI研判', aiText, aiColor));
    }

    return rows.join('');
}

function _buildLevelRows(lvl) {
    if (!lvl) return '';
    const rows = [];
    if (lvl.stop_loss) rows.push(_indCell('止损', `$${lvl.stop_loss.toLocaleString()}`, 'red'));
    if (lvl.tp1_price) rows.push(_indCell('TP1', `$${lvl.tp1_price.toLocaleString()}`, 'green'));
    if (lvl.tp2_price) rows.push(_indCell('TP2', `$${lvl.tp2_price.toLocaleString()}`, 'green'));
    if (lvl.liquidation_price) rows.push(_indCell('强平', `$${lvl.liquidation_price.toLocaleString()}`, 'yellow'));
    if (lvl.atr) rows.push(_indCell('ATR', `$${lvl.atr.toLocaleString()}`, ''));
    return rows.join('');
}


// ── 交易记录列表渲染 ──

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

        let tradeInfoHtml = '';
        const hasIndicators = t.market_indicators && Object.keys(t.market_indicators).length > 0;
        const hasLevels = t.levels && Object.keys(t.levels).length > 0;

        if (hasIndicators || hasLevels) {
            const indicatorRows = _buildIndicatorRows(t.market_indicators);
            const levelRows = _buildLevelRows(t.levels);

            // 平仓原因 / 触发原因
            const reason = t.trigger_reason || '';
            const reasonRow = reason ? `
                <div class="indicator-row trigger-reason">
                    <span class="indicator-label">${isClose ? '平仓原因:' : '触发:'}</span>
                    <span>${reason}</span>
                </div>` : '';

            // AI 一句话研判（开仓时完整展示）
            const aiSummary = t.market_indicators?.ai_summary;
            const aiRow = aiSummary ? `
                <div class="indicator-row trigger-reason">
                    <span class="indicator-label">AI:</span>
                    <span>${aiSummary}</span>
                </div>` : '';

            tradeInfoHtml = `
                <div class="trade-info-box">
                    ${indicatorRows}${levelRows}${reasonRow}${aiRow}
                </div>`;
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
