"""BTC ETF 资金流入/流出数据源 (SoSoValue API)

追踪美国现货 BTC ETF 的每日净流入/流出，是判断机构资金动向的关键指标。

信号逻辑:
- 连续净流入 → 机构看多，支撑价格上涨
- 连续净流出 → 机构减仓，价格承压
- 单日大额流入 (>$500M) → 强烈买入信号
- 单日大额流出 (>$300M) → 短期回调风险
- 累计净流入创新高 → 长期趋势看涨

数据来源: SoSoValue 免费 API (https://sosovalue.com)
API 文档: https://sosovalue.gitbook.io/soso-value-api-doc/2.-etf/summary-history
"""

import os
import requests
from datetime import datetime
from typing import List, Dict, Optional

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from stock_btc.data_sources.base import DataSourceBase, DataPoint
from stock_btc.utils import logger, retry_request


class ETFFlow(DataSourceBase):
    """BTC ETF 资金流入/流出追踪"""

    BASE_URL = "https://openapi.sosovalue.com/openapi/v1"
    SUMMARY_URL = f"{BASE_URL}/etfs/summary-history"

    def __init__(self, symbol: str = "BTC", country_code: str = "US"):
        super().__init__("BTC ETF Flow")
        self.symbol = symbol
        self.country_code = country_code
        self.api_key = os.getenv("SOSOVALUE_API_KEY", "")
        self._cache_ttl = 1800  # ETF 数据日更，30 分钟缓存足够

    def _get_headers(self) -> Dict:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-soso-api-key"] = self.api_key
        return headers

    def _get_proxy(self) -> Optional[Dict]:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            return {"https": proxy, "http": proxy}
        return None

    @retry_request(max_retries=3, delay=2.0)
    def fetch(self) -> DataPoint:
        """获取最近 ETF 资金流数据并计算关键指标"""
        history = self._fetch_history(limit=30)

        if not history:
            logger.warning("ETF 数据为空，返回默认值")
            return DataPoint(
                value=0.0,
                timestamp=datetime.now(),
                source=self.name,
                raw={"error": "no_data"},
            )

        latest = history[0]  # 最新一天
        daily_flow = latest["total_net_inflow"]
        cum_flow = latest["cum_net_inflow"]
        total_assets = latest["total_net_assets"]
        volume = latest["total_value_traded"]
        date = latest["date"]

        # 计算多日趋势
        flow_3d = sum(d["total_net_inflow"] for d in history[:3]) if len(history) >= 3 else daily_flow
        flow_7d = sum(d["total_net_inflow"] for d in history[:7]) if len(history) >= 7 else flow_3d

        # 连续流入/流出天数
        streak = 0
        if daily_flow >= 0:
            for d in history:
                if d["total_net_inflow"] >= 0:
                    streak += 1
                else:
                    break
        else:
            for d in history:
                if d["total_net_inflow"] < 0:
                    streak -= 1
                else:
                    break

        logger.info(
            f"📊 ETF: {date} 净流入 ${daily_flow/1e6:+.1f}M "
            f"(3d: ${flow_3d/1e6:+.1f}M, 7d: ${flow_7d/1e6:+.1f}M) "
            f"累计: ${cum_flow/1e9:.2f}B, 连续{abs(streak)}天{'流入' if streak > 0 else '流出'}"
        )

        return DataPoint(
            value=daily_flow,
            timestamp=datetime.now(),
            source=self.name,
            raw={
                "date": date,
                "daily_flow_usd": daily_flow,
                "flow_3d_usd": flow_3d,
                "flow_7d_usd": flow_7d,
                "cum_flow_usd": cum_flow,
                "total_net_assets_usd": total_assets,
                "total_volume_usd": volume,
                "streak_days": streak,
            },
        )

    def _fetch_history(self, limit: int = 30) -> List[Dict]:
        """获取 ETF 历史资金流数据"""
        try:
            resp = requests.get(
                self.SUMMARY_URL,
                params={
                    "symbol": self.symbol,
                    "country_code": self.country_code,
                    "limit": limit,
                },
                headers=self._get_headers(),
                proxies=self._get_proxy(),
                verify=not bool(self._get_proxy()),
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, dict) and "data" in data:
                data = data["data"]

            if not isinstance(data, list):
                logger.warning(f"ETF API 返回格式异常: {type(data)}")
                return []

            return data
        except Exception as e:
            logger.warning(f"获取 ETF 历史数据失败: {e}")
            return []

    @retry_request(max_retries=3, delay=2.0)
    def fetch_history(self, limit: int = 30) -> List[Dict]:
        """获取 ETF 历史资金流 (格式化，供 API/图表使用)"""
        raw = self._fetch_history(limit)
        return [
            {
                "date": item["date"],
                "daily_flow": item["total_net_inflow"],
                "cum_flow": item["cum_net_inflow"],
                "total_assets": item["total_net_assets"],
                "volume": item["total_value_traded"],
            }
            for item in raw
        ]

    def is_strong_inflow(self, threshold_usd: float = 200e6) -> bool:
        """是否出现大额净流入"""
        data = self.get()
        return (data.raw.get("daily_flow_usd", 0) >= threshold_usd) if data.raw else False

    def is_strong_outflow(self, threshold_usd: float = -200e6) -> bool:
        """是否出现大额净流出"""
        data = self.get()
        return (data.raw.get("daily_flow_usd", 0) <= threshold_usd) if data.raw else False

    def get_streak(self) -> int:
        """获取连续流入/流出天数 (正=流入, 负=流出)"""
        data = self.get()
        return data.raw.get("streak_days", 0) if data.raw else 0


def main():
    """测试"""
    etf = ETFFlow(symbol="BTC", country_code="US")

    print("=== BTC ETF 资金流 ===")
    data = etf.fetch()
    if data.raw.get("error"):
        print(f"获取数据失败: {data.raw['error']}")
        print("请设置 SOSOVALUE_API_KEY 环境变量")
        return

    raw = data.raw
    print(f"日期: {raw['date']}")
    print(f"当日净流入: ${raw['daily_flow_usd']/1e6:+.1f}M")
    print(f"3日净流入: ${raw['flow_3d_usd']/1e6:+.1f}M")
    print(f"7日净流入: ${raw['flow_7d_usd']/1e6:+.1f}M")
    print(f"累计净流入: ${raw['cum_flow_usd']/1e9:.2f}B")
    print(f"ETF总资产: ${raw['total_net_assets_usd']/1e9:.2f}B")
    print(f"连续{'流入' if raw['streak_days'] > 0 else '流出'}: {abs(raw['streak_days'])}天")
    print(f"大额流入: {etf.is_strong_inflow()}")
    print(f"大额流出: {etf.is_strong_outflow()}")

    print("\n=== 历史数据 (最近5天) ===")
    for h in etf.fetch_history(5):
        print(f"  {h['date']}: 净流入 ${h['daily_flow']/1e6:+.1f}M / 累计 ${h['cum_flow']/1e9:.2f}B")


if __name__ == "__main__":
    main()
