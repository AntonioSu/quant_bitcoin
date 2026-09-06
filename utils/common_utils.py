"""通用工具函数"""

import json
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional

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


def seconds_until_next_boundary(interval_seconds: int, now: Optional[float] = None) -> float:
    """Seconds until the next clock-aligned boundary (hour, 2-hour, ...).

    Boundaries are Unix-epoch aligned, so a 3600s interval wakes at :00,
    and 7200s wakes on even hours. China (UTC+8) has no DST, so this matches
    local wall-clock hours.
    """
    if interval_seconds <= 0:
        return 0.0
    now_ts = time.time() if now is None else now
    delay = interval_seconds - (now_ts % interval_seconds)
    if delay >= interval_seconds:
        return float(interval_seconds)
    return float(delay)


def read_file_prompt(path: str) -> str:
    """读取prompt文件"""
    with open(path, 'r', encoding='utf-8') as file:
        return file.read()


def ensure_dotenv_loaded():
    """Load .env from project root if not already loaded."""
    import os
    from dotenv import load_dotenv
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'
    )
    load_dotenv(env_path)


def parse_llm_json(text: str, *, strict: bool = False) -> Optional[Dict[str, Any]]:
    """Extract a JSON object from LLM output that may contain markdown fences.

    Args:
        text: Raw LLM response text.
        strict: If True, raise on parse failure instead of returning None.

    Returns:
        Parsed dict, or None on failure (unless strict=True).
    """
    raw = str(text or "").strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0]
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]

    try:
        parsed = json.loads(raw.strip())
        if not isinstance(parsed, dict):
            if strict:
                raise TypeError("LLM output is not a JSON object")
            return None
        return parsed
    except (json.JSONDecodeError, TypeError):
        if strict:
            raise
        return None
