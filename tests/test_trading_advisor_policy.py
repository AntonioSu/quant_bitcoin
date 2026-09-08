"""TradingAdvisor 护栏 / 缓存单测

护栏只管：开仓门槛（entry_ok / 等级 / 方向一致 / 平仓后重开冷却）
和减仓防抖（比例区间 / 次数 / 残仓）。持仓中的平仓决定完全交给 AI。
"""

import time

import multi_agent.trading_advisor as ta
from multi_agent.trading_advisor import TradingAdvisor, TradingDecision


def _advisor(ttl=300) -> TradingAdvisor:
    adv = TradingAdvisor.__new__(TradingAdvisor)
    adv._decision_ttl_sec = float(ttl)
    adv._reduce_count = 0
    adv._initial_position_size = 0.0
    adv._last_close = None
    adv._last_signal_id = None
    adv._last_position_hash = None
    adv._last_decision_ts = 0.0
    adv._cached_decision = None
    return adv


def _policy(adv, decision, signal, direction="NONE", size=0.0, entry=0.0,
            price=64000, signal_id="s"):
    return adv._apply_policy(
        decision, signal=signal, position_direction=direction,
        position_entry=entry, position_size_btc=size, btc_price=price,
        signal_id=signal_id,
    )


# ── 开仓门槛 ──────────────────────────────────────────────

def test_block_open_on_cautious():
    out = _policy(
        _advisor(),
        TradingDecision(action="开空", position_size_hint="25%", reason="CAUTIOUS 试探"),
        {"bias": "SHORT", "confidence_level": "CAUTIOUS", "entry_ok": True},
    )
    assert out.action == "等待入场"
    assert out.position_size_hint == "0%"


def test_allow_open_on_moderate():
    out = _policy(
        _advisor(),
        TradingDecision(action="开空", position_size_hint="50%", reason="MODERATE 开空"),
        {"bias": "SHORT", "confidence_level": "MODERATE", "entry_ok": True},
    )
    assert out.action == "开空"


def _block_reason(signal, action="开多") -> str:
    return _policy(
        _advisor(),
        TradingDecision(action=action, position_size_hint="50%", reason="x"),
        signal, signal_id="s-reason",
    ).reason


def test_block_reason_names_the_failing_condition():
    """拦截原因必须指出真正没通过的那一项。"""
    r = _block_reason({"bias": "NEUTRAL", "confidence_level": "STRONG", "entry_ok": True})
    assert "bias=NEUTRAL" in r and "STRONG<" not in r, r

    r = _block_reason({"bias": "LONG", "confidence_level": "STRONG", "entry_ok": False})
    assert "entry_ok=false" in r and "STRONG<" not in r, r

    r = _block_reason({"bias": "LONG", "confidence_level": "CAUTIOUS", "entry_ok": True})
    assert "CAUTIOUS<MODERATE" in r, r

    r = _block_reason({"bias": "NEUTRAL", "confidence_level": "WEAK", "entry_ok": False})
    assert "entry_ok=false" in r and "bias=NEUTRAL" in r and "WEAK<MODERATE" in r, r

    r = _block_reason({"bias": "SHORT", "confidence_level": "STRONG", "entry_ok": True},
                      action="开多")
    assert r and "护栏拦截开仓:" in r and r.rstrip().endswith(")"), r


# ── 平仓后重开冷却（原始问题：止损出局 → 2 分钟后原样开回）──

_LONG_OK = {"bias": "LONG", "confidence_level": "MODERATE", "entry_ok": True}


def test_reentry_same_direction_blocked_within_cooldown():
    adv = _advisor()
    adv.note_position_closed("LONG", "sig-A")

    out = _policy(adv, TradingDecision(action="开多", position_size_hint="50%"),
                  _LONG_OK, signal_id="sig-B")  # 研判已换，但时间未到
    assert out.action == "等待入场", out
    assert "冷却" in out.reason, out.reason


