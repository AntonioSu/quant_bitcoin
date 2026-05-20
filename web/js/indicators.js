// 信号指标面板渲染
function updateIndicators(data) {
    const fgEl = document.getElementById('fear-greed');
    fgEl.textContent = data.fear_greed;
    fgEl.className = 'indicator-value ' + (data.fear_greed <= 25 ? 'red' : data.fear_greed >= 75 ? 'green' : 'yellow');
    document.getElementById('fear-greed-class').textContent = data.fear_greed_class;

    const frEl = document.getElementById('funding-rate');
    frEl.textContent = data.funding_rate.toFixed(4) + '%';
    frEl.className = 'indicator-value ' + getColorClass(data.funding_rate);
    document.getElementById('funding-annual').textContent = data.funding_rate_annual.toFixed(2) + '%';

    // Top Trader Ratio
    const ttEl = document.getElementById('top-trader-ratio');
    const ratio = data.top_trader_ratio || 1.0;
    ttEl.textContent = ratio.toFixed(4);

    // 颜色逻辑：>2.0 过度看多(红色)，<0.5 过度看空(绿色)，1.0附近中性(黄色)
    if (ratio > 2.0) {
        ttEl.className = 'indicator-value red';
    } else if (ratio < 0.5) {
        ttEl.className = 'indicator-value green';
    } else if (ratio > 1.5 || ratio < 0.67) {
        ttEl.className = 'indicator-value yellow';
    } else {
        ttEl.className = 'indicator-value';
    }

    document.getElementById('top-trader-sentiment').textContent = data.top_trader_sentiment || '--';

    // ATR (Average True Range)
    const atrEl = document.getElementById('atr-value');
    const atrValue = data.atr_value || 0;
    atrEl.textContent = atrValue > 0 ?  formatNumber(atrValue, 1)+ '$' : '--';
    atrEl.className = 'indicator-value';

    // Taker 买入占比
    const takerRatioEl = document.getElementById('taker-ratio');
    const takerRatio = data.taker_buy_ratio || 50;
    takerRatioEl.textContent = takerRatio.toFixed(1) + '%';
    // 颜色: >55% 偏多(绿), <45% 偏空(红), 中性(无色)
    if (takerRatio >= 55) {
        takerRatioEl.className = 'indicator-value green';
    } else if (takerRatio <= 45) {
        takerRatioEl.className = 'indicator-value red';
    } else {
        takerRatioEl.className = 'indicator-value';
    }

    // Taker 买入/卖出/总量
    const takerBuy = data.taker_buy_btc || 0;
    const takerSell = data.taker_sell_btc || 0;
    const takerTotal = data.taker_total_btc || 0;
    document.getElementById('taker-buy').textContent = formatNumber(takerBuy, 0);
    document.getElementById('taker-sell').textContent = formatNumber(takerSell, 0);
    document.getElementById('taker-total').textContent = formatNumber(takerTotal, 0);

    // Open Interest
    const oiEl = document.getElementById('oi-value');
    const oiUsd = data.oi_value_usd;
    if (oiUsd != null && oiUsd > 0) {
        oiEl.textContent = (oiUsd / 1e9).toFixed(2) + 'B';
        const oi24h = data.oi_change_24h || 0;
        oiEl.className = 'indicator-value ' + (oi24h > 2 ? 'green' : oi24h < -2 ? 'red' : '');

        const fmt = v => (v != null ? ((v >= 0 ? '+' : '') + v.toFixed(1) + '%') : '--');
        const span = (id, v) => {
            const el = document.getElementById(id);
            el.textContent = fmt(v);
            el.className = v > 0 ? 'green' : v < 0 ? 'red' : '';
        };
        span('oi-1h', data.oi_change_1h);
        span('oi-4h', data.oi_change_4h);
        span('oi-24h', data.oi_change_24h);
    } else {
        oiEl.textContent = '--';
        oiEl.className = 'indicator-value';
    }

    // Price change
    const priceChange = data.price_change_pct || 0;
    const pcEl = document.getElementById('price-change');
    pcEl.textContent = (priceChange >= 0 ? '+' : '') + priceChange.toFixed(2) + '%';
    pcEl.className = 'indicator-value ' + getColorClass(priceChange);

    const pcStatus = document.getElementById('price-change-status');
    if (Math.abs(priceChange) < 3) {
        pcStatus.textContent = '横盘/微跌';
        pcStatus.style.color = 'var(--text-secondary)';
    } else if (priceChange > 0) {
        pcStatus.textContent = '上涨';
        pcStatus.style.color = 'var(--green)';
    } else {
        pcStatus.textContent = '下跌';
        pcStatus.style.color = 'var(--red)';
    }

    // CVD change
    const cvdChange = data.cvd_change_pct || 0;
    const cvdEl = document.getElementById('cvd-change');
    cvdEl.textContent = (cvdChange >= 0 ? '+' : '') + cvdChange.toFixed(2) + '%';
    cvdEl.className = 'indicator-value ' + getColorClass(cvdChange);

    const cvdStatus = document.getElementById('cvd-change-status');
    if (cvdChange < -20) {
        cvdStatus.textContent = '断崖暴跌';
        cvdStatus.style.color = 'var(--red)';
    } else if (cvdChange > 20) {
        cvdStatus.textContent = '大幅上涨';
        cvdStatus.style.color = 'var(--green)';
    } else {
        cvdStatus.textContent = '正常波动';
        cvdStatus.style.color = 'var(--text-secondary)';
    }

    // MACD signal
    const macdEl = document.getElementById('macd-signal');
    const macdSubEl = document.getElementById('macd-sub');
    const macdType = data.macd_signal_type || 'none';
    const macdAboveZero = data.macd_above_zero || false;
    const macdRising = data.macd_histogram_rising || false;
    const macdStrength = data.macd_strength || 0;
    if (macdType === 'bullish_cross') {
        macdEl.textContent = '金叉';
        macdEl.className = 'indicator-value green';
        macdEl.style.color = '';
    } else if (macdType === 'bearish_cross') {
        macdEl.textContent = '死叉';
        macdEl.className = 'indicator-value red';
        macdEl.style.color = '';
    } else {
        macdEl.textContent = '无交叉';
        macdEl.className = 'indicator-value';
        macdEl.style.color = 'var(--text-secondary)';
    }
    const zoneText = macdAboveZero ? '零轴上' : '零轴下';
    const histText = macdRising ? '动能↑' : '动能↓';
    const strengthText = macdType === 'none' ? '' : ` 强度${macdStrength.toFixed(2)}`;
    macdSubEl.textContent = `${zoneText} / ${histText}${strengthText}`;

    // RSI signal
    const rsiEl = document.getElementById('rsi-signal');
    const rsiSubEl = document.getElementById('rsi-sub');
    const rsiType = data.rsi_signal_type || 'none';
    const rsiValue = data.rsi_value || 50;
    const rsiTrend = data.rsi_trend_strength || 'neutral';
    const rsiStrength = data.rsi_strength || 0;
    if (rsiType === 'overbought') {
        rsiEl.textContent = `超买 ${rsiValue.toFixed(1)}`;
        rsiEl.className = 'indicator-value red';
        rsiEl.style.color = '';
    } else if (rsiType === 'oversold') {
        rsiEl.textContent = `超卖 ${rsiValue.toFixed(1)}`;
        rsiEl.className = 'indicator-value green';
        rsiEl.style.color = '';
    } else if (rsiType === 'bullish_divergence') {
        rsiEl.textContent = `看涨背离 ${rsiValue.toFixed(1)}`;
        rsiEl.className = 'indicator-value green';
        rsiEl.style.color = '';
    } else if (rsiType === 'bearish_divergence') {
        rsiEl.textContent = `看跌背离 ${rsiValue.toFixed(1)}`;
        rsiEl.className = 'indicator-value red';
        rsiEl.style.color = '';
    } else {
        rsiEl.textContent = rsiValue.toFixed(1);
        rsiEl.className = 'indicator-value';
        rsiEl.style.color = rsiValue > 55 ? 'var(--green)' : rsiValue < 45 ? 'var(--red)' : 'var(--text-secondary)';
    }
    const trendLabels = { strong_bull: '强势', bull: '偏多', neutral: '中性', bear: '偏空', strong_bear: '弱势' };
    const rsiStrengthText = rsiType === 'none' ? '' : ` 强度${rsiStrength.toFixed(2)}`;
    rsiSubEl.textContent = `${trendLabels[rsiTrend] || '中性'} / ${data.rsi_above_center ? '50上方' : '50下方'}${rsiStrengthText}`;

    // Bollinger Bands signal
    const bbEl = document.getElementById('bollinger-signal');
    const bbSubEl = document.getElementById('bollinger-sub');
    const bbType = data.bollinger_signal_type || 'none';
    const bbPctB = data.bollinger_percent_b != null ? data.bollinger_percent_b : 0.5;
    const bbBandwidth = data.bollinger_bandwidth || 0;
    const bbSqueeze = data.bollinger_is_squeeze || false;
    const bbStrength = data.bollinger_strength || 0;
    const bbLabels = {
        touch_lower: '触下轨',
        touch_upper: '触上轨',
        breakout_up: '突破上轨',
        breakout_down: '跌破下轨',
        squeeze_up: '收窄突破↑',
        squeeze_down: '收窄突破↓',
    };
    const bbBullish = ['touch_lower', 'breakout_up', 'squeeze_up'];
    const bbBearish = ['touch_upper', 'breakout_down', 'squeeze_down'];
    if (bbLabels[bbType]) {
        bbEl.textContent = bbLabels[bbType];
        bbEl.className = 'indicator-value ' + (bbBullish.includes(bbType) ? 'green' : 'red');
        bbEl.style.color = '';
    } else {
        bbEl.textContent = `%B ${bbPctB.toFixed(2)}`;
        bbEl.className = 'indicator-value';
        bbEl.style.color = bbPctB > 0.8 ? 'var(--red)' : bbPctB < 0.2 ? 'var(--green)' : 'var(--text-secondary)';
    }
    const squeezeText = bbSqueeze ? '收窄中' : `带宽${(bbBandwidth * 100).toFixed(1)}%`;
    const bbStrengthText = bbType === 'none' ? '' : ` 强度${bbStrength.toFixed(2)}`;
    bbSubEl.textContent = `${squeezeText}${bbStrengthText}`;

    // MA signal
    const maEl = document.getElementById('ma-signal');
    const maSubEl = document.getElementById('ma-sub');
    const maType = data.ma_signal_type || 'none';
    const maTrend = data.ma_trend || 'neutral';
    const maDeviation = data.ma_price_deviation || 0;
    const maStrength = data.ma_strength || 0;
    const maLabels = {
        golden_cross: '金叉',
        death_cross: '死叉',
        bullish_alignment: '多头排列',
        bearish_alignment: '空头排列',
    };
    const maBullish = ['golden_cross', 'bullish_alignment'];
    if (maLabels[maType]) {
        maEl.textContent = maLabels[maType];
        maEl.className = 'indicator-value ' + (maBullish.includes(maType) ? 'green' : 'red');
        maEl.style.color = '';
    } else {
        const trendMap = { bullish: '多头', bearish: '空头', neutral: '中性' };
        maEl.textContent = trendMap[maTrend] || '中性';
        maEl.className = 'indicator-value';
        maEl.style.color = maTrend === 'bullish' ? 'var(--green)' : maTrend === 'bearish' ? 'var(--red)' : 'var(--text-secondary)';
    }
    const devText = `偏离${(maDeviation * 100).toFixed(1)}%`;
    const maStrText = maType === 'none' ? '' : ` 强度${maStrength.toFixed(2)}`;
    maSubEl.textContent = `${devText}${maStrText}`;

    // Volume signal
    const volEl = document.getElementById('volume-signal');
    const volSubEl = document.getElementById('volume-sub');
    const volType = data.volume_signal_type || 'none';
    const volRatio = data.volume_ratio || 1.0;
    const obvTrend = data.volume_obv_trend || 'flat';
    const volStrength = data.volume_strength || 0;
    const volLabels = {
        surge_up: '放量上涨',
        surge_down: '放量下跌',
        dry_up: '缩量',
        divergence_top: '量价顶背离',
        divergence_bottom: '量价底背离',
    };
    const volBullish = ['surge_up', 'divergence_bottom'];
    const volBearish = ['surge_down', 'divergence_top'];
    if (volLabels[volType]) {
        volEl.textContent = volLabels[volType];
        volEl.className = 'indicator-value ' + (volBullish.includes(volType) ? 'green' : volBearish.includes(volType) ? 'red' : 'yellow');
        volEl.style.color = '';
    } else {
        volEl.textContent = `${volRatio.toFixed(1)}x`;
        volEl.className = 'indicator-value';
        volEl.style.color = volRatio > 1.5 ? 'var(--green)' : volRatio < 0.5 ? 'var(--red)' : 'var(--text-secondary)';
    }
    const obvMap = { up: 'OBV↑', down: 'OBV↓', flat: 'OBV→' };
    const volStrText = volType === 'none' ? '' : ` 强度${volStrength.toFixed(2)}`;
    volSubEl.textContent = `${obvMap[obvTrend] || 'OBV→'} / 量比${volRatio.toFixed(1)}x${volStrText}`;

    // Divergence status
    const divEl = document.getElementById('divergence-status');
    const hasBullish = data.has_bullish_divergence || false;
    const hasBearish = data.has_bearish_divergence || false;
    if (hasBullish) {
        divEl.textContent = '底背离';
        divEl.className = 'indicator-value green';
        divEl.style.color = '';
    } else if (hasBearish) {
        divEl.textContent = '顶背离';
        divEl.className = 'indicator-value red';
        divEl.style.color = '';
    } else {
        divEl.textContent = '无背离';
        divEl.className = 'indicator-value';
        divEl.style.color = 'var(--text-secondary)';
    }

    // ETF Flow
    const etfDailyEl = document.getElementById('etf-daily-flow');
    const etfDaily = data.etf_daily_flow;
    if (etfDaily !== null && etfDaily !== undefined) {
        etfDailyEl.textContent = (etfDaily >= 0 ? '+' : '-') + (Math.abs(etfDaily) / 1e6).toFixed(1) + 'M';
        etfDailyEl.className = 'etf-val ' + (etfDaily >= 0 ? 'green' : 'red');
    }
    const etf3dEl = document.getElementById('etf-flow-3d');
    const etf3d = data.etf_flow_3d;
    if (etf3d !== null && etf3d !== undefined) {
        etf3dEl.textContent = (etf3d >= 0 ? '+' : '-') + (Math.abs(etf3d) / 1e6).toFixed(0) + 'M';
        etf3dEl.className = 'etf-val ' + (etf3d >= 0 ? 'green' : 'red');
    }

    const etf7dEl = document.getElementById('etf-flow-7d');
    const etf7d = data.etf_flow_7d;
    if (etf7d !== null && etf7d !== undefined) {
        etf7dEl.textContent = (etf7d >= 0 ? '+' : '-') + (Math.abs(etf7d) / 1e6).toFixed(0) + 'M';
        etf7dEl.className = 'etf-val ' + (etf7d >= 0 ? 'green' : 'red');
    }

    const etfCumEl = document.getElementById('etf-cum-flow');
    const etfCum = data.etf_cum_flow;
    if (etfCum !== null && etfCum !== undefined) {
        etfCumEl.textContent = (etfCum / 1e9).toFixed(1) + 'B';
    }

    const etfAssetsEl = document.getElementById('etf-total-assets');
    const etfAssets = data.etf_total_assets;
    if (etfAssets !== null && etfAssets !== undefined) {
        etfAssetsEl.textContent = (etfAssets / 1e9).toFixed(1) + 'B';
    }

    // Exchange Netflow
    const nfEl = document.getElementById('netflow-value');
    const nfBtc = data.netflow_btc;
    if (nfBtc !== null && nfBtc !== undefined) {
        const nfAbs = Math.abs(nfBtc);
        const nfText = (nfBtc >= 0 ? '+' : '-') + (nfAbs >= 1000 ? (nfAbs / 1000).toFixed(1) + 'K' : nfAbs.toFixed(0));
        nfEl.textContent = nfText + ' BTC';
        nfEl.className = 'indicator-value ' + (nfBtc > 2000 ? 'red' : nfBtc < -2000 ? 'green' : '');
    }
    const nfSigEl = document.getElementById('netflow-signal');
    const nfSigMap = {
        strong_sell_pressure: '强卖压',
        moderate_sell_pressure: '中等卖压',
        strong_accumulation: '强囤币',
        moderate_accumulation: '囤币中',
        neutral: '中性',
    };
    nfSigEl.textContent = nfSigMap[data.netflow_signal] || data.netflow_signal || '--';

    // Options P/C + Max Pain
    const optPcEl = document.getElementById('options-pc');
    const pcRatio = data.options_pc_ratio;
    if (pcRatio !== null && pcRatio !== undefined) {
        optPcEl.textContent = pcRatio.toFixed(3);
        optPcEl.className = 'indicator-value ' + (pcRatio > 1.0 ? 'red' : pcRatio < 0.7 ? 'green' : '');
    }
    const optSubEl = document.getElementById('options-sub');
    const maxPain = data.options_max_pain;
    const priceVsMp = data.options_price_vs_mp;
    if (maxPain) {
        const mpText = 'MP:$' + (maxPain / 1000).toFixed(0) + 'K';
        const vsText = priceVsMp != null ? ` ${priceVsMp >= 0 ? '+' : ''}${priceVsMp.toFixed(1)}%` : '';
        optSubEl.textContent = mpText + vsText;
    }

    // Stablecoin
    const scEl = document.getElementById('stablecoin-value');
    const scSupply = data.stablecoin_supply_b;
    if (scSupply !== null && scSupply !== undefined) {
        scEl.textContent = '$' + scSupply.toFixed(0) + 'B';
        scEl.className = 'indicator-value';
    }
    const scSubEl = document.getElementById('stablecoin-sub');
    const sc7d = data.stablecoin_change_7d;
    if (sc7d !== null && sc7d !== undefined) {
        scSubEl.textContent = `7d: ${sc7d >= 0 ? '+' : ''}${sc7d.toFixed(2)}%`;
        scSubEl.className = 'indicator-sub ' + (sc7d > 0.3 ? 'green' : sc7d < -0.3 ? 'red' : '');
    }

    // MVRV
    const mvrvEl = document.getElementById('mvrv-value');
    const mvrvVal = data.mvrv_value;
    if (mvrvVal !== null && mvrvVal !== undefined) {
        mvrvEl.textContent = mvrvVal.toFixed(3);
        mvrvEl.className = 'indicator-value ' + (mvrvVal > 2.5 ? 'red' : mvrvVal < 1.0 ? 'green' : '');
    }
    const mvrvSubEl = document.getElementById('mvrv-sub');
    const zoneMap = {
        extreme_top: '极度高估', overvalued: '高估', fair_high: '正常偏高',
        fair_low: '正常偏低', undervalued: '低估', extreme_bottom: '极度低估',
    };
    mvrvSubEl.textContent = zoneMap[data.mvrv_zone] || data.mvrv_zone || '--';

    // DXY
    const dxyEl = document.getElementById('dxy-value');
    const dxyVal = data.macro_dxy;
    if (dxyVal !== null && dxyVal !== undefined) {
        dxyEl.textContent = dxyVal.toFixed(1);
        dxyEl.className = 'indicator-value ' + (data.macro_dxy_trend === 'strengthening' ? 'red' : 'green');
    }
    const dxySubEl = document.getElementById('dxy-sub');
    dxySubEl.textContent = data.macro_dxy_trend === 'strengthening' ? '走强 (BTC承压)' : '走弱 (BTC利好)';

    // M2
    const m2El = document.getElementById('m2-value');
    const m2Change = data.macro_m2_change;
    if (m2Change !== null && m2Change !== undefined) {
        m2El.textContent = (m2Change >= 0 ? '+' : '') + m2Change.toFixed(2) + '%';
        m2El.className = 'indicator-value ' + (m2Change > 0 ? 'green' : 'red');
    }
    const m2SubEl = document.getElementById('m2-sub');
    m2SubEl.textContent = m2Change > 0 ? '流动性扩张' : '流动性紧缩';

    // News sentiment
    const sentimentEl = document.getElementById('news-sentiment');
    const scoreEl = document.getElementById('news-score');
    const reasoningEl = document.getElementById('news-reasoning');
    const newsMetaEl = document.getElementById('news-updated-at');

    const sentiment = data.news_sentiment;
    const newsScore = data.news_score;

    if (sentiment && newsScore !== null && newsScore !== undefined) {
        const sentimentMap = { bullish: '看多', bearish: '看空', neutral: '中性' };
        sentimentEl.textContent = sentimentMap[sentiment] || sentiment;
        sentimentEl.className = 'news-sentiment ' + (sentiment || 'neutral');

        const sign = newsScore > 0 ? '+' : '';
        scoreEl.textContent = sign + Math.round(newsScore);
        scoreEl.className = 'news-score ' + (newsScore > 0 ? 'positive' : newsScore < 0 ? 'negative' : 'zero');

        // Render bullish/bearish factors
        const bullishList = document.getElementById('news-bullish-list');
        const bearishList = document.getElementById('news-bearish-list');
        const renderFactors = (list, factors) => {
            list.innerHTML = '';
            (factors || []).forEach(f => {
                const li = document.createElement('li');
                const text = typeof f === 'string' ? f : (f.factor || '');
                const url = typeof f === 'object' ? f.url : '';
                if (url) {
                    const a = document.createElement('a');
                    a.href = url;
                    a.target = '_blank';
                    a.textContent = text;
                    li.appendChild(a);
                } else {
                    li.textContent = text;
                }
                list.appendChild(li);
            });
        };
        renderFactors(bullishList, data.news_bullish_factors);
        renderFactors(bearishList, data.news_bearish_factors);

        reasoningEl.textContent = data.news_reasoning || '';

        if (data.news_updated_at) {
            const dt = new Date(data.news_updated_at);
            newsMetaEl.textContent = '更新: ' + dt.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        }
    }
}
