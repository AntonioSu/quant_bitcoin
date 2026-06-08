// K线图与权益曲线图初始化、K线数据加载
function initChart() {
    const container = document.getElementById('chart-container');

    chart = LightweightCharts.createChart(container, {
        layout: {
            background: { type: 'solid', color: THEME.bg },
            textColor: THEME.text,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11,
        },
        grid: {
            vertLines: { color: THEME.grid },
            horzLines: { color: THEME.grid },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
            horzLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
        },
        rightPriceScale: {
            borderColor: THEME.border,
            minimumWidth: 80,
        },
        timeScale: {
            borderColor: THEME.border,
            timeVisible: true,
            visible: false,
        },
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: THEME.green,
        downColor: THEME.red,
        borderVisible: true,
        borderUpColor: THEME.green,
        borderDownColor: THEME.red,
        wickUpColor: THEME.green,
        wickDownColor: THEME.red,
    });

    const overlayLine = (color) => chart.addLineSeries({
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
    });
    ma7Series = overlayLine(THEME.overlay1);
    ma25Series = overlayLine(THEME.overlay2);
    ma99Series = overlayLine(THEME.overlay3);
    bollUpperSeries = overlayLine(THEME.overlay1);
    bollMiddleSeries = overlayLine(THEME.overlay2);
    bollLowerSeries = overlayLine(THEME.overlay3);

    applyOverlay(activeOverlay);

    new ResizeObserver(() => {
        chart.applyOptions({ width: container.clientWidth });
    }).observe(container);
}

function applyOverlay(name) {
    activeOverlay = name;
    const showMA = name === 'ma';
    const showBoll = name === 'boll';
    if (ma7Series) ma7Series.applyOptions({ visible: showMA });
    if (ma25Series) ma25Series.applyOptions({ visible: showMA });
    if (ma99Series) ma99Series.applyOptions({ visible: showMA });
    if (bollUpperSeries) bollUpperSeries.applyOptions({ visible: showBoll });
    if (bollMiddleSeries) bollMiddleSeries.applyOptions({ visible: showBoll });
    if (bollLowerSeries) bollLowerSeries.applyOptions({ visible: showBoll });

    const maLegend = document.getElementById('kline-legend-ma');
    const bollLegend = document.getElementById('kline-legend-boll');
    if (maLegend) maLegend.style.display = showMA ? '' : 'none';
    if (bollLegend) bollLegend.style.display = showBoll ? '' : 'none';

    document.querySelectorAll('#kline-tabs .kline-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.overlay === name);
    });
}

function initMacdChart() {
    const container = document.getElementById('macd-container');
    if (!container) return;

    macdChart = LightweightCharts.createChart(container, {
        layout: {
            background: { type: 'solid', color: THEME.bg },
            textColor: THEME.text,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11,
        },
        grid: {
            vertLines: { color: THEME.grid },
            horzLines: { color: THEME.grid },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
            horzLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
        },
        rightPriceScale: {
            borderColor: THEME.border,
            minimumWidth: 80,
            scaleMargins: { top: 0.1, bottom: 0.1 },
        },
        timeScale: {
            borderColor: THEME.border,
            timeVisible: true,
            visible: false,
        },
    });

    macdHistSeries = macdChart.addHistogramSeries({
        priceFormat: { type: 'price', precision: 1, minMove: 0.1 },
        priceLineVisible: false,
        lastValueVisible: false,
    });

    macdDifSeries = macdChart.addLineSeries({
        color: THEME.macdDif,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
    });

    macdDeaSeries = macdChart.addLineSeries({
        color: THEME.macdDea,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
    });

    macdChart.priceScale('right').applyOptions({
        autoScale: true,
    });

    new ResizeObserver(() => {
        macdChart.applyOptions({ width: container.clientWidth });
    }).observe(container);
}

