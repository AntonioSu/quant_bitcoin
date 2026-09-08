// 全局常量与共享状态
const API_BASE = window.location.origin;

let chart = null;
let candleSeries = null;
let ma7Series = null;
let ma25Series = null;
let ma99Series = null;
let bollUpperSeries = null;
let bollMiddleSeries = null;
let bollLowerSeries = null;
let supportResistancePriceLines = [];
let activeOverlay = 'ma'; // 'ma' | 'boll' | 'none'
let activeInterval = '4h'; // '1h' | '4h' | '1d'
let macdChart = null;
let macdDifSeries = null;
let macdDeaSeries = null;
let macdHistSeries = null;
let volChart = null;
let volSeries = null;
let volMa5Series = null;
let volMa10Series = null;
let etfChart = null;
let etfBarSeries = null;
let exchangeFlowChart = null;
let exchangeFlowBarSeries = null;
let equityChart = null;
let equitySeries = null;
let returnSeries = null;
let ws = null;
let selectedPreset = 'standard';

const THEME = {
    bg: 'rgba(255, 255, 255, 0.01)',
    grid: 'rgba(139, 92, 246, 0.06)',
    border: 'rgba(139, 92, 246, 0.1)',
    text: '#6B5B7B',
    teal: '#8B5CF6',
    green: '#10B981',
    red: '#EF4444',
    blue: '#6366F1',
    macdDif: '#F59E0B',
    macdDea: '#A855F7',
    overlay1: '#F59E0B', // MA7 / BOLL UP
    overlay2: '#A855F7', // MA25 / BOLL MB
    overlay3: '#6366F1', // MA99 / BOLL DN
};
