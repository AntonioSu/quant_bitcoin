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
let equityChart = null;
let equitySeries = null;
let returnSeries = null;
let ws = null;
let selectedPreset = 'standard';

const THEME = {
    bg: '#FFFFFF',
    grid: '#F0F1F5',
    border: '#E2E4EB',
    text: '#6B7280',
    teal: '#0D9488',
    green: '#16A34A',
    red: '#DC2626',
    blue: '#2563EB',
    macdDif: '#F59E0B',
    macdDea: '#A855F7',
    overlay1: '#F59E0B', // MA7 / BOLL UP
    overlay2: '#A855F7', // MA25 / BOLL MB
    overlay3: '#2563EB', // MA99 / BOLL DN
};
