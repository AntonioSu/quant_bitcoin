"""飞书成交通知 - 独立模块，仅依赖 requests + 环境变量"""

import asyncio
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

NOTIFY_ACTIONS = frozenset({"LONG", "SHORT", "CLOSE", "REDUCE"})

_ACTION_LABELS = {
    "LONG": "做多",
    "SHORT": "做空",
    "CLOSE": "平仓",
    "REDUCE": "减仓",
}

_EVENT_LABELS = {
    "LONG": "开仓",
    "SHORT": "开仓",
    "CLOSE": "平仓",
    "REDUCE": "减仓",
}

_MODE_EMOJI = {"LONG": "🗡️", "SHORT": "🛡️"}

_SIGNAL_LABELS = {
    "golden_cross": "金叉", "death_cross": "死叉", "none": "-",
    "overbought": "超买", "oversold": "超卖",
    "bullish_divergence": "底背离", "bearish_divergence": "顶背离",
    "breakout_upper": "突破上轨", "breakout_lower": "突破下轨", "squeeze": "收窄",
    "bullish_alignment": "多头排列", "bearish_alignment": "空头排列",
    "surge_up": "放量上涨", "surge_down": "放量下跌", "dry_up": "缩量",
    "divergence_top": "量价顶背离", "divergence_bottom": "量价底背离",
}


def should_notify_trade(trade: dict) -> bool:
    return trade.get("action") in NOTIFY_ACTIONS


def _webhook_url() -> Optional[str]:
    url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    return url or None


def _fmt_pct(v) -> Optional[str]:
    if v is None:
        return None
    v = float(v)
    return f"{v:+.2f}%"


def _signal_label(sig) -> str:
    if not sig:
        return "-"
    return _SIGNAL_LABELS.get(sig, str(sig))


def _append_line(lines: list, label: str, value) -> None:
    if value is None or value == "":
        return
    lines.append(f"{label}: {value}")


def _format_indicators(ind: dict) -> list[str]:
    if not ind:
        return []

    lines = []
    price_pct = ind.get("price_change_pct", ind.get("price_change_percent"))
    cvd_pct = ind.get("cvd_change_pct", ind.get("cvd_change_percent"))

    _append_line(lines, "恐贪", ind.get("fear_greed_index"))
    if ind.get("funding_rate") is not None:
        _append_line(lines, "费率", f"{ind['funding_rate']:.5f}%")
    if ind.get("long_short_ratio") is not None:
        _append_line(lines, "多空比", f"{ind['long_short_ratio']:.2f}")
    _append_line(lines, "价格变化", _fmt_pct(price_pct))
    _append_line(lines, "CVD变化", _fmt_pct(cvd_pct))
    _append_line(lines, "背离", ind.get("divergence_type"))

    if ind.get("macd_signal") is not None:
        _append_line(lines, "MACD", _signal_label(ind["macd_signal"]))
    if ind.get("rsi_value") is not None:
        _append_line(lines, "RSI", f"{ind['rsi_value']:.1f}")
    if ind.get("boll_signal") is not None:
        _append_line(lines, "布林", _signal_label(ind["boll_signal"]))
    if ind.get("ma_signal") is not None:
        _append_line(lines, "均线", _signal_label(ind["ma_signal"]))
    if ind.get("vol_signal") is not None:
        _append_line(lines, "成交量", _signal_label(ind["vol_signal"]))
    if ind.get("taker_buy_ratio") is not None:
        _append_line(lines, "主买占比", f"{ind['taker_buy_ratio'] * 100:.1f}%")
    if ind.get("news_score") is not None:
        _append_line(lines, "新闻", ind["news_score"])
    if ind.get("ai_bias"):
        level = ind.get("ai_confidence_level", "")
        ai_text = ind["ai_bias"]
        if level:
            ai_text = f"{ai_text} {level}"
        _append_line(lines, "AI研判", ai_text)

    return lines


