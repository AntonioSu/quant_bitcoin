// Manual news retrieval and analysis; cooldown is independent of the AI panel.
function initNewsRefreshButton() {
    const btn = document.getElementById('news-refresh-btn');
    const status = document.getElementById('news-refresh-status');
    if (!btn) return;
    const cooldown = (seconds) => {
        const until = Date.now() + seconds * 1000;
        const tick = () => {
            const remaining = Math.ceil((until - Date.now()) / 1000);
            _setAiBtnState(btn, remaining > 0 ? 'cooldown' : 'idle',
                remaining > 0 ? `${remaining}s 后可点` : undefined);
            if (remaining <= 0) clearInterval(timer);
        };
        const timer = setInterval(tick, 1000);
        tick();
    };
    btn.addEventListener('click', async () => {
        if (btn.disabled) return;
        status.hidden = false;
        status.textContent = '正在获取最新新闻并分析…';
        _setAiBtnState(btn, 'loading');
        btn.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(`${API_BASE}/api/news-analysis/refresh`, { method: 'POST' });
            if (!response.ok) {
                const message = response.status === 404
                    ? '新闻分析接口未加载，请重启后端服务后重试（HTTP 404）'
                    : `请求失败（HTTP ${response.status}），请稍后重试`;
                throw new Error(message);
            }
            const data = await response.json();
            if (data.status === 'ok') {
                updateNewsAnalysis(data);
                status.textContent = '新闻分析已更新';
                cooldown(30);
            } else if (data.status === 'cooldown' || data.status === 'running') {
                status.textContent = data.status === 'running'
                    ? '新闻分析正在进行中，完成后将自动更新' : '刚刚完成分析，请稍后重试';
                cooldown(data.retry_after || 5);
            } else {
                throw new Error(data.message || '新闻分析失败，请稍后重试');
            }
        } catch (error) {
            status.textContent = error.message || '请求失败，请稍后重试';
            _setAiBtnState(btn, 'error', '重试分析');
        } finally {
            btn.setAttribute('aria-busy', 'false');
        }
    });
}
