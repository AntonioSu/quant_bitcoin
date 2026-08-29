// 通用格式化与 DOM 辅助函数
function formatNumber(num, decimals = 2) {
    return new Intl.NumberFormat('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    }).format(num);
}

function formatUSD(num) {
    return '$' + formatNumber(num);
}

function formatPnl(num) {
    const formatted = formatUSD(Math.abs(num));
    return num >= 0 ? '+' + formatted : '-' + formatted;
}

function getColorClass(value, threshold = 0) {
    return value > threshold ? 'green' : value < threshold ? 'red' : '';
}

function setIfExists(id, text, className) {
    const el = document.getElementById(id);
    if (!el) return;
    if (text !== undefined) el.textContent = text;
    if (className !== undefined) el.className = className;
}

// Server timestamps are naive Asia/Shanghai local time from Python isoformat()
// (no offset, often 6-digit microseconds). Safari treats no-offset ISO as UTC,
// which shifts displayed clock times +8h and makes them look later than now.
function parseChinaTime(iso) {
    if (!iso) return null;
    let s = String(iso).trim();
    s = s.replace(/(\.\d{3})\d+/, '$1');
    if (!/[zZ]$/.test(s) && !/[+-]\d{2}:\d{2}$/.test(s)) {
        s += '+08:00';
    }
    const dt = new Date(s);
    return Number.isNaN(dt.getTime()) ? null : dt;
}

function formatClockTime(iso) {
    const dt = parseChinaTime(iso);
    if (!dt) return '--';
    const tz = 'Asia/Shanghai';
    const clockOpts = { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz };
    const dayKey = (d) => d.toLocaleDateString('en-CA', { timeZone: tz });
    if (dayKey(dt) !== dayKey(new Date())) {
        return dt.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', ...clockOpts });
    }
    return dt.toLocaleTimeString('zh-CN', clockOpts);
}