def _format_levels(levels: dict) -> list[str]:
    if not levels:
        return []

    lines = []
    if levels.get("stop_loss"):
        lines.append(f"止损: ${levels['stop_loss']:,.2f}")
    if levels.get("liquidation_price"):
        lines.append(f"强平: ${levels['liquidation_price']:,.2f}")
    if levels.get("atr"):
        lines.append(f"ATR: ${levels['atr']:,.2f}")
    return lines


def _format_message(trade: dict, preset: str) -> tuple[str, str, str]:
    action = trade.get("action", "")
    mode = trade.get("mode", "")
    direction = _ACTION_LABELS.get(action, action)
    event = _EVENT_LABELS.get(action, "成交")
    emoji = _MODE_EMOJI.get(mode, "📊")
    is_close = action in ("CLOSE", "REDUCE")

    lines = [
        f"预设: {preset}",
        f"{mode} - {action}",
    ]

    if is_close and trade.get("entry_price"):
        lines.append(
            f"价格: ${trade['entry_price']:,.2f} → ${trade.get('price', 0):,.2f}"
        )
    else:
        lines.append(f"价格: ${trade.get('price', 0):,.2f}")

    sizing = []
    if trade.get("notional"):
        sizing.append(f"${trade['notional']:,.2f}")
    if trade.get("leverage"):
        sizing.append(f"{trade['leverage']}x")
    if sizing:
        lines.append(" · ".join(sizing))
    lines.append(f"数量: {trade.get('amount', 0):.4f} BTC")
    if is_close:
        lines.append(f"盈亏: ${trade.get('pnl', 0):+,.2f}")
    if trade.get("time"):
        lines.append(f"时间: {trade['time']}")

    ind = trade.get("market_indicators") or {}
    indicator_lines = _format_indicators(ind)
    if indicator_lines:
        lines.append("")
        lines.extend(indicator_lines)

    level_lines = _format_levels(trade.get("levels") or {})
    if level_lines:
        lines.append("")
        lines.extend(level_lines)

    reason = trade.get("trigger_reason")
    if reason:
        reason_label = "平仓原因" if is_close else "触发"
        lines.append("")
        lines.append(f"{reason_label}: {reason}")

    ai_summary = ind.get("ai_summary")
    if ai_summary:
        lines.append(f"AI: {ai_summary}")

    price = trade.get("price", 0)
    if is_close:
        pnl = trade.get("pnl", 0)
        pnl_tag = "盈利" if pnl >= 0 else "亏损"
        title = f"{emoji} {event} · {pnl_tag} ${pnl:+,.2f} @ ${price:,.0f}"
    else:
        title = f"{emoji} {event} · {direction} @ ${price:,.0f}"

    return title, "\n".join(lines), _card_color(trade)


def _card_color(trade: dict) -> str:
    action = trade.get("action", "")
    if action in ("CLOSE", "REDUCE"):
        return "green" if trade.get("pnl", 0) >= 0 else "red"
    if action == "LONG":
        return "blue"
    if action == "SHORT":
        return "orange"
    return "blue"


def _send_feishu(webhook_url: str, title: str, body: str, color: str) -> bool:
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text", "content": body}},
            ],
        },
    }
    resp = requests.post(
        webhook_url,
        json=payload,
        timeout=10,
        proxies={"http": None, "https": None},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        logger.warning("飞书推送返回异常: %s", data)
        return False
    return True


def notify_trade_feishu_sync(trade: dict, preset: str) -> bool:
    if not should_notify_trade(trade):
        return False
    webhook_url = _webhook_url()
    if not webhook_url:
        return False
    title, body, color = _format_message(trade, preset)
    try:
        ok = _send_feishu(webhook_url, title, body, color)
        if ok:
            logger.info("飞书成交通知已发送 [%s] %s", preset, trade.get("action"))
        return ok
    except Exception:
        logger.exception("飞书成交通知发送失败")
        return False


async def notify_trade_feishu(trade: dict, preset: str) -> bool:
    return await asyncio.to_thread(notify_trade_feishu_sync, trade, preset)
