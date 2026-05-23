"""BTC 期权数据源 (Put/Call Ratio + Max Pain)

Put/Call Ratio:
- > 1.0: 看跌情绪主导，但可能是反向指标(过度恐慌=底部)
- < 0.7: 看涨情绪主导，可能过热
- 0.7~1.0: 中性

Max Pain (最大痛点):
- 期权到期时让最多持仓者亏损的价格
- 价格倾向于在到期前回归 Max Pain 附近
- 当前价格远高于 Max Pain → 回调风险
- 当前价格远低于 Max Pain → 反弹机会

数据源: Deribit 公开 API (无需认证)
https://docs.deribit.com/
"""

import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

from data_sources.base import DataSourceBase, DataPoint
from utils import logger, retry_request


class OptionsData(DataSourceBase):
    """BTC期权数据 (Put/Call Ratio + Max Pain)"""

    DERIBIT_URL = "https://www.deribit.com/api/v2/public"

    def __init__(self):
        super().__init__("BTC Options")
        self._cache_ttl = 1800  # 期权数据半小时刷新

    @retry_request(max_retries=3, delay=2.0)
    def fetch(self) -> DataPoint:
        """获取期权综合数据"""
        pc_ratio, pc_details = self._fetch_put_call_ratio()
        max_pain, mp_details = self._fetch_max_pain()
        btc_price = self._get_btc_index_price()

        price_vs_maxpain_pct = 0
        if max_pain and btc_price:
            price_vs_maxpain_pct = ((btc_price - max_pain) / max_pain) * 100

        signal = self._compute_signal(pc_ratio, price_vs_maxpain_pct)

        logger.info(
            f"📊 期权数据 - P/C Ratio: {pc_ratio:.3f}, "
            f"Max Pain: ${max_pain:,.0f}, "
            f"BTC vs MaxPain: {price_vs_maxpain_pct:+.1f}%"
        )

        return DataPoint(
            value=pc_ratio,
            timestamp=datetime.now(),
            source="Deribit",
            raw={
                "put_call_ratio": pc_ratio,
                "put_volume": pc_details.get("put_volume", 0),
                "call_volume": pc_details.get("call_volume", 0),
                "put_oi": pc_details.get("put_oi", 0),
                "call_oi": pc_details.get("call_oi", 0),
                "max_pain": max_pain,
                "btc_price": btc_price,
                "price_vs_maxpain_pct": round(price_vs_maxpain_pct, 2),
                "nearest_expiry": mp_details.get("expiry"),
                "signal": signal,
            },
        )

    def _fetch_put_call_ratio(self) -> Tuple[float, Dict]:
        """计算 Put/Call Ratio (基于持仓量)"""
        url = f"{self.DERIBIT_URL}/get_book_summary_by_currency"
        params = {"currency": "BTC", "kind": "option"}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        instruments = data.get("result", [])
        if not instruments:
            return 1.0, {}

        total_put_oi = 0
        total_call_oi = 0
        total_put_vol = 0
        total_call_vol = 0

        for inst in instruments:
            name = inst.get("instrument_name", "")
            oi = float(inst.get("open_interest", 0))
            vol = float(inst.get("volume", 0))

            if "-P" in name:
                total_put_oi += oi
                total_put_vol += vol
            elif "-C" in name:
                total_call_oi += oi
                total_call_vol += vol

        pc_ratio_oi = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0

        return pc_ratio_oi, {
            "put_oi": total_put_oi,
            "call_oi": total_call_oi,
            "put_volume": total_put_vol,
            "call_volume": total_call_vol,
        }

    def _fetch_max_pain(self) -> Tuple[float, Dict]:
        """计算最近到期期权的 Max Pain"""
        expiry, instruments = self._get_nearest_expiry_instruments()
        if not instruments:
            return 0, {}

        strikes = self._collect_strikes(instruments)
        max_pain = self._calculate_max_pain(strikes)

        return max_pain, {"expiry": expiry, "num_strikes": len(strikes)}

    def _get_nearest_expiry_instruments(self) -> Tuple[str, List]:
        """获取最近到期日的期权合约"""
        url = f"{self.DERIBIT_URL}/get_instruments"
        params = {"currency": "BTC", "kind": "option", "expired": "false"}

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        instruments = data.get("result", [])
        if not instruments:
            return "", []

        now_ts = int(datetime.now().timestamp() * 1000)
        future_instruments = [
            i for i in instruments
            if i.get("expiration_timestamp", 0) > now_ts
        ]

        if not future_instruments:
            return "", []

        future_instruments.sort(key=lambda x: x["expiration_timestamp"])
        nearest_ts = future_instruments[0]["expiration_timestamp"]

        nearest_expiry_dt = datetime.fromtimestamp(nearest_ts / 1000)
        if (nearest_expiry_dt - datetime.now()).days < 1:
            unique_expiries = sorted(set(
                i["expiration_timestamp"] for i in future_instruments
            ))
            if len(unique_expiries) > 1:
                nearest_ts = unique_expiries[1]

        nearest = [
            i for i in future_instruments
            if i["expiration_timestamp"] == nearest_ts
        ]

        expiry_str = datetime.fromtimestamp(nearest_ts / 1000).strftime("%Y-%m-%d")
        return expiry_str, nearest

    def _collect_strikes(self, instruments: List) -> Dict[float, Dict]:
        """收集各行权价的持仓量"""
        strikes: Dict[float, Dict] = {}

        for inst in instruments:
            strike = float(inst.get("strike", 0))
            name = inst.get("instrument_name", "")
            oi = float(inst.get("open_interest", 0))

            if strike not in strikes:
                strikes[strike] = {"call_oi": 0, "put_oi": 0}

            if "-C" in name:
                strikes[strike]["call_oi"] += oi
            elif "-P" in name:
                strikes[strike]["put_oi"] += oi

        return strikes

    @staticmethod
    def _calculate_max_pain(strikes: Dict[float, Dict]) -> float:
        """
        Max Pain = 让所有期权持仓总亏损最大的价格
        即对于每个候选到期价格，计算所有 put/call 的内在价值总和，
        选择使总和最小的价格点
        """
        if not strikes:
            return 0

        strike_prices = sorted(strikes.keys())
        min_pain = float("inf")
        max_pain_strike = 0

        for candidate in strike_prices:
            total_pain = 0
            for strike, oi in strikes.items():
                # Call: ITM 当 candidate > strike
                if candidate > strike:
                    total_pain += (candidate - strike) * oi["call_oi"]
                # Put: ITM 当 candidate < strike
                if candidate < strike:
                    total_pain += (strike - candidate) * oi["put_oi"]

            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = candidate

        return max_pain_strike

    def _get_btc_index_price(self) -> float:
        """获取 BTC 指数价格"""
        url = f"{self.DERIBIT_URL}/get_index_price"
        params = {"index_name": "btc_usd"}

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return float(data.get("result", {}).get("index_price", 0))
        except Exception:
            return 0

    @staticmethod
    def _compute_signal(pc_ratio: float, price_vs_maxpain_pct: float) -> str:
        """综合解读期权信号"""
        if pc_ratio > 1.2 and price_vs_maxpain_pct < -5:
            return "contrarian_bullish"  # 恐慌过度 + 价格低于MaxPain
        elif pc_ratio < 0.6 and price_vs_maxpain_pct > 5:
            return "contrarian_bearish"  # 贪婪过度 + 价格高于MaxPain
        elif price_vs_maxpain_pct > 8:
            return "overextended_above"  # 远高于MaxPain，回调风险
        elif price_vs_maxpain_pct < -8:
            return "overextended_below"  # 远低于MaxPain，反弹机会
        elif pc_ratio > 1.0:
            return "mildly_bearish"
        elif pc_ratio < 0.7:
            return "mildly_bullish"
        return "neutral"


def main():
    """测试"""
    options = OptionsData()
    data = options.fetch()
    print(f"Put/Call Ratio: {data.value:.3f}")
    print(f"Max Pain: ${data.raw.get('max_pain', 0):,.0f}")
    print(f"BTC vs MaxPain: {data.raw.get('price_vs_maxpain_pct', 0):+.1f}%")
    print(f"Signal: {data.raw.get('signal')}")


if __name__ == "__main__":
    main()
