"""TradingAdvisor 持仓/开仓护栏单测"""

from multi_agent.trading_advisor import TradingAdvisor, TradingDecision


def _advisor() -> TradingAdvisor:
    adv = TradingAdvisor.__new__(TradingAdvisor)
    adv._reduce_count = 0
    adv._initial_position_size = 0.0
    return adv


def test_block_open_on_cautious():
    adv = _advisor()
    adv._last_partial_close_signal_id = None
    out = adv._apply_policy(
        TradingDecision(action="开空", position_size_hint="25%", reason="CAUTIOUS 试探"),
        signal={"bias": "SHORT", "confidence_level": "CAUTIOUS", "entry_ok": True},
        position_direction="NONE",
        position_entry=0,
        position_size_btc=0,
        btc_price=64000,
        signal_id="s1",
    )
    assert out.action == "等待入场"
    assert out.position_size_hint == "0%"


def test_allow_open_on_moderate():
    adv = _advisor()
    adv._last_partial_close_signal_id = None
    out = adv._apply_policy(
        TradingDecision(action="开空", position_size_hint="50%", reason="MODERATE 开空"),
        signal={"bias": "SHORT", "confidence_level": "MODERATE", "entry_ok": True},
        position_direction="NONE",
        position_entry=0,
        position_size_btc=0,
        btc_price=64000,
        signal_id="s2",
    )
    assert out.action == "开空"


def _block_reason(signal, action="开多") -> str:
    adv = _advisor()
    adv._last_partial_close_signal_id = None
    return adv._apply_policy(
        TradingDecision(action=action, position_size_hint="50%", reason="x"),
        signal=signal,
        position_direction="NONE",
        position_entry=0,
        position_size_btc=0,
        btc_price=64000,
        signal_id="s-reason",
    ).reason


def test_block_reason_names_the_failing_condition():
    """拦截原因必须指出真正没通过的那一项。

    以前无论哪项失败都拼 "{level}<MODERATE"，等级明明够用时也这么写，
    照着日志排查会被带到完全错误的方向。
    """
    # 等级够、bias 不是 LONG/SHORT → 不能诬告等级
    r = _block_reason({"bias": "NEUTRAL", "confidence_level": "STRONG", "entry_ok": True})
    assert "bias=NEUTRAL" in r, r
    assert "STRONG<" not in r, r

    # 等级够、entry_ok=false → 只报 entry_ok
    r = _block_reason({"bias": "LONG", "confidence_level": "STRONG", "entry_ok": False})
    assert "entry_ok=false" in r and "STRONG<" not in r, r

    # 等级确实不够 → 要报等级
    r = _block_reason({"bias": "LONG", "confidence_level": "CAUTIOUS", "entry_ok": True})
    assert "CAUTIOUS<MODERATE" in r, r

    # 多项同时失败 → 全部列出
    r = _block_reason({"bias": "NEUTRAL", "confidence_level": "WEAK", "entry_ok": False})
    assert "entry_ok=false" in r and "bias=NEUTRAL" in r and "WEAK<MODERATE" in r, r

    # 各项都过、只是方向与 bias 相反 → 不能报成空原因
    r = _block_reason({"bias": "SHORT", "confidence_level": "STRONG", "entry_ok": True},
                      action="开多")
    assert r and "护栏拦截开仓:" in r and r.rstrip().endswith(")"), r


def test_entry_ok_false_does_not_force_close_when_holding():
    adv = _advisor()
    adv._last_partial_close_signal_id = None
    out = adv._apply_policy(
        TradingDecision(action="平仓", reason="entry_ok=false 果断离场"),
        signal={
            "bias": "NEUTRAL",
            "confidence_level": "WEAK",
            "entry_ok": False,
        },
        position_direction="SHORT",
        position_entry=64000,
        position_size_btc=0.01,
        btc_price=63800,  # 小幅浮盈
        signal_id="s3",
    )
    assert out.action == "持仓观望"


def test_strong_reversal_forces_close():
    adv = _advisor()
    adv._last_partial_close_signal_id = None
    out = adv._apply_policy(
        TradingDecision(action="持仓观望", reason="观望"),
        signal={"bias": "LONG", "confidence_level": "STRONG", "entry_ok": True},
        position_direction="SHORT",
        position_entry=64000,
        position_size_btc=0.01,
        btc_price=64100,
        signal_id="s4",
    )
    assert out.action == "平仓"
    assert out.close_ratio == 1.0


def test_moderate_reversal_caps_reduce():
    adv = _advisor()
    adv._last_partial_close_signal_id = None
    out = adv._apply_policy(
        TradingDecision(action="平仓", close_ratio=1.0, reason="反转平仓"),
        signal={"bias": "LONG", "confidence_level": "MODERATE", "entry_ok": True},
        position_direction="SHORT",
        position_entry=64000,
        position_size_btc=0.01,
        btc_price=64100,
        signal_id="s5",
    )
    assert out.action == "减仓"
    assert out.close_ratio == 0.25


def test_same_signal_reduce_only_once():
    adv = _advisor()
    adv._last_partial_close_signal_id = None
    first = adv._apply_policy(
        TradingDecision(action="减仓", close_ratio=0.5, reason="减仓"),
        signal={"bias": "LONG", "confidence_level": "MODERATE", "entry_ok": True},
        position_direction="SHORT",
        position_entry=64000,
        position_size_btc=0.01,
        btc_price=64100,
        signal_id="same",
    )
    second = adv._apply_policy(
        TradingDecision(action="减仓", close_ratio=0.5, reason="再减"),
        signal={"bias": "LONG", "confidence_level": "MODERATE", "entry_ok": True},
        position_direction="SHORT",
        position_entry=64000,
        position_size_btc=0.005,
        btc_price=64100,
        signal_id="same",
    )
    assert first.action == "减仓"
    assert first.close_ratio == 0.25
    assert second.action == "持仓观望"


def test_reduce_exhausted_escalates_to_close():
    """累计减仓达到上限后，信号仍反向时升级为全平"""
    adv = _advisor()
    adv._last_partial_close_signal_id = None
    adv._reduce_count = 2
    adv._initial_position_size = 0.01

    out = adv._apply_policy(
        TradingDecision(action="减仓", close_ratio=0.25, reason="继续减仓"),
        signal={"bias": "LONG", "confidence_level": "MODERATE", "entry_ok": True},
        position_direction="SHORT",
        position_entry=64000,
        position_size_btc=0.005,
        btc_price=64100,
        signal_id="s6",
    )
    assert out.action == "平仓"
    assert out.close_ratio == 1.0
    assert "已减仓" in out.reason


def test_position_too_small_escalates_to_close():
    """仓位低于初始 50% 且信号仍反向时升级为全平"""
    adv = _advisor()
    adv._last_partial_close_signal_id = None
    adv._reduce_count = 1
    adv._initial_position_size = 0.01

    out = adv._apply_policy(
        TradingDecision(action="减仓", close_ratio=0.25, reason="再减仓"),
        signal={"bias": "LONG", "confidence_level": "MODERATE", "entry_ok": True},
        position_direction="SHORT",
        position_entry=64000,
        position_size_btc=0.004,
        btc_price=64100,
        signal_id="s7",
    )
    assert out.action == "平仓"
    assert out.close_ratio == 1.0
