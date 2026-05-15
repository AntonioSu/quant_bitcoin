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
