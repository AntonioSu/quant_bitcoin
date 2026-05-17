"""研判记忆存储

每次 AI 研判 + 对应交易结果配对持久化，供 Reflector / StrategySummarizer 使用。

存储格式 (JSON Lines):
{
    "id": "uuid",
    "time": "ISO",
    "bias": "LONG",
    "confidence": 75,
    "summary": "...",
    "key_drivers": [...],
    "snapshot_digest": { ... },   # 压缩版市场快照
    "trade_result": null | { ... } # 平仓后回填
    "reflection": null | { ... }   # 复盘后回填
}
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional

from ..utils import logger

_DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
)


class AnalysisMemory:
    """研判 + 交易结果配对存储"""

    MAX_RECORDS = 500

    def __init__(self, data_dir: Optional[str] = None):
        data_dir = data_dir or _DEFAULT_DIR
        os.makedirs(data_dir, exist_ok=True)
        self._filepath = os.path.join(data_dir, "analysis_memory.json")
        self._lock = Lock()
        self._records: List[Dict[str, Any]] = []
        self._load()

    # ── 写入 ──

    def save_analysis(self, analysis: Dict[str, Any],
                      snapshot_digest: Optional[Dict] = None) -> str:
        """研判产生时保存，返回 record_id"""
        record_id = uuid.uuid4().hex[:12]
        record = {
            "id": record_id,
            "time": datetime.now().isoformat(),
            "bias": analysis.get("bias"),
            "confidence": analysis.get("confidence"),
            "summary": analysis.get("summary"),
            "key_drivers": analysis.get("key_drivers", []),
            "risks": analysis.get("risks", []),
            "snapshot_digest": snapshot_digest,
            "trade_result": None,
            "reflection": None,
        }
        with self._lock:
            self._records.append(record)
            self._trim()
            self._save()
        logger.debug(f"📝 研判记忆已保存: {record_id}")
        return record_id

    def attach_trade_result(self, record_id: str, trade: Dict[str, Any]) -> bool:
        """平仓时把交易结果关联到最近的研判记录"""
        with self._lock:
            rec = self._find(record_id)
            if not rec:
                rec = self._find_latest_unlinked(trade.get("mode"))
            if not rec:
                return False
            rec["trade_result"] = {
                "action": trade.get("action"),
                "pnl": trade.get("pnl", 0),
                "entry_price": trade.get("entry_price"),
                "exit_price": trade.get("price"),
                "exit_time": trade.get("time"),
                "trigger_reason": trade.get("trigger_reason"),
                "hold_minutes": self._calc_hold_minutes(rec["time"], trade.get("time")),
            }
            self._save()
        return True

    def attach_reflection(self, record_id: str, reflection: Dict[str, Any]):
        """复盘后回填"""
        with self._lock:
            rec = self._find(record_id)
            if rec:
                rec["reflection"] = reflection
                self._save()

    # ── 查询 ──

    def get_latest_analysis_id(self) -> Optional[str]:
        """最近一次研判的 id"""
        with self._lock:
            return self._records[-1]["id"] if self._records else None

    def get_unreflected(self, limit: int = 10) -> List[Dict]:
        """有交易结果但还没复盘的记录"""
        with self._lock:
            return [
                r for r in self._records
                if r.get("trade_result") and not r.get("reflection")
            ][-limit:]

    def get_recent_with_results(self, n: int = 3) -> List[Dict]:
        """最近 n 条有交易结果的记录（含复盘）"""
        with self._lock:
            matched = [r for r in self._records if r.get("trade_result")]
            return matched[-n:]

    def get_all_reflections(self, since_days: int = 30) -> List[Dict]:
        """获取所有复盘记录"""
        cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
        with self._lock:
            return [
                r for r in self._records
                if r.get("reflection") and r["time"] >= cutoff
            ]

    def get_record(self, record_id: str) -> Optional[Dict]:
        with self._lock:
            return self._find(record_id)

    # ── 内部方法 ──

    def _find(self, record_id: str) -> Optional[Dict]:
        for r in reversed(self._records):
            if r["id"] == record_id:
                return r
        return None

    def _find_latest_unlinked(self, mode: Optional[str] = None) -> Optional[Dict]:
        """找最近一条没关联交易结果的记录"""
        for r in reversed(self._records):
            if r.get("trade_result"):
                continue
            if mode and r.get("bias") and r["bias"] != mode:
                continue
            return r
        return None

    @staticmethod
    def _calc_hold_minutes(open_time_iso: str, close_time_str: Optional[str]) -> float:
        if not close_time_str:
            return 0
        try:
            t_open = datetime.fromisoformat(open_time_iso)
            t_close = datetime.strptime(close_time_str, "%Y-%m-%d %H:%M:%S")
            return round((t_close - t_open).total_seconds() / 60, 1)
        except Exception:
            return 0

    def _trim(self):
        if len(self._records) > self.MAX_RECORDS:
            self._records = self._records[-self.MAX_RECORDS:]

    def _load(self):
        if not os.path.exists(self._filepath):
            return
        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                self._records = json.load(f)
            logger.debug(f"📝 加载研判记忆: {len(self._records)} 条")
        except Exception as e:
            logger.error(f"加载研判记忆失败: {e}")
            self._records = []

    def _save(self):
        try:
            with open(self._filepath, "w", encoding="utf-8") as f:
                json.dump(self._records, f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.error(f"保存研判记忆失败: {e}")