function initVolChart() {
    const container = document.getElementById('vol-container');
    if (!container) return;

    volChart = LightweightCharts.createChart(container, {
        layout: {
            background: { type: 'solid', color: THEME.bg },
            textColor: THEME.text,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11,
        },
        grid: {
            vertLines: { color: THEME.grid },
            horzLines: { color: THEME.grid },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
            horzLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
        },
        rightPriceScale: {
            borderColor: THEME.border,
            minimumWidth: 80,
            scaleMargins: { top: 0.2, bottom: 0.0 },
        },
        timeScale: {
            borderColor: THEME.border,
            timeVisible: true,
        },
    });

    volSeries = volChart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceLineVisible: false,
        lastValueVisible: false,
    });

    volMa5Series = volChart.addLineSeries({
        color: THEME.overlay1,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
    });

    volMa10Series = volChart.addLineSeries({
        color: THEME.overlay2,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
    });

    new ResizeObserver(() => {
        volChart.applyOptions({ width: container.clientWidth });
    }).observe(container);
}

function ensureEtfChart() {
    if (etfChart) return;
    const container = document.getElementById('etf-container');
    if (!container || container.clientWidth === 0) return;

    etfChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 110,
        layout: {
            background: { type: 'solid', color: THEME.bg },
            textColor: THEME.text,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11,
        },
        grid: {
            vertLines: { color: THEME.grid },
            horzLines: { color: THEME.grid },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
            horzLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
        },
        rightPriceScale: {
            borderColor: THEME.border,
            minimumWidth: 80,
            scaleMargins: { top: 0.1, bottom: 0.1 },
        },
        timeScale: {
            borderColor: THEME.border,
            timeVisible: false,
            visible: true,
        },
    });

    etfBarSeries = etfChart.addHistogramSeries({
        priceFormat: {
            type: 'custom',
            formatter: v => {
                const abs = Math.abs(v);
                if (abs >= 1e9) return (v / 1e9).toFixed(1) + 'B';
                if (abs >= 1e6) return (v / 1e6).toFixed(0) + 'M';
                if (abs >= 1e3) return (v / 1e3).toFixed(0) + 'K';
                return v.toFixed(0);
            },
        },
        priceLineVisible: false,
        lastValueVisible: false,
    });

    new ResizeObserver(() => {
        if (container.clientWidth > 0) {
            etfChart.applyOptions({ width: container.clientWidth });
        }
    }).observe(container);

    // 十字线与主图联动
    if (chart) {
        const mainSeries = candleSeries;
        etfChart.subscribeCrosshairMove(param => {
            if (!param.time) { chart.clearCrosshairPosition(); return; }
            if (mainSeries) chart.setCrosshairPosition(NaN, param.time, mainSeries);
        });
        chart.subscribeCrosshairMove(param => {
            if (!param.time) { etfChart.clearCrosshairPosition(); return; }
            if (etfBarSeries) etfChart.setCrosshairPosition(NaN, param.time, etfBarSeries);
        });
    }
}

function ensureExchangeFlowChart() {
    if (exchangeFlowChart) return;
    const container = document.getElementById('exchange-flow-container');
    if (!container || container.clientWidth === 0) return;

    exchangeFlowChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 110,
        layout: {
            background: { type: 'solid', color: THEME.bg },
            textColor: THEME.text,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11,
        },
        grid: {
            vertLines: { color: THEME.grid },
            horzLines: { color: THEME.grid },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
            horzLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
        },
        rightPriceScale: {
            borderColor: THEME.border,
            minimumWidth: 80,
            scaleMargins: { top: 0.1, bottom: 0.1 },
        },
        timeScale: {
            borderColor: THEME.border,
            timeVisible: false,
            visible: true,
        },
    });

    exchangeFlowBarSeries = exchangeFlowChart.addHistogramSeries({
        priceFormat: {
            type: 'custom',
            formatter: v => formatBtcFlow(v),
        },
        priceLineVisible: false,
        lastValueVisible: false,
    });

    new ResizeObserver(() => {
        if (container.clientWidth > 0) {
            exchangeFlowChart.applyOptions({ width: container.clientWidth });
        }
    }).observe(container);

    if (chart) {
        const mainSeries = candleSeries;
        exchangeFlowChart.subscribeCrosshairMove(param => {
            if (!param.time) { chart.clearCrosshairPosition(); return; }
            if (mainSeries) chart.setCrosshairPosition(NaN, param.time, mainSeries);
        });
        chart.subscribeCrosshairMove(param => {
            if (!param.time) { exchangeFlowChart.clearCrosshairPosition(); return; }
            if (exchangeFlowBarSeries) exchangeFlowChart.setCrosshairPosition(NaN, param.time, exchangeFlowBarSeries);
        });
    }
}

