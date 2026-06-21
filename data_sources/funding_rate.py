"""Binance 资金费率数据源

资金费率说明:
- 正费率: 多头支付空头 (市场看多情绪强)
- 负费率: 空头支付多头 (市场看空情绪强)
- 每8小时结算一次 (00:00, 08:00, 16:00 UTC)

做空模式关注:
- 高正费率 (>=0.02%) 表示做空有利可图
- 年化收益 = 费率 × 3 × 365 = 0.02% × 1095 ≈ 21.9%
"""

import requests
from datetime import datetime
from typing import Optional, List, Dict

from data_sources.base import DataSourceBase, DataPoint
from utils import logger, retry_request


class FundingRate(DataSourceBase):
    """币安合约资金费率"""
    
    # 币本位合约 API
    COIN_M_URL = "https://dapi.binance.com/dapi/v1/premiumIndex"
    COIN_M_HISTORY_URL = "https://dapi.binance.com/dapi/v1/fundingRate"
    
    # U本位合约 API
    USDT_M_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
    USDT_M_HISTORY_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
    
    def __init__(self, symbol: str = "BTCUSDT"):
        """
        Args:
            symbol: 合约符号
                - 币本位: BTCUSD_PERP, ETHUSD_PERP
                - U本位: BTCUSDT, ETHUSDT (默认使用)
        """
        super().__init__("Binance Funding Rate")
        self.symbol = symbol
        # 自动判断合约类型：包含 USD_PERP 或 USD 结尾的是币本位，其他是U本位
        self.use_coin_m = symbol.endswith("USD_PERP") or (symbol.endswith("USD") and not symbol.endswith("USDT"))
        self._cache_ttl = 60  # 费率每8小时更新，但预测值实时变化，缓存1分钟
    
    @retry_request(max_retries=3, delay=1.0)
    def fetch(self) -> DataPoint:
        """获取最近一次已结算的资金费率 + 实时预测费率

        premiumIndex 的 lastFundingRate 是实时预测值（可能为负），
        不代表上次结算费率。这里改用 fundingRate 历史接口获取最近一次
        已结算费率作为主值，premiumIndex 仅用于获取预测值和标记价。
        """
        # 1) 从历史接口拿最近一次已结算费率
        history_url = self.COIN_M_HISTORY_URL if self.use_coin_m else self.USDT_M_HISTORY_URL
        resp_h = requests.get(
            history_url, params={"symbol": self.symbol, "limit": 1}, timeout=10
        )
        resp_h.raise_for_status()
        history_data = resp_h.json()
        if isinstance(history_data, list) and history_data:
            last_rate = float(history_data[-1]["fundingRate"])
        else:
            last_rate = 0.0

        # 2) 从 premiumIndex 拿预测费率和标记价
        premium_url = self.COIN_M_URL if self.use_coin_m else self.USDT_M_URL
        try:
            resp_p = requests.get(
                premium_url, params={"symbol": self.symbol}, timeout=10
            )
            resp_p.raise_for_status()
            pdata = resp_p.json()
            if isinstance(pdata, list):
                pdata = pdata[0]
            predicted_rate = float(pdata.get("lastFundingRate", 0))
            next_funding_time = int(pdata.get("nextFundingTime", 0))
            mark_price = float(pdata.get("markPrice", 0))
            index_price = float(pdata.get("indexPrice", 0))
        except Exception:
            predicted_rate = last_rate
            next_funding_time = 0
            mark_price = 0.0
            index_price = 0.0

        logger.info(
            f"💰 资金费率: {last_rate:.5%} (预测下期: {predicted_rate:.5%})"
        )

        return DataPoint(
            value=last_rate * 100,  # 转换为百分比 (0.0003 -> 0.03%)
            timestamp=datetime.now(),
            source=self.name,
            raw={
                "last_rate": last_rate,
                "predicted_rate": predicted_rate,
                "next_funding_time": next_funding_time,
                "mark_price": mark_price,
                "index_price": index_price,
                "annual_yield": last_rate * 3 * 365,
            }
        )
    
    @retry_request(max_retries=3, delay=1.0)
    def fetch_history(self, limit: int = 100) -> List[Dict]:
        """获取历史资金费率"""
        url = self.COIN_M_HISTORY_URL if self.use_coin_m else self.USDT_M_HISTORY_URL
        
        response = requests.get(
            url, 
            params={"symbol": self.symbol, "limit": limit},
            timeout=10
        )
        response.raise_for_status()
        
        history = []
        for item in response.json():
            history.append({
                "time": datetime.fromtimestamp(int(item["fundingTime"]) / 1000),
                "rate": float(item["fundingRate"]),
                "rate_pct": float(item["fundingRate"]) * 100,
            })
        
        return history
    
    def get_annual_yield(self) -> float:
        """获取年化收益率"""
        data = self.get()
        return data.raw.get("annual_yield", 0) if data.raw else 0
    
    def is_profitable_short(self, threshold_pct: float = 0.02) -> bool:
        """
        是否适合做空收租 (做空模式触发条件之一)
        
        Args:
            threshold_pct: 费率阈值 (百分比，如 0.02 表示 0.02%)
        """
        return self.is_above_threshold(threshold_pct)
    
    def get_next_settlement(self) -> Optional[datetime]:
        """获取下次结算时间"""
        data = self.get()
        if data.raw and data.raw.get("next_funding_time"):
            return datetime.fromtimestamp(data.raw["next_funding_time"] / 1000)
        return None


def main():
    """测试"""
    # 自动识别合约类型
    print("=== U本位合约 (BTCUSDT) ===")
    fr_usdt = FundingRate(symbol='BTCUSDT')
    print(f"合约类型: {'币本位' if fr_usdt.use_coin_m else 'U本位'}")
    data = fr_usdt.fetch()
    print(f"Last Rate: {data.value:.5f}%")
    print(f"Annual Yield: {fr_usdt.get_annual_yield():.2%}")
    print(f"Next Settlement: {fr_usdt.get_next_settlement()}")
    print("\n历史费率 (最近5次):")
    for h in fr_usdt.fetch_history(5):
        print(f"  {h['time']}: {h['rate_pct']:.5f}%")
    
    print("\n=== 币本位合约 (BTCUSD_PERP) ===")
    fr_coin = FundingRate(symbol='BTCUSD_PERP')
    print(f"合约类型: {'币本位' if fr_coin.use_coin_m else 'U本位'}")
    data = fr_coin.fetch()
    print(f"Last Rate: {data.value:.5f}%")
    print(f"Annual Yield: {fr_coin.get_annual_yield():.2%}")
    print(f"Profitable Short (>=0.02%): {fr_coin.is_profitable_short()}")
    print("\n历史费率 (最近5次):")
    for h in fr_coin.fetch_history(5):
        print(f"  {h['time']}: {h['rate_pct']:.5f}%")


if __name__ == "__main__":
    main()
