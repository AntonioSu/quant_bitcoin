// AI 综合研判面板渲染
function updateAiAnalysis(data) {
    const biasEl = document.getElementById('ai-bias');
    const confEl = document.getElementById('ai-confidence');
    const actionEl = document.getElementById('ai-action');
    const summaryEl = document.getElementById('ai-summary');
    const horizonEl = document.getElementById('ai-horizon');
    const metaEl = document.getElementById('ai-updated-at');
    const committeeEl = document.getElementById('ai-committee');
    const entryOkEl = document.getElementById('ai-entry-ok');
    const positionEl = document.getElementById('ai-position-size');
    const committeeNoteEl = document.getElementById('ai-committee-note');
    if (!biasEl) return;

    const bias = (data.ai_bias || 'NEUTRAL').toUpperCase();
    const biasMap = { LONG: '看多', SHORT: '看空', NEUTRAL: '中性' };
    const biasClass = bias === 'LONG' ? 'bullish' : bias === 'SHORT' ? 'bearish' : 'neutral';
    biasEl.textContent = biasMap[bias] || '--';
    biasEl.className = 'ai-bias ' + biasClass;

    const confLevel = data.ai_confidence_level;
    const levelMap = { VERY_STRONG: '极强', STRONG: '强', MODERATE: '中', CAUTIOUS: '谨慎', WEAK: '弱' };
    if (confLevel) {
        confEl.textContent = levelMap[confLevel.toUpperCase()] || confLevel;
        confEl.className = 'ai-confidence ' + biasClass;
    } else {
        confEl.textContent = '--';
        confEl.className = 'ai-confidence';
    }

    const action = data.ai_action || '等待分析';
    actionEl.textContent = action;
    const actionClass = /加多|加仓.*多|看多|做多/.test(action) ? 'bullish'
        : /加空|做空|看空|减仓|离场/.test(action) ? 'bearish'
        : 'neutral';
    actionEl.className = 'ai-action ' + actionClass;

    summaryEl.textContent = data.ai_summary || '等待首次 AI 综合研判...';

    const hasCommittee = (
        data.ai_entry_ok !== null && data.ai_entry_ok !== undefined
    ) || !!data.ai_committee;
    if (committeeEl) {
        committeeEl.hidden = !hasCommittee;
    }
    if (hasCommittee && entryOkEl) {
        const entryOk = data.ai_entry_ok === true;
        entryOkEl.textContent = entryOk ? '入场 OK' : '入场 WAIT';
        entryOkEl.className = 'ai-committee-pill ' + (entryOk ? 'bullish' : 'neutral');
    }
    if (hasCommittee && positionEl) {
        positionEl.textContent = '仓位 ' + (data.ai_position_size_hint || '0%');
    }
    if (hasCommittee && committeeNoteEl) {
        const committee = data.ai_committee || {};
        const note = committee.risk_review
            || committee.manager_rationale
            || committee.bull_case
            || committee.bear_case
            || '';
        committeeNoteEl.textContent = note;
    }

    if (data.ai_horizon) horizonEl.textContent = data.ai_horizon;

    const renderDrivers = (listId, items, sideFilter) => {
        const list = document.getElementById(listId);
        if (!list) return;
        list.innerHTML = '';
        (items || []).forEach(it => {
            if (sideFilter && typeof it === 'object' && it.side && it.side !== sideFilter) return;
            const li = document.createElement('li');
            const text = typeof it === 'string'
                ? it
                : (it.factor || it.text || JSON.stringify(it));
            const weight = typeof it === 'object' ? (it.weight || '') : '';
            if (weight) {
                const tag = document.createElement('span');
                tag.className = 'ai-weight ' + weight;
                tag.textContent = weight.toUpperCase();
                li.appendChild(tag);
            }
            li.appendChild(document.createTextNode(text));
            list.appendChild(li);
        });
    };

    const drivers = data.ai_key_drivers || [];
    renderDrivers('ai-bull-list', drivers, 'bull');

    const bearDrivers = drivers.filter(d => typeof d === 'object' && d.side === 'bear');
    const risks = (data.ai_risks || []).map(r => ({ factor: r, weight: '' }));
    renderDrivers('ai-risk-list', [...bearDrivers, ...risks]);

    if (data.ai_updated_at) {
        const dt = new Date(data.ai_updated_at);
        metaEl.textContent = '更新: ' + dt.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
}

// ─── 立即分析按钮 ──────────────────────────────────────────────
const AI_LOCAL_COOLDOWN_MS = 30000;
let _aiBtnLocalCooldownUntil = 0;
let _aiBtnCountdownTimer = null;

function _setAiBtnState(btn, state, label) {
    if (!btn) return;
    const iconEl = btn.querySelector('.ai-refresh-icon');
    const labelEl = btn.querySelector('.ai-refresh-label');
    btn.classList.remove('is-loading', 'is-cooldown', 'is-success', 'is-error');
    if (iconEl) iconEl.classList.remove('spinning');

    if (state === 'loading') {
        btn.classList.add('is-loading');
        btn.disabled = true;
        if (iconEl) iconEl.classList.add('spinning');
        if (labelEl) labelEl.textContent = label || '分析中…';
    } else if (state === 'cooldown') {
        btn.classList.add('is-cooldown');
        btn.disabled = true;
        if (labelEl) labelEl.textContent = label || '稍候';
    } else if (state === 'success') {
        btn.classList.add('is-success');
        btn.disabled = false;
        if (labelEl) labelEl.textContent = label || '已更新';
    } else if (state === 'error') {
        btn.classList.add('is-error');
        btn.disabled = false;
        if (labelEl) labelEl.textContent = label || '失败';
    } else {
        btn.disabled = false;
        if (labelEl) labelEl.textContent = label || '立即分析';
    }
}

function _startAiBtnCooldown(btn, seconds) {
    if (_aiBtnCountdownTimer) {
        clearInterval(_aiBtnCountdownTimer);
        _aiBtnCountdownTimer = null;
    }
    _aiBtnLocalCooldownUntil = Date.now() + seconds * 1000;

    const tick = () => {
        const remain = Math.ceil((_aiBtnLocalCooldownUntil - Date.now()) / 1000);
        if (remain <= 0) {
            clearInterval(_aiBtnCountdownTimer);
            _aiBtnCountdownTimer = null;
            _setAiBtnState(btn, 'idle');
        } else {
            _setAiBtnState(btn, 'cooldown', `${remain}s 后可点`);
        }
    };
    tick();
    _aiBtnCountdownTimer = setInterval(tick, 1000);
}

async function _triggerAiRefresh(btn) {
    if (!btn || btn.disabled) return;
    if (Date.now() < _aiBtnLocalCooldownUntil) return;

    _setAiBtnState(btn, 'loading');
    try {
        const resp = await fetch(`${API_BASE}/api/ai-analysis/refresh`, { method: 'POST' });
        const data = await resp.json();

        if (data.status === 'ok') {
            updateAiAnalysis(data);
            _setAiBtnState(btn, 'success', '已更新');
            _startAiBtnCooldown(btn, AI_LOCAL_COOLDOWN_MS / 1000);
        } else if (data.status === 'running') {
            _setAiBtnState(btn, 'cooldown', '分析中…');
            setTimeout(() => _setAiBtnState(btn, 'idle'), 3000);
        } else if (data.status === 'cooldown') {
            _startAiBtnCooldown(btn, data.retry_after || 30);
        } else {
            _setAiBtnState(btn, 'error', data.message ? '失败' : '失败');
            console.error('AI 手动刷新失败:', data.message);
            setTimeout(() => _setAiBtnState(btn, 'idle'), 3000);
        }
    } catch (e) {
        console.error('AI 手动刷新请求失败:', e);
        _setAiBtnState(btn, 'error', '失败');
        setTimeout(() => _setAiBtnState(btn, 'idle'), 3000);
    }
}

function initAiRefreshButton() {
    const btn = document.getElementById('ai-refresh-btn');
    if (!btn) return;
    btn.addEventListener('click', () => _triggerAiRefresh(btn));
}
