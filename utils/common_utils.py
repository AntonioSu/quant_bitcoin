"""通用工具函数"""

import time
from datetime import datetime
from functools import wraps
from typing import Callable, Any

from utils.log_util import logger


def retry_request(max_retries: int = 3, delay: float = 1.0):
    """
    请求重试装饰器
    
    Args:
        max_retries: 最大重试次数
        delay: 重试间隔(秒)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        logger.warning(f"{func.__name__} 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                        time.sleep(delay * (attempt + 1))
            logger.error(f"{func.__name__} 最终失败: {last_error}")
            raise last_error
        return wrapper
    return decorator


def timestamp_to_datetime(ts: int) -> datetime:
    """毫秒时间戳转 datetime"""
    return datetime.fromtimestamp(ts / 1000)


def datetime_to_timestamp(dt: datetime) -> int:
    """datetime 转毫秒时间戳"""
    return int(dt.timestamp() * 1000)


# 读取prompt文件
def read_file_prompt(path: str) -> str:
    """读取prompt文件"""
    with open(path, 'r', encoding='utf-8') as file:
        return file.read()
