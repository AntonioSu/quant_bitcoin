"""状态持久化存储

使用 JSON 文件保存交易状态，服务重启后自动恢复
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional
from threading import Lock

from stock_btc.utils import logger


class StateStore:
    """JSON 状态存储"""
    
    def __init__(self, filepath: Optional[str] = None):
        if filepath is None:
            filepath = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data",
                "trading_state.json"
            )
        
        self.filepath = filepath
        self._lock = Lock()
        
        # 确保目录存在
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    def save(self, state: Dict[str, Any]) -> bool:
        """保存状态到文件"""
        with self._lock:
            try:
                # 添加保存时间
                state["_saved_at"] = datetime.now().isoformat()
                
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2, ensure_ascii=False)
                
                logger.debug(f"状态已保存: {self.filepath}")
                return True
            except Exception as e:
                logger.error(f"保存状态失败: {e}")
                return False
    
    def load(self) -> Optional[Dict[str, Any]]:
        """从文件加载状态"""
        with self._lock:
            if not os.path.exists(self.filepath):
                logger.info("状态文件不存在，使用初始状态")
                return None
            
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    state = json.load(f)
                
                saved_at = state.get("_saved_at", "未知")
                logger.info(f"状态已恢复 (保存于: {saved_at})")
                return state
            except Exception as e:
                logger.error(f"加载状态失败: {e}")
                return None
    
    def clear(self) -> bool:
        """清除状态文件"""
        with self._lock:
            try:
                if os.path.exists(self.filepath):
                    os.remove(self.filepath)
                    logger.info("状态已清除")
                return True
            except Exception as e:
                logger.error(f"清除状态失败: {e}")
                return False
