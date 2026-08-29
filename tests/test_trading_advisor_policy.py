"""TradingAdvisor 持仓/开仓护栏单测"""

from multi_agent.trading_advisor import TradingAdvisor, TradingDecision


def _advisor() -> TradingAdvisor:
    return TradingAdvisor.__new__(TradingAdvisor)


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
