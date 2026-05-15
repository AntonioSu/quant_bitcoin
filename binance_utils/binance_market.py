#!/usr/bin/env python3
"""
Binance 市场数据 API 封装

提供:
1. K线数据获取（带缓存机制）
2. 实时价格查询
3. 其他市场数据接口
4. 同步和异步两种调用方式
"""

import time
from typing import List, Optional

from stock_btc.utils import logger
from stock_btc.utils.http_client import async_get, sync_get


# ══════════════════════════════════════════════════════════════
# 全局K线缓存（多个 scheduler 共享，避免重复请求）
# ══════════════════════════════════════════════════════════════

class _KlinesCache:
    """K线数据全局缓存（单例模式）"""
    _instance = None
    _klines: Optional[List] = None
    _timestamp: float = 0
    _ttl: int = 60  # 缓存60秒
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def get(self) -> Optional[List]:
        """获取缓存的K线数据"""
        if self._klines and (time.time() - self._timestamp) < self._ttl:
            return self._klines
        return None
    
    def set(self, klines: List):
        """设置缓存的K线数据"""
        self._klines = klines
        self._timestamp = time.time()
    
    def is_valid(self) -> bool:
        """检查缓存是否有效"""
        return self._klines is not None and (time.time() - self._timestamp) < self._ttl


# 全局缓存实例
_klines_cache = _KlinesCache()


# ══════════════════════════════════════════════════════════════
# 市场数据 API
# ══════════════════════════════════════════════════════════════

async def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    limit: int = 100,
    use_cache: bool = True
) -> List:
    """
    获取K线数据（使用全局缓存，多个 scheduler 共享）
    
    Args:
        symbol: 交易对（默认 BTCUSDT）
        interval: K线周期（1m, 5m, 15m, 1h, 4h, 1d 等）
        limit: K线数量
        use_cache: 是否使用缓存（默认 True，避免重复请求）
    
    Returns:
        K线列表，每根K线格式: [timestamp, open, high, low, close, volume]
    """
    # 使用全局缓存避免多个 scheduler 重复请求
    if use_cache:
        cached = _klines_cache.get()
        if cached and len(cached) >= limit:
            logger.debug(f"使用缓存的K线数据 (共 {len(cached)} 根)")
            return cached[:limit]
    
    try:
        logger.info("📊 获取最新K线数据...")
        raw = await async_get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10,
        )
        klines = [
            [k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
             k[6], float(k[7]), int(k[8]), float(k[9]), float(k[10]), k[11]]
            for k in raw
        ]
        
        # 更新全局缓存
        if use_cache:
            _klines_cache.set(klines)
            logger.debug(f"K线数据已缓存 (共 {len(klines)} 根)")
        
        return klines
    except Exception as e:
        logger.error(f"获取K线失败: {e}")
        # 如果请求失败，尝试返回缓存
        if use_cache and _klines_cache.is_valid():
            logger.warning("⚠️ 网络请求失败，使用缓存的K线数据")
            cached = _klines_cache.get()
            return cached[:limit] if cached else []
        return []


async def fetch_price(symbol: str = "BTCUSDT") -> float:
    """
    获取指定交易对的实时价格
    
    Args:
        symbol: 交易对（默认 BTCUSDT）
    
    Returns:
        当前价格（失败返回 0.0）
    """
    try:
        data = await async_get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=5,
        )
        return float(data["price"])
    except Exception as e:
        logger.error(f"获取价格失败 ({symbol}): {e}")
        return 0.0


async def fetch_24h_ticker(symbol: str = "BTCUSDT") -> dict:
    """
    获取24小时价格变动统计
    
    Args:
        symbol: 交易对（默认 BTCUSDT）
    
    Returns:
        包含价格、涨跌幅、成交量等信息的字典
    """
    try:
        data = await async_get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": symbol},
            timeout=5,
        )
        return {
            "price": float(data["lastPrice"]),
            "price_change": float(data["priceChange"]),
            "price_change_percent": float(data["priceChangePercent"]),
            "high": float(data["highPrice"]),
            "low": float(data["lowPrice"]),
            "volume": float(data["volume"]),
            "quote_volume": float(data["quoteVolume"]),
        }
    except Exception as e:
        logger.error(f"获取24h统计失败 ({symbol}): {e}")
        return {}


def clear_cache():
    """清除K线缓存（用于测试或手动刷新）"""
    _klines_cache._klines = None
    _klines_cache._timestamp = 0
    logger.info("K线缓存已清除")


# ══════════════════════════════════════════════════════════════
# 同步版本（用于 FastAPI 路由等同步上下文）
# ══════════════════════════════════════════════════════════════

def fetch_klines_sync(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    limit: int = 100,
    use_cache: bool = True
) -> List:
    """
    获取K线数据（同步版本，用于 FastAPI 路由）
    
    Args:
        symbol: 交易对（默认 BTCUSDT）
        interval: K线周期（1m, 5m, 15m, 1h, 4h, 1d 等）
        limit: K线数量
        use_cache: 是否使用缓存（默认 True，避免重复请求）
    
    Returns:
        K线列表，每根K线格式: [timestamp, open, high, low, close, volume, 
        close_time, quote_asset_volume, number_of_trades, 
        taker_buy_base_asset_volume, taker_buy_quote_asset_volume, ignore]
    """
    # 使用全局缓存避免多个调用重复请求
    if use_cache:
        cached = _klines_cache.get()
        if cached and len(cached) >= limit:
            logger.debug(f"使用缓存的K线数据 (共 {len(cached)} 根)")
            return cached[:limit]
    
    try:
        logger.info("📊 获取最新K线数据...")
        raw = sync_get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10,
        )
        klines = [
            [k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
             k[6], float(k[7]), int(k[8]), float(k[9]), float(k[10]), k[11]]
            for k in raw
        ]
        
        # 更新全局缓存
        if use_cache:
            _klines_cache.set(klines)
            logger.debug(f"K线数据已缓存 (共 {len(klines)} 根)")
        
        return klines
    except Exception as e:
        logger.error(f"获取K线失败: {e}")
        # 如果请求失败，尝试返回缓存
        if use_cache and _klines_cache.is_valid():
            logger.warning("⚠️ 网络请求失败，使用缓存的K线数据")
            cached = _klines_cache.get()
            return cached[:limit] if cached else []
        return []


def fetch_price_sync(symbol: str = "BTCUSDT") -> float:
    """
    获取指定交易对的实时价格（同步版本，用于 FastAPI 路由）
    
    Args:
        symbol: 交易对（默认 BTCUSDT）
    
    Returns:
        当前价格（失败返回 0.0）
    """
    try:
        data = sync_get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=5,
        )
        return float(data["price"])
    except Exception as e:
        logger.error(f"获取价格失败 ({symbol}): {e}")
        return 0.0


def fetch_24h_ticker_sync(symbol: str = "BTCUSDT") -> dict:
    """
    获取24小时价格变动统计（同步版本，用于 FastAPI 路由）
    
    Args:
        symbol: 交易对（默认 BTCUSDT）
    
    Returns:
        包含价格、涨跌幅、成交量等信息的字典
    """
    try:
        data = sync_get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": symbol},
            timeout=5,
        )
        return {
            "price": float(data["lastPrice"]),
            "price_change": float(data["priceChange"]),
            "price_change_percent": float(data["priceChangePercent"]),
            "high": float(data["highPrice"]),
            "low": float(data["lowPrice"]),
            "volume": float(data["volume"]),
            "quote_volume": float(data["quoteVolume"]),
        }
    except Exception as e:
        logger.error(f"获取24h统计失败 ({symbol}): {e}")
        return {}