function formatBtcFlow(v) {
    if (v == null || isNaN(v)) return '--';
    const abs = Math.abs(v);
    if (abs >= 1000) return (v / 1000).toFixed(1) + 'K';
    return v.toFixed(0);
}

function updateExchangeFlowLegend(last) {
    const netEl = document.getElementById('exchange-flow-bar-val');
    const inEl = document.getElementById('exchange-flow-in-val');
    const outEl = document.getElementById('exchange-flow-out-val');
    if (!last) return;

    const netflow = last.netflow_btc || 0;
    if (netEl) {
        netEl.textContent = (netflow >= 0 ? '+' : '-') + formatBtcFlow(Math.abs(netflow)) + ' BTC';
        netEl.className = 'macd-legend-item ' + (netflow >= 0 ? 'red' : 'green');
    }
    if (inEl) inEl.textContent = formatBtcFlow(last.inflow_btc || 0);
    if (outEl) outEl.textContent = formatBtcFlow(last.outflow_btc || 0);
}

async function loadExchangeNetflow() {
    try {
        const resp = await fetch(`${API_BASE}/api/exchange-netflow?limit=0`);
        const data = await resp.json();
        if (!data || !data.length) return;

        ensureExchangeFlowChart();
        if (!exchangeFlowChart || !exchangeFlowBarSeries) return;

        const sorted = data.slice().reverse();
        const barData = sorted.map(d => {
            const parts = d.date.split('-');
            const ts = Date.UTC(+parts[0], +parts[1] - 1, +parts[2]) / 1000;
            const flow = d.netflow_btc || 0;
            return {
                time: ts,
                value: flow,
                color: flow >= 0 ? 'rgba(220, 38, 38, 0.75)' : 'rgba(22, 163, 74, 0.75)',
            };
        });
        exchangeFlowBarSeries.setData(barData);
        updateExchangeFlowLegend(sorted[sorted.length - 1]);

        if (chart) {
            const tr = chart.timeScale().getVisibleRange();
            if (tr) exchangeFlowChart.timeScale().setVisibleRange(tr);
        }
    } catch (e) {
        console.error('加载交易所净流入失败:', e);
    }
}

async function loadEtfFlow() {
    try {
        const resp = await fetch(`${API_BASE}/api/etf-flow?limit=0`);
        const data = await resp.json();
        if (!data || !data.length) return;

        ensureEtfChart();
        if (!etfChart || !etfBarSeries) return;

        const sorted = data.slice().reverse();
        const barData = sorted.map(d => {
            const parts = d.date.split('-');
            const ts = Date.UTC(+parts[0], +parts[1] - 1, +parts[2]) / 1000;
            const flow = d.daily_flow;
            return {
                time: ts,
                value: flow,
                color: flow >= 0 ? 'rgba(22, 163, 74, 0.75)' : 'rgba(220, 38, 38, 0.75)',
            };
        });
        etfBarSeries.setData(barData);

        const last = sorted[sorted.length - 1];
        const el = document.getElementById('etf-bar-val');
        if (el && last) {
            const v = last.daily_flow;
            const abs = Math.abs(v);
            let txt;
            if (abs >= 1e9) txt = (v / 1e9).toFixed(2) + 'B';
            else if (abs >= 1e6) txt = (v / 1e6).toFixed(1) + 'M';
            else txt = v.toFixed(0);
            el.textContent = (v >= 0 ? '+$' : '-$') + txt.replace('-', '');
            el.className = 'macd-legend-item ' + (v >= 0 ? 'green' : 'red');
        }

        // 对齐到 K线的可见时间范围
        if (chart) {
            const tr = chart.timeScale().getVisibleRange();
            if (tr) etfChart.timeScale().setVisibleRange(tr);
        }
    } catch (e) {
        console.error('加载ETF资金流失败:', e);
    }
}

