"""历史数据持久化存储

保存指标历史数据到本地 JSON 文件，支持查询历史记录
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from threading import Lock
from collections import defaultdict

from stock_btc.utils import logger

# 历史数据存储
class HistoryStore:
    """历史数据存储"""
    
    # 数据类型
    FEAR_GREED = "fear_greed"
    FUNDING_RATE = "funding_rate"
    TOP_TRADER_RATIO = "top_trader_ratio"
    BTC_PRICE = "btc_price"
    
    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            data_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data",
                "history"
            )
        
        self.data_dir = data_dir
        self._lock = Lock()
        self._cache: Dict[str, List[Dict]] = defaultdict(list)
        
        # 确保目录存在
        os.makedirs(data_dir, exist_ok=True)
        
        # 加载现有数据到缓存
        self._load_all()
    
    def _get_filepath(self, data_type: str) -> str:
        """获取数据文件路径"""
        return os.path.join(self.data_dir, f"{data_type}.json")
    
    def _load_all(self):
        """加载所有历史数据到缓存"""
        for data_type in [self.FEAR_GREED, self.FUNDING_RATE, self.TOP_TRADER_RATIO, self.BTC_PRICE]:
            filepath = self._get_filepath(data_type)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        self._cache[data_type] = json.load(f)
                    logger.debug(f"加载历史数据: {data_type} ({len(self._cache[data_type])}条)")
                except Exception as e:
                    logger.error(f"加载 {data_type} 失败: {e}")
                    self._cache[data_type] = []
    
    def _save(self, data_type: str):
        """保存数据到文件"""
        filepath = self._get_filepath(data_type)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self._cache[data_type], f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存 {data_type} 失败: {e}")
    
    def add(self, data_type: str, value: Any, timestamp: Optional[datetime] = None, extra: Optional[Dict] = None):
        """
        添加一条记录
        
        Args:
            data_type: 数据类型 (fear_greed, funding_rate, whale_netflow, btc_price)
            value: 数值
            timestamp: 时间戳 (默认当前时间)
            extra: 额外数据
        """
        with self._lock:
            if timestamp is None:
                timestamp = datetime.now()
            
            record = {
                "timestamp": timestamp.isoformat(),
                "value": value,
            }
            if extra:
                record["extra"] = extra
            
            # 检查是否重复 (同一分钟内不重复记录)
            ts_minute = timestamp.strftime("%Y-%m-%d %H:%M")
            existing = self._cache[data_type]
            if existing:
                last_ts = existing[-1].get("timestamp", "")
                if last_ts.startswith(ts_minute[:16]):  # 同一分钟
                    return
            
            self._cache[data_type].append(record)
            
            # 限制最大记录数 (保留最近10000条)
            max_records = 10000
            if len(self._cache[data_type]) > max_records:
                self._cache[data_type] = self._cache[data_type][-max_records:]
            
            # 保存到文件
            self._save(data_type)
    
    def get_history(
        self, 
        data_type: str, 
        limit: int = 100,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None
    ) -> List[Dict]:
        """
        获取历史数据
        
        Args:
            data_type: 数据类型
            limit: 返回条数
            since: 开始时间
            until: 结束时间
        """
        with self._lock:
            records = self._cache.get(data_type, [])
            
            # 时间过滤
            if since or until:
                filtered = []
                for r in records:
                    ts = datetime.fromisoformat(r["timestamp"])
                    if since and ts < since:
                        continue
                    if until and ts > until:
                        continue
                    filtered.append(r)
                records = filtered
            
            # 返回最近的 limit 条
            return records[-limit:]
    
    def get_latest(self, data_type: str) -> Optional[Dict]:
        """获取最新一条记录"""
        with self._lock:
            records = self._cache.get(data_type, [])
            return records[-1] if records else None
    
    def get_stats(self, data_type: str, days: int = 7) -> Dict:
        """
        获取统计信息
        
        Args:
            data_type: 数据类型
            days: 统计天数
        """
        since = datetime.now() - timedelta(days=days)
        records = self.get_history(data_type, limit=10000, since=since)
        
        if not records:
            return {"count": 0, "min": None, "max": None, "avg": None}
        
        values = [r["value"] for r in records if r.get("value") is not None]
        
        if not values:
            return {"count": 0, "min": None, "max": None, "avg": None}
        
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "first_time": records[0]["timestamp"],
            "last_time": records[-1]["timestamp"],
        }
    
    def clear(self, data_type: Optional[str] = None):
        """清除历史数据"""
        with self._lock:
            if data_type:
                self._cache[data_type] = []
                self._save(data_type)
                logger.info(f"已清除 {data_type} 历史数据")
            else:
                for dt in [self.FEAR_GREED, self.FUNDING_RATE, self.TOP_TRADER_RATIO, self.BTC_PRICE]:
                    self._cache[dt] = []
                    self._save(dt)
                logger.info("已清除所有历史数据")


# 默认实例
history_store = HistoryStore()
