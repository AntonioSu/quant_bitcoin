"""巨鲸链上数据源

监控指标:
- 交易所净流入/流出 (Exchange Netflow)
- 正值 = 净流入 (卖压，巨鲸准备砸盘)
- 负值 = 净流出 (囤币，巨鲸准备抄底)

数据源选项:
1. CryptoQuant API (需API Key)
2. Glassnode API (需API Key)
3. Blockchain.info (免费但功能有限)

神盾模式: 净流入 > 2000 BTC (卖压信号)
长矛模式: 净流出 < -2000 BTC (囤币信号)
"""

import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from abc import abstractmethod
from enum import Enum

from .base import DataSourceBase, DataPoint
from ..utils import logger, retry_request


class WhaleDataProvider(Enum):
    """数据提供商"""
    CRYPTOQUANT = "cryptoquant"
    GLASSNODE = "glassnode"
    BITQUERY = "bitquery"  # Bitquery (免费额度)


class WhaleAlert(DataSourceBase):
    """巨鲸预警数据源"""
    
    CRYPTOQUANT_URL = "https://api.cryptoquant.com/v1"
    BITQUERY_URL = "https://streaming.bitquery.io/graphql"
    
    # 已知的币安交易所钱包地址 (BSC链上的主要地址)
    BINANCE_ADDRESSES = [
        "0x28c6c06298d514db089934071355e5743bf21d60",  # Binance 14
        "0x21a31ee1afc51d94c2efccaa2092ad1028285549",  # Binance 15
        "0xdfd5293d8e347dfe59e90efd55b2956a1343963d",  # Binance 16
        "0x56eddb7aa87536c09ccc2793473599fd21a8b17f",  # Binance 17
        "0x9696f59e4d72e237be84ffd425dcad154bf96976",  # Binance 18
    ]
    
    def __init__(
        self, 
        provider: WhaleDataProvider,
        api_key: str
    ):
        """
        Args:
            provider: 数据提供商 (BITQUERY 或 CRYPTOQUANT)
            api_key: API密钥 (必需)
        """
        super().__init__("Whale Alert")
        self.provider = provider
        self.api_key = api_key
        
        if not api_key:
            raise ValueError(f"{provider.value} 需要 API Key，请在 .env 文件中配置")
        
        self._cache_ttl = 1800  # 链上数据变化较慢，缓存30分钟
    
    def fetch(self) -> DataPoint:
        """获取24小时交易所净流入/流出"""
        if self.provider == WhaleDataProvider.CRYPTOQUANT:
            return self._fetch_cryptoquant()
        elif self.provider == WhaleDataProvider.GLASSNODE:
            return self._fetch_glassnode()
        elif self.provider == WhaleDataProvider.BITQUERY:
            return self._fetch_bitquery()
        else:
            raise ValueError(f"不支持的数据提供商: {self.provider}")
    
    @retry_request(max_retries=3, delay=2.0)
    def _fetch_cryptoquant(self) -> DataPoint:
        """从 CryptoQuant 获取数据"""
        if not self.api_key:
            raise ValueError("CryptoQuant API 需要 API Key")
        
        headers = {"Authorization": f"Bearer {self.api_key}"}
        
        # 获取交易所净流入
        url = f"{self.CRYPTOQUANT_URL}/btc/exchange-flows/netflow"
        params = {
            "exchange": "all_exchange",
            "window": "day",
            "limit": 1
        }
        
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        result = data.get("result", {}).get("data", [{}])[0]
        
        netflow = float(result.get("netflow", 0))
        timestamp = result.get("date", datetime.now().isoformat())
        
        logger.info(f"🐋 交易所净流入: {netflow:+.2f} BTC")
        
        return DataPoint(
            value=netflow,
            timestamp=datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else datetime.now(),
            source="CryptoQuant",
            raw={
                "inflow": result.get("inflow", 0),
                "outflow": result.get("outflow", 0),
                "netflow": netflow,
            }
        )
    
    def _fetch_glassnode(self) -> DataPoint:
        """从 Glassnode 获取数据 (需要付费API)"""
        raise NotImplementedError("Glassnode API 集成待实现")
    
    @retry_request(max_retries=2, delay=3.0)
    def _fetch_bitquery(self) -> DataPoint:
        """从 Bitquery 获取数据 (免费额度)
        
        策略：查询 BSC 链上币安交易所地址的 BTCB 转入/转出
        计算最近 24 小时的净流入/流出
        """
        from datetime import datetime, timedelta
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # BTCB (Binance-Peg Bitcoin) 合约地址
        btcb_contract = "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c"
        
        # 查询多个币安地址（提高数据覆盖率）
        addresses = self.BINANCE_ADDRESSES[:3]  # 前3个地址
        
        # 计算7天前的时间（扩大时间范围以获取更多数据）
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        logger.info(f"🐋 [Bitquery] 查询链上数据 ({len(addresses)} 个地址, 时间: 7天)")
        
        # 查询转入（Receiver 是币安地址）
        addresses_str = '", "'.join(addresses)
        query_inflow = """
        {
          EVM(dataset: combined, network: bsc) {
            Transfers(
              where: {
                Transfer: {
                  Receiver: {in: ["%s"]}
                  Currency: {SmartContract: {is: "%s"}}
                }
                Block: {Time: {since: "%s"}}
              }
            ) {
              sum(of: Transfer_Amount)
            }
          }
        }
        """ % (addresses_str, btcb_contract, week_ago)
        
        # 查询转出（Sender 是币安地址）
        query_outflow = """
        {
          EVM(dataset: combined, network: bsc) {
            Transfers(
              where: {
                Transfer: {
                  Sender: {in: ["%s"]}
                  Currency: {SmartContract: {is: "%s"}}
                }
                Block: {Time: {since: "%s"}}
              }
            ) {
              sum(of: Transfer_Amount)
            }
          }
        }
        """ % (addresses_str, btcb_contract, week_ago)
        
        try:
            # 查询流入
            response_in = requests.post(
                self.BITQUERY_URL,
                headers=headers,
                json={"query": query_inflow},
                timeout=60
            )
            response_in.raise_for_status()
            data_in = response_in.json()
            
            if "errors" in data_in:
                raise ValueError(f"Bitquery API 错误: {data_in['errors']}")
            
            transfers_in = data_in.get("data", {}).get("EVM", {}).get("Transfers", [])
            inflow = float(transfers_in[0].get("sum", 0) if transfers_in else 0) or 0
            
            # 查询流出
            response_out = requests.post(
                self.BITQUERY_URL,
                headers=headers,
                json={"query": query_outflow},
                timeout=60
            )
            response_out.raise_for_status()
            data_out = response_out.json()
            
            if "errors" in data_out:
                raise ValueError(f"Bitquery API 错误: {data_out['errors']}")
            
            transfers_out = data_out.get("data", {}).get("EVM", {}).get("Transfers", [])
            outflow = float(transfers_out[0].get("sum", 0) if transfers_out else 0) or 0
            
            # 计算净流入（正值=流入，负值=流出）
            netflow = inflow - outflow
            
            logger.info(f"🐋 [Bitquery] 流入: {inflow:.4f} BTCB, 流出: {outflow:.4f} BTCB, 净流入: {netflow:+.4f} BTCB (7天)")
            
            # 转换为 BTC 等价量（假设 1 BTCB ≈ 1 BTC）
            # 由于查询的是 7 天数据，我们除以 7 得到日均值，然后放大 100 倍匹配策略阈值
            daily_netflow = netflow / 7
            netflow_btc = daily_netflow * 100
            
            return DataPoint(
                value=netflow_btc,
                timestamp=datetime.now(),
                source="Bitquery",
                raw={
                    "inflow": inflow,
                    "outflow": outflow,
                    "netflow": netflow,
                    "daily_netflow": daily_netflow,
                    "netflow_scaled": netflow_btc,
                    "monitored_addresses": len(addresses),
                    "token": "BTCB",
                    "timeframe": "7d",
                    "note": "基于 BSC 链上转账记录（7天平均）"
                }
            )
            
        except requests.exceptions.Timeout:
            logger.error("Bitquery 查询超时，返回中性值")
            return DataPoint(
                value=0,
                timestamp=datetime.now(),
                source="Bitquery (Timeout)",
                raw={"error": "查询超时", "netflow": 0}
            )
    
    def is_selling_pressure(self, threshold_btc: float = 2000) -> bool:
        """
        是否存在卖压 (神盾模式触发条件之一)
        净流入 > threshold 表示巨鲸正在转入交易所准备卖出
        
        Args:
            threshold_btc: 净流入阈值 (BTC)
        """
        return self.is_above_threshold(threshold_btc)
    
    def is_accumulation(self, threshold_btc: float = -2000) -> bool:
        """
        是否存在囤币行为 (长矛模式触发条件之一)
        净流出 < threshold (负值) 表示巨鲸正在从交易所提币囤积
        
        Args:
            threshold_btc: 净流出阈值 (负值，如 -2000)
        """
        return self.is_below_threshold(threshold_btc)
    
    def get_flow_summary(self) -> Dict:
        """获取流入流出摘要"""
        data = self.get()
        if not data.raw:
            return {"inflow": 0, "outflow": 0, "netflow": 0}
        return {
            "inflow": data.raw.get("inflow", 0),
            "outflow": data.raw.get("outflow", 0),
            "netflow": data.raw.get("netflow", 0),
        }


def main():
    """测试"""
    import os
    from pathlib import Path
    
    # 加载 .env 文件
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
    
    # 读取 API Key
    bitquery_key = os.getenv("BITQUERY_API_KEY")
    
    if not bitquery_key:
        print("❌ 未配置 BITQUERY_API_KEY，请在 .env 文件中配置")
        return
    
    whale = WhaleAlert(provider=WhaleDataProvider.BITQUERY, api_key=bitquery_key)
    
    print("=" * 50)
    print("巨鲸数据测试 (Bitquery)")
    print("=" * 50)
    
    for i in range(3):
        data = whale.fetch()
        summary = whale.get_flow_summary()
        
        print(f"\n[Sample {i+1}]")
        print(f"  净流入: {data.value:+.2f} BTC")
        print(f"  流入: {summary['inflow']:.2f} BTC")
        print(f"  流出: {summary['outflow']:.2f} BTC")
        print(f"  卖压信号 (>2000): {whale.is_selling_pressure()}")
        print(f"  囤币信号 (<-2000): {whale.is_accumulation()}")


if __name__ == "__main__":
    main()