function syncCharts() {
    const panes = [
        { chart: chart, anchor: () => candleSeries },
        { chart: macdChart, anchor: () => macdHistSeries },
        { chart: volChart, anchor: () => volSeries },
    ].filter(p => p.chart);
    if (panes.length < 2) return;

    let syncing = false;
    panes.forEach(src => {
        src.chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
            if (syncing || !range) return;
            syncing = true;
            try {
                panes.forEach(tgt => {
                    if (tgt.chart !== src.chart) {
                        tgt.chart.timeScale().setVisibleLogicalRange(range);
                    }
                });
                if (etfChart && chart) {
                    const tr = chart.timeScale().getVisibleRange();
                    if (tr) etfChart.timeScale().setVisibleRange(tr);
                }
                if (exchangeFlowChart && chart) {
                    const tr = chart.timeScale().getVisibleRange();
                    if (tr) exchangeFlowChart.timeScale().setVisibleRange(tr);
                }
            } finally {
                syncing = false;
            }
        });

        src.chart.subscribeCrosshairMove(param => {
            panes.forEach(tgt => {
                if (tgt.chart === src.chart) return;
                if (!param.time) { tgt.chart.clearCrosshairPosition(); return; }
                const series = tgt.anchor();
                if (series) tgt.chart.setCrosshairPosition(NaN, param.time, series);
            });
            if (etfChart && etfBarSeries) {
                if (!param.time) etfChart.clearCrosshairPosition();
                else etfChart.setCrosshairPosition(NaN, param.time, etfBarSeries);
            }
            if (exchangeFlowChart && exchangeFlowBarSeries) {
                if (!param.time) exchangeFlowChart.clearCrosshairPosition();
                else exchangeFlowChart.setCrosshairPosition(NaN, param.time, exchangeFlowBarSeries);
            }
        });
    });
}

function computeSMA(values, period) {
    const ma = new Array(values.length).fill(null);
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
        sum += values[i];
        if (i >= period) sum -= values[i - period];
        if (i >= period - 1) ma[i] = sum / period;
    }
    return ma;
}

function computeBollinger(values, period = 20, mult = 2) {
    const middle = computeSMA(values, period);
    const upper = new Array(values.length).fill(null);
    const lower = new Array(values.length).fill(null);
    for (let i = period - 1; i < values.length; i++) {
        let sumSq = 0;
        for (let j = i - period + 1; j <= i; j++) {
            const d = values[j] - middle[i];
            sumSq += d * d;
        }
        const std = Math.sqrt(sumSq / period);
        upper[i] = middle[i] + mult * std;
        lower[i] = middle[i] - mult * std;
    }
    return { upper, middle, lower };
}

function buildLineData(values, times) {
    const out = new Array(times.length);
    for (let i = 0; i < times.length; i++) {
        out[i] = values[i] != null ? { time: times[i], value: values[i] } : { time: times[i] };
    }
    return out;
}

