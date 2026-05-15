// 策略绩效 + 资金曲线渲染
function updatePerformance(d) {
    if (!d || !('total_trades' in d)) return;
    const el = id => document.getElementById(id);
    const safe = (v, fallback = 0) => (v != null && !isNaN(v)) ? v : fallback;

    const sharpe = safe(d.sharpe_ratio, null);
    const sharpeEl = el('perf-sharpe');
    sharpeEl.textContent = sharpe != null ? sharpe.toFixed(2) : '--';
    sharpeEl.className = 'perf-value ' + (sharpe > 1 ? 'green' : sharpe > 0 ? 'yellow' : sharpe != null ? 'red' : '');

    const retEl = el('perf-return');
    retEl.textContent = formatPnl(safe(d.total_pnl));
    retEl.className = 'perf-value ' + getColorClass(safe(d.total_pnl));
    const retPct = safe(d.total_return_pct);
    el('perf-return-pct').textContent = `${retPct >= 0 ? '+' : ''}${retPct}%`;

    const dd = safe(d.max_drawdown_pct);
    const ddEl = el('perf-drawdown');
    ddEl.textContent = dd > 0 ? `-${dd}%` : '0.00%';
    ddEl.className = 'perf-value ' + (dd > 5 ? 'red' : dd > 0 ? 'yellow' : 'green');

    const wr = safe(d.win_rate);
    const wrEl = el('perf-winrate');
    wrEl.textContent = `${wr}%`;
    wrEl.className = 'perf-value ' + (wr >= 50 ? 'green' : wr > 0 ? 'red' : '');
    el('perf-winrate-detail').textContent = `${safe(d.win_count)}W / ${safe(d.loss_count)}L`;

    const pf = safe(d.profit_factor, null);
    const pfEl = el('perf-pf');
    pfEl.textContent = pf != null ? pf.toFixed(2) : '∞';
    pfEl.className = 'perf-value ' + (pf == null || pf > 1.5 ? 'green' : pf > 1 ? 'yellow' : 'red');

    const avgEl = el('perf-avg-pnl');
    avgEl.textContent = formatPnl(safe(d.avg_pnl));
    avgEl.className = 'perf-value ' + getColorClass(safe(d.avg_pnl));

    const holdMin = safe(d.avg_hold_minutes);
    el('perf-hold').textContent = holdMin > 0
        ? (holdMin < 60 ? `${holdMin.toFixed(0)}m` : `${(holdMin / 60).toFixed(1)}h`)
        : '--';

    el('perf-count').textContent = safe(d.total_trades);

    const initEq = safe(d.initial_equity, 1000);
    const curEq = safe(d.current_equity, initEq);
    el('equity-range').textContent = `${formatUSD(initEq)} → ${formatUSD(curEq)}`;
    const retPctHeader = safe(d.total_return_pct);
    const retHeaderEl = el('equity-return');
    retHeaderEl.textContent = `${retPctHeader >= 0 ? '+' : ''}${retPctHeader.toFixed(2)}%`;
    retHeaderEl.className = 'equity-return ' + getColorClass(retPctHeader);

    if (equitySeries && returnSeries && d.equity_curve && d.equity_curve.length > 0) {
        const isUp = curEq >= initEq;
        equitySeries.applyOptions({
            topColor: isUp ? 'rgba(13, 148, 136, 0.2)' : 'rgba(220, 38, 38, 0.15)',
            bottomColor: isUp ? 'rgba(13, 148, 136, 0.0)' : 'rgba(220, 38, 38, 0.0)',
            lineColor: isUp ? THEME.teal : THEME.red,
        });

        const raw = [];
        for (const p of d.equity_curve) {
            const t = p.time
                ? Math.floor(new Date(p.time + '+08:00').getTime() / 1000)
                : Math.floor(Date.now() / 1000);
            raw.push({ time: t, equity: p.equity });
        }

        raw.sort((a, b) => a.time - b.time);

        const equityPoints = [];
        const returnPoints = [];
        for (const p of raw) {
            if (equityPoints.length > 0 && equityPoints[equityPoints.length - 1].time === p.time) {
                equityPoints[equityPoints.length - 1].value = p.equity;
                returnPoints[returnPoints.length - 1].value = ((p.equity - initEq) / initEq) * 100;
            } else {
                equityPoints.push({ time: p.time, value: p.equity });
                returnPoints.push({ time: p.time, value: ((p.equity - initEq) / initEq) * 100 });
            }
        }

        if (equityPoints.length > 0) {
            equitySeries.setData(equityPoints);
            returnSeries.setData(returnPoints);
            equityChart.timeScale().fitContent();
        }
    }
}
