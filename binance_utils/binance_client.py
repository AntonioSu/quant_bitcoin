#!/usr/bin/env python3
"""
Binance 客户端 - 简化版

提供:
1. 交易所连接管理（testnet/demo/mainnet）
2. 暴露 self.exchange 供外部直接操作 ccxt
"""

import os

from utils import logger

os.environ["CRYPTOGRAPHY_OPENSSL_NO_LEGACY"] = "1"

import ccxt


class BinanceClient:
    """币安客户端
    
    连接模式:
      - testnet=True:  旧版测试网 (仅现货可用, 合约已弃用)
      - demo=True:     Demo Trading (合约推荐, 需要 demo API key)
      - 两者均 False:  真实交易
    """

    def __init__(self, binance_cfg: dict, market_type: str = "future", demo: bool = False, testnet: bool = False):
        """
        Args:
            binance_cfg: Binance 配置字典 (必须包含 api_key, secret_key，可选 proxy)
            market_type: 市场类型 "spot"(现货) 或 "future"(合约)
            demo: 是否使用 Demo Trading 模式 (合约推荐)
            testnet: 是否使用测试网 (现货)
        """
        api_key = binance_cfg.get("api_key", "")
        secret_key = binance_cfg.get("secret_key", "")

        exchange_config = {
            "apiKey": api_key,
            "secret": secret_key,
            "enableRateLimit": True,
            "options": {"defaultType": market_type},
        }

        # 代理配置 (demo-api.binance.com 可能需要)
        proxy = binance_cfg.get("proxy")
        if proxy:
            exchange_config["proxies"] = {"http": proxy, "https": proxy}

        self.exchange = ccxt.binance(exchange_config)

        if demo:
            self.exchange.enable_demo_trading(True)
            logger.info(f"📡 Binance Demo Trading 模式 ({market_type})")
        elif testnet:
            self.exchange.set_sandbox_mode(True)
            logger.info(f"📡 Binance Testnet 模式 ({market_type})")