function calcSupportResistanceLevels(klines, opts = {}) {
    const lookback = opts.lookback || 120;
    const pivotWindow = opts.pivotWindow || 2;
    const tolerancePct = opts.tolerancePct || 0.006;
    const rangeMultiplier = opts.rangeMultiplier || 0.45;
    const maxLevels = opts.maxLevels || 4;
    const recent = klines.slice(-lookback);
    if (recent.length < pivotWindow * 2 + 10) {
        return { supports: [], resistances: [] };
    }

    const currentPrice = Number(recent[recent.length - 1].close);
    const rangeSlice = recent.slice(-Math.min(recent.length, 14));
    const avgRange = rangeSlice.reduce((sum, k) => sum + (Number(k.high) - Number(k.low)), 0) / rangeSlice.length;
    const tolerance = Math.max(currentPrice * tolerancePct, avgRange * rangeMultiplier);
    const avgVolume = recent.reduce((sum, k) => sum + Number(k.volume || 0), 0) / recent.length || 1;

    const findPivots = kind => {
        const pivots = [];
        for (let i = pivotWindow; i < recent.length - pivotWindow; i++) {
            const window = recent.slice(i - pivotWindow, i + pivotWindow + 1);
            if (kind === 'support') {
                const price = Number(recent[i].low);
                if (price !== Math.min(...window.map(k => Number(k.low)))) continue;
                pivots.push({ price, index: i, volume: Number(recent[i].volume || 0) });
            } else {
                const price = Number(recent[i].high);
                if (price !== Math.max(...window.map(k => Number(k.high)))) continue;
                pivots.push({ price, index: i, volume: Number(recent[i].volume || 0) });
            }
        }
        return pivots;
    };

    const clusterPivots = pivots => {
        const clusters = [];
        pivots.slice().sort((a, b) => a.price - b.price).forEach(pivot => {
            const cluster = clusters.find(c => Math.abs(c.price - pivot.price) <= tolerance);
            if (!cluster) {
                clusters.push({
                    price: pivot.price,
                    touches: 1,
                    lastTouchIndex: pivot.index,
                    volume: pivot.volume,
                });
                return;
            }
            const touches = cluster.touches + 1;
            cluster.price = (cluster.price * cluster.touches + pivot.price) / touches;
            cluster.touches = touches;
            cluster.lastTouchIndex = Math.max(cluster.lastTouchIndex, pivot.index);
            cluster.volume += pivot.volume;
        });

        const lastPivotIndex = Math.max(...pivots.map(p => p.index), 1);
        return clusters.map(cluster => {
            const distancePct = (cluster.price - currentPrice) / currentPrice * 100;
            const touchScore = Math.min(cluster.touches / 4, 1) * 0.45;
            const recencyScore = (cluster.lastTouchIndex / lastPivotIndex) * 0.25;
            const volumeScore = Math.min((cluster.volume / cluster.touches) / avgVolume, 2) / 2 * 0.15;
            const distanceScore = Math.max(0, 1 - Math.abs(distancePct / 100) / 0.08) * 0.15;
            return {
                price: cluster.price,
                touches: cluster.touches,
                strength: Math.min(touchScore + recencyScore + volumeScore + distanceScore, 1),
                distancePct,
            };
        });
    };

    const supports = clusterPivots(findPivots('support'))
        .filter(level => level.price < currentPrice)
        .sort((a, b) => b.strength - a.strength || Math.abs(a.distancePct) - Math.abs(b.distancePct))
        .slice(0, maxLevels);
    const resistances = clusterPivots(findPivots('resistance'))
        .filter(level => level.price > currentPrice)
        .sort((a, b) => b.strength - a.strength || Math.abs(a.distancePct) - Math.abs(b.distancePct))
        .slice(0, maxLevels);

    return { supports, resistances };
}

function renderSupportResistanceLines(klines) {
    if (!candleSeries) return;
    supportResistancePriceLines.forEach(line => candleSeries.removePriceLine(line));
    supportResistancePriceLines = [];

    const { supports, resistances } = calcSupportResistanceLevels(klines);
    const addLine = (level, type) => {
        const isSupport = type === 'support';
        const color = isSupport ? 'rgba(22, 163, 74, 0.86)' : 'rgba(220, 38, 38, 0.86)';
        const label = `${isSupport ? 'S' : 'R'} ${formatNumber(level.price, 0)}`;
        const line = candleSeries.createPriceLine({
            price: level.price,
            color,
            lineWidth: level.strength >= 0.7 ? 2 : 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: label,
        });
        supportResistancePriceLines.push(line);
    };

    supports.forEach(level => addLine(level, 'support'));
    resistances.forEach(level => addLine(level, 'resistance'));
}

function computeEMA(values, period) {
    const k = 2 / (period + 1);
    const ema = new Array(values.length).fill(null);
    if (!values.length) return ema;
    ema[0] = values[0];
    for (let i = 1; i < values.length; i++) {
        ema[i] = values[i] * k + ema[i - 1] * (1 - k);
    }
    return ema;
}

function computeMACD(closes, fast = 12, slow = 26, signal = 9) {
    const emaFast = computeEMA(closes, fast);
    const emaSlow = computeEMA(closes, slow);
    const dif = closes.map((_, i) => emaFast[i] - emaSlow[i]);
    const dea = computeEMA(dif, signal);
    const hist = dif.map((d, i) => d - dea[i]);
    return { dif, dea, hist };
}

