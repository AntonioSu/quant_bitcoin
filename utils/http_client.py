"""统一 HTTP 客户端

集中处理代理、SSL、超时配置，避免在每个模块中重复。
"""

import os
import ssl
from typing import Optional

import aiohttp
import requests

from .log_util import logger


def _get_proxy() -> Optional[str]:
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")


def _get_ssl_context(proxy: Optional[str]):
    if not proxy:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def sync_get(url: str, params: dict = None, timeout: int = 10) -> dict:
    """同步 GET 请求 (用于 FastAPI 路由中的简单请求)"""
    proxy = _get_proxy()
    proxies = {"https": proxy, "http": proxy} if proxy else None
    resp = requests.get(
        url,
        params=params,
        timeout=timeout,
        proxies=proxies,
        verify=not bool(proxy),
    )
    resp.raise_for_status()
    return resp.json()


async def async_get(url: str, params: dict = None, timeout: int = 10) -> dict:
    """异步 GET 请求 (用于调度器等 async 上下文)"""
    proxy = _get_proxy()
    ssl_ctx = _get_ssl_context(proxy)
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(
            url,
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout),
            proxy=proxy,
        ) as resp:
            return await resp.json()
