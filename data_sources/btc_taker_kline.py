"""
BTC Taker 买卖量分析
- 基于 K线数据计算主动买入/卖出量
- 支持传入 klines 或自动获取
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TakerData:
    """Taker 买卖数据"""
    taker_buy_btc: float      # 主动买入量
    taker_sell_btc: float     # 主动卖出量
    total_volume_btc: float   # 总成交量
    buy_sell_ratio: float     # 买/卖比
    buy_ratio_pct: float      # 主动买入占比 (%)
    
    def to_dict(self) -> dict:
        return {
            "taker_buy_btc": self.taker_buy_btc,
            "taker_sell_btc": self.taker_sell_btc,
            "total_volume_btc": self.total_volume_btc,
            "buy_sell_ratio": self.buy_sell_ratio,
            "buy_ratio_pct": self.buy_ratio_pct,
        }


class TakerAnalyzer:
    """Taker 买卖量分析器"""
    
    def calculate(self, klines: List[List], periods: int = 1) -> Optional[TakerData]:
        """
        从 K线数据计算 Taker 买卖量
        
        Args:
            klines: K线数据列表，格式为 Binance 标准格式
                    [open_time, open, high, low, close, volume, ..., taker_buy_base]
            periods: 计算最近几根K线，默认1（最新一根）
        
        Returns:
            TakerData 或 None（数据不足时）
        """
        if not klines or len(klines) < periods:
            return None
        
        # 取最近 N 根 K线
        recent_klines = klines[-periods:]
        
        total_vol = 0.0
        taker_buy_vol = 0.0
        
        for k in recent_klines:
            total_vol += float(k[5])      # 成交量 (base asset)
            taker_buy_vol += float(k[9])  # Taker 主动买入量
        
        taker_sell_vol = total_vol - taker_buy_vol
        ratio = (taker_buy_vol / taker_sell_vol) if taker_sell_vol > 0 else float('inf')
        buy_pct = (taker_buy_vol / total_vol * 100) if total_vol > 0 else 0
        
        return TakerData(
            taker_buy_btc=taker_buy_vol,
            taker_sell_btc=taker_sell_vol,
            total_volume_btc=total_vol,
            buy_sell_ratio=ratio,
            buy_ratio_pct=buy_pct,
        )


# 模块级单例
taker_analyzer = TakerAnalyzer()


if __name__ == "__main__":
    from binance_utils import fetch_klines_sync
    
    klines = fetch_klines_sync("BTCUSDT", "4h", limit=10)
    data = taker_analyzer.calculate(klines, periods=1)
    print(data)