function updateLegendValue(id, v) {
    const el = document.getElementById(id);
    if (!el) return;
    if (v == null || isNaN(v)) {
        el.textContent = '--';
        return;
    }
    el.textContent = typeof formatNumber === 'function' ? formatNumber(v, 1) : v.toFixed(1);
}

function updateVolLegend(id, v) {
    const el = document.getElementById(id);
    if (!el) return;
    if (v == null || isNaN(v)) {
        el.textContent = '--';
        return;
    }
    const abs = Math.abs(v);
    if (abs >= 1e6) el.textContent = (v / 1e6).toFixed(2) + 'M';
    else if (abs >= 1e3) el.textContent = (v / 1e3).toFixed(2) + 'K';
    else el.textContent = v.toFixed(2);
}

function updateMacdLegend(dif, dea, hist) {
    const fmt = v => (v == null || isNaN(v)) ? '--' : (v >= 0 ? '+' : '') + v.toFixed(1);
    const setVal = (id, v) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = fmt(v);
        el.className = (v == null || isNaN(v)) ? '' : (v >= 0 ? 'green' : 'red');
    };
    setVal('macd-dif-val', dif);
    setVal('macd-dea-val', dea);
    setVal('macd-hist-val', hist);
}

function initEquityChart() {
    const container = document.getElementById('equity-chart-container');

    equityChart = LightweightCharts.createChart(container, {
        layout: {
            background: { type: 'solid', color: THEME.bg },
            textColor: THEME.text,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11,
        },
        grid: {
            vertLines: { color: THEME.grid },
            horzLines: { color: THEME.grid },
        },
        leftPriceScale: {
            visible: true,
            borderColor: THEME.border,
        },
        rightPriceScale: {
            visible: true,
            borderColor: THEME.border,
        },
        timeScale: {
            visible: true,
            borderColor: THEME.border,
            timeVisible: true,
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
            horzLine: { color: 'rgba(13, 148, 136, 0.25)', labelBackgroundColor: THEME.teal },
        },
    });

    equitySeries = equityChart.addAreaSeries({
        topColor: 'rgba(13, 148, 136, 0.2)',
        bottomColor: 'rgba(13, 148, 136, 0.0)',
        lineColor: THEME.teal,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        priceScaleId: 'left',
        title: '资金 $',
        priceFormat: { type: 'custom', formatter: v => '$' + v.toFixed(0) },
    });

    returnSeries = equityChart.addLineSeries({
        color: THEME.blue,
        lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: true,
        priceScaleId: 'right',
        title: '收益率 %',
        priceFormat: { type: 'custom', formatter: v => v.toFixed(2) + '%' },
    });

    new ResizeObserver(() => {
        equityChart.applyOptions({ width: container.clientWidth });
    }).observe(container);
}

async function applyInterval(interval) {
    if (!['1h', '4h', '1d'].includes(interval) || interval === activeInterval) {
        document.querySelectorAll('#kline-interval-tabs .kline-tab').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.interval === activeInterval);
        });
        return;
    }
    activeInterval = interval;
    document.querySelectorAll('#kline-interval-tabs .kline-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.interval === interval);
    });

    const isDaily = interval === '1d';
    const etfWrap = document.getElementById('etf-wrap');
    const exchangeFlowWrap = document.getElementById('exchange-flow-wrap');
    const volWrap = document.querySelector('.vol-wrap');
    if (etfWrap) etfWrap.style.display = isDaily ? '' : 'none';
    if (exchangeFlowWrap) {
        exchangeFlowWrap.style.display = isDaily ? '' : 'none';
        exchangeFlowWrap.classList.toggle('no-bottom-radius', isDaily);
    }
    if (volWrap) volWrap.classList.toggle('no-bottom-radius', isDaily);

    if (volChart) volChart.applyOptions({ timeScale: { visible: !isDaily } });

    await loadKlines();
    if (chart) chart.timeScale().fitContent();
    if (macdChart) macdChart.timeScale().fitContent();
    if (volChart) volChart.timeScale().fitContent();

    if (isDaily) {
        await Promise.all([loadExchangeNetflow(), loadEtfFlow()]);
    }
}

