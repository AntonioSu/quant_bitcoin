"""通知插件挂载 - 通过追加 on_trade 回调接入，不修改调度器核心逻辑"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def attach_trade_notifications(app_state) -> bool:
    """向已创建的调度器追加成交通知。未配置 FEISHU_WEBHOOK_URL 时 no-op。"""
    if not os.getenv("FEISHU_WEBHOOK_URL", "").strip():
        return False

    from notifications.feishu_trade import notify_trade_feishu, should_notify_trade

    attached = 0
    for scheduler_key, scheduler in app_state.schedulers.items():
        async def on_trade(trade, key=scheduler_key):
            if not should_notify_trade(trade):
                return
            try:
                asyncio.create_task(notify_trade_feishu(trade, key))
            except Exception:
                logger.exception("飞书通知任务创建失败 [%s]", key)

        scheduler.on_trade(on_trade)
        attached += 1

    logger.info("飞书成交通知已挂载 (%d 个调度器)", attached)
    return True
