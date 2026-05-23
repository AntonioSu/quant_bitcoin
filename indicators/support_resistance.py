"""Support / resistance level detection from recent OHLCV candles.

The calculator finds local swing highs/lows, clusters nearby prices, then scores
levels by touches, recency, volume, and distance from the current price.
"""

from dataclasses import dataclass
from typing import List, Optional

from utils import logger


@dataclass
class PriceLevel:
    """Single support or resistance level."""

    price: float
    strength: float
    touches: int
    distance_pct: float
    last_touch_index: int

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "strength": self.strength,
            "touches": self.touches,
            "distance_pct": self.distance_pct,
            "last_touch_index": self.last_touch_index,
        }


@dataclass
class SupportResistanceResult:
    """Support/resistance analysis result."""

    current_price: float
    nearest_support: Optional[PriceLevel]
    nearest_resistance: Optional[PriceLevel]
    support_levels: List[PriceLevel]
    resistance_levels: List[PriceLevel]
    tolerance: float
    timeframe: str

    def to_dict(self) -> dict:
        return {
            "current_price": self.current_price,
            "nearest_support": self.nearest_support.to_dict() if self.nearest_support else None,
            "nearest_resistance": self.nearest_resistance.to_dict() if self.nearest_resistance else None,
            "support_levels": [level.to_dict() for level in self.support_levels],
            "resistance_levels": [level.to_dict() for level in self.resistance_levels],
            "tolerance": self.tolerance,
            "timeframe": self.timeframe,
        }


class SupportResistanceCalculator:
    """Detect nearby support/resistance levels from candle pivots."""

    def __init__(
        self,
        lookback: int = 80,
        pivot_window: int = 2,
        tolerance_pct: float = 0.006,
        range_multiplier: float = 0.45,
        max_levels: int = 4,
        timeframe: str = "4h",
    ):
        self.lookback = lookback
        self.pivot_window = pivot_window
        self.tolerance_pct = tolerance_pct
        self.range_multiplier = range_multiplier
        self.max_levels = max_levels
        self.timeframe = timeframe

    def _calc_tolerance(self, klines: List[List], current_price: float) -> float:
        recent = klines[-min(len(klines), 14):]
        avg_range = sum(float(k[2]) - float(k[3]) for k in recent) / len(recent)
        return max(current_price * self.tolerance_pct, avg_range * self.range_multiplier)

    def _find_pivots(self, klines: List[List], kind: str) -> List[dict]:
        pivots = []
        for i in range(self.pivot_window, len(klines) - self.pivot_window):
            window = klines[i - self.pivot_window:i + self.pivot_window + 1]
            if kind == "support":
                price = float(klines[i][3])
                if price != min(float(k[3]) for k in window):
                    continue
            else:
                price = float(klines[i][2])
                if price != max(float(k[2]) for k in window):
                    continue
            pivots.append({
                "price": price,
                "index": i,
                "volume": float(klines[i][5]) if len(klines[i]) > 5 else 0.0,
            })
        return pivots

    def _cluster_pivots(
        self,
        pivots: List[dict],
        current_price: float,
        tolerance: float,
        avg_volume: float,
    ) -> List[PriceLevel]:
        clusters = []
        for pivot in sorted(pivots, key=lambda p: p["price"]):
            cluster = next(
                (
                    c for c in clusters
                    if abs(c["price"] - pivot["price"]) <= tolerance
                ),
                None,
            )
            if cluster is None:
                clusters.append({
                    "price": pivot["price"],
                    "touches": 1,
                    "last_touch_index": pivot["index"],
                    "volume": pivot["volume"],
                })
                continue

            touches = cluster["touches"] + 1
            cluster["price"] = (cluster["price"] * cluster["touches"] + pivot["price"]) / touches
            cluster["touches"] = touches
            cluster["last_touch_index"] = max(cluster["last_touch_index"], pivot["index"])
            cluster["volume"] += pivot["volume"]

        levels = []
        last_index = max((p["index"] for p in pivots), default=1)
        for cluster in clusters:
            distance_pct = (cluster["price"] - current_price) / current_price
            touch_score = min(cluster["touches"] / 4.0, 1.0) * 0.45
            recency_score = (cluster["last_touch_index"] / max(last_index, 1)) * 0.25
            avg_cluster_volume = cluster["volume"] / cluster["touches"]
            volume_score = min(avg_cluster_volume / max(avg_volume, 1.0), 2.0) / 2.0 * 0.15
            distance_score = max(0.0, 1.0 - abs(distance_pct) / 0.08) * 0.15
            strength = min(touch_score + recency_score + volume_score + distance_score, 1.0)
            levels.append(PriceLevel(
                price=cluster["price"],
                strength=round(strength, 3),
                touches=cluster["touches"],
                distance_pct=round(distance_pct * 100, 2),
                last_touch_index=cluster["last_touch_index"],
            ))
        return levels

    def calculate(self, klines: List[List]) -> SupportResistanceResult:
        min_required = self.pivot_window * 2 + 10
        if len(klines) < min_required:
            raise ValueError(f"需要至少 {min_required} 根K线，当前只有 {len(klines)} 根")

        recent = klines[-self.lookback:]
        current_price = float(recent[-1][4])
        tolerance = self._calc_tolerance(recent, current_price)
        volumes = [float(k[5]) for k in recent if len(k) > 5]
        avg_volume = sum(volumes) / len(volumes) if volumes else 1.0

        support_pivots = self._find_pivots(recent, "support")
        resistance_pivots = self._find_pivots(recent, "resistance")

        supports = [
            level for level in self._cluster_pivots(
                support_pivots, current_price, tolerance, avg_volume
            )
            if level.price < current_price
        ]
        resistances = [
            level for level in self._cluster_pivots(
                resistance_pivots, current_price, tolerance, avg_volume
            )
            if level.price > current_price
        ]

        supports.sort(key=lambda level: (-level.strength, abs(level.distance_pct)))
        resistances.sort(key=lambda level: (-level.strength, abs(level.distance_pct)))
        top_supports = supports[:self.max_levels]
        top_resistances = resistances[:self.max_levels]

        nearest_support = max(supports, key=lambda level: level.price, default=None)
        nearest_resistance = min(resistances, key=lambda level: level.price, default=None)

        logger.debug(
            "📐 支撑/压力计算完成: "
            f"support={nearest_support.price if nearest_support else None}, "
            f"resistance={nearest_resistance.price if nearest_resistance else None}"
        )

        return SupportResistanceResult(
            current_price=current_price,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
            support_levels=top_supports,
            resistance_levels=top_resistances,
            tolerance=tolerance,
            timeframe=self.timeframe,
        )