async function loadKlines() {
    try {
        const klineLimit = activeInterval === '1d' ? 120 : 500;
        const resp = await fetch(`${API_BASE}/api/klines?interval=${activeInterval}&limit=${klineLimit}`);
        const data = await resp.json();

        const formatted = data.map(k => ({
            time: k.time / 1000,
            open: k.open,
            high: k.high,
            low: k.low,
            close: k.close,
        }));

        candleSeries.setData(formatted);
        renderSupportResistanceLines(data);

        const closes = data.map(k => k.close);
        const times = data.map(k => k.time / 1000);

        // MA(7/25/99)
        if (ma7Series && ma25Series && ma99Series) {
            const ma7 = computeSMA(closes, 7);
            const ma25 = computeSMA(closes, 25);
            const ma99 = computeSMA(closes, 99);
            ma7Series.setData(buildLineData(ma7, times));
            ma25Series.setData(buildLineData(ma25, times));
            ma99Series.setData(buildLineData(ma99, times));
            const last = times.length - 1;
            updateLegendValue('ma7-val', ma7[last]);
            updateLegendValue('ma25-val', ma25[last]);
            updateLegendValue('ma99-val', ma99[last]);
        }

        // Bollinger Bands (20, 2)
        if (bollUpperSeries && bollMiddleSeries && bollLowerSeries) {
            const { upper, middle, lower } = computeBollinger(closes, 20, 2);
            bollUpperSeries.setData(buildLineData(upper, times));
            bollMiddleSeries.setData(buildLineData(middle, times));
            bollLowerSeries.setData(buildLineData(lower, times));
            const last = times.length - 1;
            updateLegendValue('boll-up-val', upper[last]);
            updateLegendValue('boll-mid-val', middle[last]);
            updateLegendValue('boll-dn-val', lower[last]);
        }

        // 成交量 + MA(5/10)
        if (volChart && volSeries) {
            const volumes = data.map(k => k.volume || 0);
            const volData = volumes.map((v, i) => ({
                time: times[i],
                value: v,
                color: data[i].close >= data[i].open
                    ? 'rgba(22, 163, 74, 0.55)'
                    : 'rgba(220, 38, 38, 0.55)',
            }));
            const volMa5 = computeSMA(volumes, 5);
            const volMa10 = computeSMA(volumes, 10);
            volSeries.setData(volData);
            volMa5Series.setData(buildLineData(volMa5, times));
            volMa10Series.setData(buildLineData(volMa10, times));
            const last = times.length - 1;
            updateVolLegend('vol-val', volumes[last]);
            updateVolLegend('vol-ma5-val', volMa5[last]);
            updateVolLegend('vol-ma10-val', volMa10[last]);
        }

        if (macdChart && macdHistSeries) {
            const { dif, dea, hist } = computeMACD(closes);

            // 前 25 根 EMA 尚未稳定，用 whitespace 数据点占位，保证两图 bar 索引完全对齐
            const skipFirst = 25;
            const difData = [];
            const deaData = [];
            const histData = [];
            for (let i = 0; i < times.length; i++) {
                if (i < skipFirst) {
                    difData.push({ time: times[i] });
                    deaData.push({ time: times[i] });
                    histData.push({ time: times[i] });
                    continue;
                }
                difData.push({ time: times[i], value: dif[i] });
                deaData.push({ time: times[i], value: dea[i] });
                const v = hist[i];
                const prev = i > 0 ? hist[i - 1] : 0;
                // 颜色：红柱(正)/绿柱(负)，淡色表示动能减弱
                const isRed = v >= 0;
                const fading = isRed ? v < prev : v > prev;
                const color = isRed
                    ? (fading ? 'rgba(220, 38, 38, 0.45)' : '#DC2626')
                    : (fading ? 'rgba(22, 163, 74, 0.45)' : '#16A34A');
                histData.push({ time: times[i], value: v, color });
            }
            macdDifSeries.setData(difData);
            macdDeaSeries.setData(deaData);
            macdHistSeries.setData(histData);

            const last = times.length - 1;
            updateMacdLegend(dif[last], dea[last], hist[last]);
        }
    } catch (e) {
        console.error('加载K线失败:', e);
    }
}