def test_reentry_same_signal_blocked_after_cooldown():
    adv = _advisor()
    adv.note_position_closed("LONG", "sig-A")
    adv._last_close["ts"] -= ta.REENTRY_COOLDOWN_SEC + 1  # 时间过了，研判没换

    out = _policy(adv, TradingDecision(action="开多", position_size_hint="50%"),
                  _LONG_OK, signal_id="sig-A")
    assert out.action == "等待入场", out
    assert "研判未更新" in out.reason, out.reason


def test_reentry_allowed_with_new_signal_after_cooldown():
    adv = _advisor()
    adv.note_position_closed("LONG", "sig-A")
    adv._last_close["ts"] -= ta.REENTRY_COOLDOWN_SEC + 1

    out = _policy(adv, TradingDecision(action="开多", position_size_hint="50%"),
                  _LONG_OK, signal_id="sig-B")
    assert out.action == "开多", out


def test_reentry_opposite_direction_not_blocked():
    adv = _advisor()
    adv.note_position_closed("LONG", "sig-A")

    out = _policy(adv, TradingDecision(action="开空", position_size_hint="50%"),
                  {"bias": "SHORT", "confidence_level": "STRONG", "entry_ok": True},
                  signal_id="sig-A")
    assert out.action == "开空", out


def test_note_close_ignores_invalid_direction():
    adv = _advisor()
    adv.note_position_closed("NONE", "sig")
    assert adv._last_close is None


# ── 持仓：平仓交给 AI ──────────────────────────────────────

def test_ai_close_is_executed_even_without_reversal():
    """AI 想止盈/离场，护栏不再以「信号未强反转」拦下"""
    adv = _advisor()
    out = _policy(
        adv,
        TradingDecision(action="平仓", reason="峰值 1.8R 回撤 1.1R，突破失败"),
        {"bias": "SHORT", "confidence_level": "MODERATE", "entry_ok": True},
        direction="SHORT", size=0.01, entry=64000, price=63800,
    )
    assert out.action == "平仓" and out.close_ratio == 1.0
    assert adv._reduce_count == 0 and adv._initial_position_size == 0.0


def test_no_forced_close_on_strong_reversal():
    """反向 STRONG 时不再替 AI 强制平仓：AI 说观望就观望"""
    out = _policy(
        _advisor(),
        TradingDecision(action="持仓观望", reason="观望"),
        {"bias": "LONG", "confidence_level": "STRONG", "entry_ok": True},
        direction="SHORT", size=0.01, entry=64000, price=64100,
    )
    assert out.action == "持仓观望"


def test_hold_passes_through_with_stop_r():
    out = _policy(
        _advisor(),
        TradingDecision(action="持仓观望", stop_r=0.0, reason="浮盈 0.7R，推到保本"),
        _LONG_OK, direction="LONG", size=0.01, entry=64000, price=64500,
    )
    assert out.action == "持仓观望" and out.stop_r == 0.0


# ── 减仓防抖 ─────────────────────────────────────────────

def test_reduce_ratio_clamped_to_range():
    adv = _advisor()
    lo = _policy(adv, TradingDecision(action="减仓", close_ratio=0.01, reason="r"),
                 _LONG_OK, direction="LONG", size=0.01, entry=64000)
    assert lo.action == "减仓" and lo.close_ratio == ta._MIN_REDUCE_RATIO

    adv = _advisor()
    hi = _policy(adv, TradingDecision(action="减仓", close_ratio=0.99, reason="r"),
                 _LONG_OK, direction="LONG", size=0.01, entry=64000)
    # 0.9 减完剩 10% < 20% 残仓阈值 → 升级为平仓
    assert hi.action == "平仓", hi


def test_reduce_keeps_stop_r():
    out = _policy(_advisor(),
                  TradingDecision(action="减仓", close_ratio=0.5, stop_r=0.2, reason="r"),
                  _LONG_OK, direction="LONG", size=0.01, entry=64000)
    assert out.action == "减仓" and out.close_ratio == 0.5 and out.stop_r == 0.2


def test_third_reduce_escalates_to_close():
    adv = _advisor()
    adv._reduce_count = ta._MAX_REDUCE_COUNT
    adv._initial_position_size = 0.01
    out = _policy(adv, TradingDecision(action="减仓", close_ratio=0.3, reason="再减"),
                  _LONG_OK, direction="LONG", size=0.006, entry=64000)
    assert out.action == "平仓" and out.close_ratio == 1.0


