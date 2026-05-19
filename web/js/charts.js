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
                if (!param.time) { etfChart.clearCrosshairPosition(); return; }
                etfChart.setCrosshairPosition(NaN, param.time, etfBarSeries);
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
    const volWrap = document.querySelector('.vol-wrap');
    if (etfWrap) etfWrap.style.display = isDaily ? '' : 'none';
    if (volWrap) volWrap.classList.toggle('no-bottom-radius', isDaily);

    if (volChart) volChart.applyOptions({ timeScale: { visible: !isDaily } });

    await loadKlines();
    if (chart) chart.timeScale().fitContent();
    if (macdChart) macdChart.timeScale().fitContent();
    if (volChart) volChart.timeScale().fitContent();

    if (isDaily) await loadEtfFlow();
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
