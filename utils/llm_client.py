#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM API 客户端
支持多个模型的统一接口
"""

from curses import keyname
import json
import os
import requests
from typing import Dict, Optional
import time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))


class LLMClient:
    """LLM API客户端"""
    
    def __init__(self, model_name: str = None, key: str = None, api_url: str = None, timeout: int = 60, max_retries: int = 3):
        """
        初始化LLM客户端
        
        Args:
            model_name: 模型名称，默认从环境变量 LLM_MODEL_NAME 读取
            key: API密钥，默认从环境变量 LLM_API_KEY 读取
            api_url: API URL，默认从环境变量 LLM_API_URL 读取
            timeout: 请求超时时间(秒)
            max_retries: 最大重试次数
        """
        self.model_name = model_name
        self.key = key
        self.api_url = api_url
        self.timeout = timeout
        self.max_retries = max_retries
    
    def _make_request(self, messages: list, temperature: float = 0.6) -> Dict:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {self.key}"
        }
        
        payload = {
            'model': self.model_name,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': 4096
        }
        
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
                print(f"请求超时 (尝试 {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)  # 指数退避
                else:
                    raise
            
            except requests.exceptions.RequestException as e:
                print(f"请求失败: {str(e)} (尝试 {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise

    def chat(self, system_prompt: Optional[str] = None, prompt: str = None) -> str:
        """
        简单对话接口
        
        Args:
            prompt: 用户消息
            system_prompt: 系统提示词
        
        Returns:
            模型回复
        """
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': prompt}
        ]
                
        response = self._make_request(messages)
        return response['choices'][0]['message']['content']


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
