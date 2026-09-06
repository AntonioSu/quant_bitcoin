#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM API 客户端
支持多个模型的统一接口
"""

import json
import os
import time
from typing import Any, Callable, Dict, Optional

import requests
from dotenv import load_dotenv

from utils.log_util import logger

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))


class LLMClient:
    """LLM API客户端"""

    def __init__(self, model_name: str = None, key: str = None, api_url: str = None,
                 timeout: int = 60, max_retries: int = 3, max_tokens: int = 4096,
                 extra_body: Optional[Dict[str, Any]] = None):
        """
        初始化LLM客户端

        Args:
            model_name: 模型名称，默认从环境变量 LLM_MODEL_NAME 读取
            key: API密钥，默认从环境变量 LLM_API_KEY 读取
            api_url: API URL，默认从环境变量 LLM_API_URL 读取
            timeout: 请求超时时间(秒)
            max_retries: 最大重试次数
            max_tokens: 单次响应最大 token 数，长 JSON 输出场景需调高（默认 4096）
            extra_body: 透传到 payload 顶层的额外字段，例如
                {"caching": {"type": "enabled", "prefix": True},
                 "thinking": {"type": "disabled"}}
                后端不识别的字段会被自动忽略，可安全地用于开启
                厂商专属能力（火山 Ark / DeepSeek 等的 Prompt Caching）。
        """
        self.model_name = model_name
        self.key = key
        self.api_url = api_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.extra_body: Dict[str, Any] = dict(extra_body or {})

    def _make_request(self, messages: list, temperature: float = 0.6,
                      extra_body: Optional[Dict[str, Any]] = None,
                      tools: Optional[list] = None) -> Dict:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {self.key}"
        }

        payload: Dict[str, Any] = {
            'model': self.model_name,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': self.max_tokens,
        }
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = 'auto'

        # 合并默认 extra_body 与本次调用覆写值
        merged_extra = {**self.extra_body, **(extra_body or {})}
        payload.update(merged_extra)

        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout
                )

                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout:
                logger.warning(f"LLM 请求超时 (尝试 {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)  # 指数退避
                else:
                    raise

            except requests.exceptions.RequestException as e:
                logger.warning(f"LLM 请求失败: {e} (尝试 {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise

    @staticmethod
    def _log_usage(usage: Dict[str, Any], tag: str = ""):
        """打印 token 用量 + cache 命中率，便于观察 Prompt Caching 是否生效

        不同厂商字段命名略有差异，做一次广义兼容：
        - DeepSeek:  prompt_cache_hit_tokens / prompt_cache_miss_tokens
        - 火山 Ark:  prompt_tokens_details.cached_tokens
        - OpenAI:    prompt_tokens_details.cached_tokens
        """
        if not usage:
            return
        prompt_tokens = usage.get('prompt_tokens', 0)
        completion_tokens = usage.get('completion_tokens', 0)

        cached = (
            usage.get('prompt_cache_hit_tokens')
            or (usage.get('prompt_tokens_details') or {}).get('cached_tokens')
            or usage.get('cached_tokens')
            or 0
        )
        hit_rate = (cached / prompt_tokens * 100) if prompt_tokens else 0
        prefix = f"🧠 {tag} " if tag else "🧠 "
        logger.info(
            f"{prefix}LLM usage: prompt={prompt_tokens} "
            f"(cached={cached}, hit_rate={hit_rate:.0f}%) "
            f"completion={completion_tokens}"
        )
        if os.getenv("LLM_DEBUG_USAGE"):
            logger.info(f"{prefix}raw usage: {json.dumps(usage, ensure_ascii=False)}")

    def chat(self, system_prompt: Optional[str] = None, prompt: str = None,
             extra_body: Optional[Dict[str, Any]] = None,
             usage_tag: str = "") -> str:
        """
        简单对话接口

        Args:
            prompt: 用户消息
            system_prompt: 系统提示词
            extra_body: 本次调用追加 / 覆写的厂商特有字段
            usage_tag: 日志前缀，方便区分不同 agent 的命中率

        Returns:
            模型回复
        """
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': prompt}
        ]

        response = self._make_request(messages, extra_body=extra_body)
        self._log_usage(response.get('usage') or {}, tag=usage_tag)
        return response['choices'][0]['message']['content']

    def chat_with_tools(
        self,
        system_prompt: Optional[str],
        prompt: str,
        tools: list,
        dispatch: Callable[[str, Dict[str, Any]], Any],
        max_rounds: int = 4,
        extra_body: Optional[Dict[str, Any]] = None,
        usage_tag: str = "",
    ) -> str:
        """带 function calling 的对话，返回模型最终的文本回复。

        Args:
            tools: OpenAI 格式的 tools 定义
            dispatch: (工具名, 参数dict) -> 可 JSON 序列化的结果，由调用方执行
            max_rounds: 工具调用轮数上限，防止模型无限循环调用

        工具执行失败不抛出，而是把错误文本回灌给模型，让它自己纠正或放弃。
        轮数耗尽时强制再请求一次且不带 tools，逼模型给出最终答案。
        """
        messages: list = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': prompt},
        ]

        for round_idx in range(max_rounds):
            response = self._make_request(
                messages, extra_body=extra_body, tools=tools,
            )
            self._log_usage(response.get('usage') or {}, tag=usage_tag)
            message = response['choices'][0]['message']
            tool_calls = message.get('tool_calls') or []

            if not tool_calls:
                return message.get('content') or ""

            # 必须把带 tool_calls 的 assistant 消息原样回填，否则后续
            # tool 角色消息没有对应的 call_id，多数厂商会直接报 400。
            messages.append(message)

            for call in tool_calls:
                fn = (call.get('function') or {})
                name = fn.get('name') or ""
                raw_args = fn.get('arguments') or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                    result: Any = {"error": f"参数不是合法 JSON: {raw_args[:120]}"}
                else:
                    try:
                        result = dispatch(name, args)
                    except Exception as e:
                        logger.warning(f"🔧 {usage_tag} 工具 {name} 执行失败: {e}")
                        result = {"error": f"{type(e).__name__}: {e}"}

                logger.info(
                    f"🔧 {usage_tag} tool={name} args={json.dumps(args, ensure_ascii=False)}"
                )
                messages.append({
                    'role': 'tool',
                    'tool_call_id': call.get('id'),
                    'content': json.dumps(result, ensure_ascii=False),
                })

        # 轮数用尽：强制收敛。必须显式下指令，否则部分厂商（DeepSeek 系）会
        # 把工具调用语法当普通文本吐出来（<｜｜DSML｜｜tool_calls>...），
        # 下游 JSON 解析直接失败。
        logger.warning(f"🔧 {usage_tag} 工具调用达到 {max_rounds} 轮上限，强制收敛")
        messages.append({
            'role': 'user',
            'content': (
                "工具调用环节已结束，不要再调用任何工具，也不要输出任何工具调用语法。"
                "现在基于已有的试算结果，直接输出最终 JSON。"
            ),
        })
        response = self._make_request(messages, extra_body=extra_body)
        self._log_usage(response.get('usage') or {}, tag=usage_tag)
        return response['choices'][0]['message'].get('content') or ""


if __name__ == '__main__':
    key = os.getenv('LLM_API_KEY')
    api_url = os.getenv('LLM_API_URL')
    model_name = os.getenv('LLM_MODEL_NAME')
    client = LLMClient(model_name=model_name, key=key, api_url=api_url)
    print(f"model_name: {model_name}")
    print(f"key: {key}")
    print(f"api_url: {api_url}")
    result = client.chat(system_prompt="你是一个专业的股票分析师，请根据以下信息分析股票走势", prompt="请分析一下比特币的未来走势")
    print(f"result: {result}")