def test_reduce_leaving_tiny_remainder_escalates_to_close():
    adv = _advisor()
    adv._reduce_count = 1
    adv._initial_position_size = 0.01
    # 剩 30%，再减 50% → 剩 15% < 20%
    out = _policy(adv, TradingDecision(action="减仓", close_ratio=0.5, reason="再减"),
                  _LONG_OK, direction="LONG", size=0.003, entry=64000)
    assert out.action == "平仓", out


def test_reduce_count_increments_and_resets_on_close():
    adv = _advisor()
    first = _policy(adv, TradingDecision(action="减仓", close_ratio=0.3, reason="a"),
                    _LONG_OK, direction="LONG", size=0.01, entry=64000)
    assert first.action == "减仓" and adv._reduce_count == 1
    second = _policy(adv, TradingDecision(action="减仓", close_ratio=0.3, reason="b"),
                     _LONG_OK, direction="LONG", size=0.007, entry=64000)
    assert second.action == "减仓" and adv._reduce_count == 2

    adv.note_position_closed("LONG", "s")
    assert adv._reduce_count == 0 and adv._initial_position_size == 0.0


# ── 决策缓存：有效期 = 节拍 ────────────────────────────────

def test_cache_expires_after_ttl():
    adv = _advisor(ttl=300)
    adv._cached_decision = TradingDecision(action="持仓观望")
    adv._last_signal_id = "sig"
    adv._last_position_hash = "LONG:0.010000"
    adv._last_decision_ts = time.monotonic()

    assert adv._cache_valid("sig", "LONG:0.010000")
    adv._last_decision_ts -= 301
    assert not adv._cache_valid("sig", "LONG:0.010000"), "过期后必须重新问 LLM"


def test_cache_misses_on_signal_or_position_change():
    adv = _advisor(ttl=300)
    adv._cached_decision = TradingDecision(action="持仓观望")
    adv._last_signal_id = "sig"
    adv._last_position_hash = "LONG:0.010000"
    adv._last_decision_ts = time.monotonic()

    assert not adv._cache_valid("sig-new", "LONG:0.010000")
    assert not adv._cache_valid("sig", "LONG:0.005000")
    assert not adv._cache_valid("", "LONG:0.010000"), "无研判 ID 不缓存"


def test_zero_ttl_disables_time_cache():
    adv = _advisor(ttl=0)
    adv._cached_decision = TradingDecision(action="持仓观望")
    adv._last_signal_id = "sig"
    adv._last_position_hash = "NONE:0.000000"
    adv._last_decision_ts = time.monotonic()
    assert not adv._cache_valid("sig", "NONE:0.000000")


# ── 解析：stop_r / close_ratio ────────────────────────────

def test_parse_stop_r_only_when_holding():
    adv = _advisor()
    held = adv._parse_response('{"action": "持仓观望", "stop_r": 0.5}', "LONG")
    assert held.stop_r == 0.5
    neg = adv._parse_response('{"action": "持仓观望", "stop_r": -0.4}', "LONG")
    assert neg.stop_r == -0.4, "负值合法：从 -1R 收紧到 -0.4R"
    flat = adv._parse_response('{"action": "等待入场", "stop_r": 0.5}', "NONE")
    assert flat.stop_r is None
    junk = adv._parse_response('{"action": "持仓观望", "stop_r": "很近"}', "LONG")
    assert junk.stop_r is None


def test_parse_reduce_ratio_defaults_and_clamps():
    adv = _advisor()
    d = adv._parse_response('{"action": "减仓"}', "LONG")
    assert d.close_ratio == 0.5
    d = adv._parse_response('{"action": "减仓", "close_ratio": 0.95}', "LONG")
    assert d.close_ratio == ta._MAX_REDUCE_RATIO
    d = adv._parse_response('{"action": "减仓", "close_ratio": 0.7}', "LONG")
    assert d.close_ratio == 0.7
