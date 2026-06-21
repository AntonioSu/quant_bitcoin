"""交易复盘器

这个模块负责在交易闭环结束后，对单笔交易做自动复盘。
它会读取 AnalysisMemory 中保存的"开仓前 AI 研判"和随后关联进来的
"实际交易结果"，再调用 LLM 分析这次判断为什么正确或错误。

典型链路：

- MarketAnalyzer 在产生非 NEUTRAL 研判时，把 bias、confidence、summary、
  key_drivers、risks 和市场快照写入 AnalysisMemory；
- 交易调度器在 CLOSE / REDUCE 等平仓动作发生后，把成交结果关联到最近一次研判；
- Reflector 对比研判内容、市场快照和实际 PnL，生成评分、模式标签和经验教训；
- 复盘结果会写回 AnalysisMemory，后续由 StrategySummarizer 聚合成策略备忘录，
  再注入下一轮 MarketAnalyzer 的 prompt 中。

Reflector 只负责单笔交易的归因分析，不直接修改交易策略或执行下单。
它运行在异步线程中，失败时只记录日志，不阻塞交易主流程。
"""

import json
import os
from typing import Dict, Any, Optional

from dotenv import load_dotenv

from indicators.analysis_memory import AnalysisMemory
from utils import logger
from utils.common_utils import read_file_prompt
from utils.llm_client import LLMClient

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), 'prompts')


class Reflector:
    """交易复盘器"""

    def __init__(self, memory: AnalysisMemory,
                 model_name: Optional[str] = None):
        self.memory = memory
        self.llm = LLMClient(
            model_name=model_name or os.getenv("LLM_MODEL_NAME"),
            key=os.getenv("LLM_API_KEY"),
            api_url=os.getenv("LLM_API_URL"),
            timeout=90,
        )

    def reflect_on_trade(self, record_id: str) -> Optional[Dict[str, Any]]:
        """对指定记录做复盘，结果写回 memory"""
        record = self.memory.get_record(record_id)
        if not record:
            logger.warning(f"🔍 复盘跳过: 找不到记录 {record_id}")
            return None

        trade_result = record.get("trade_result")
        if not trade_result:
            logger.warning(f"🔍 复盘跳过: 记录 {record_id} 无交易结果")
            return None

        if record.get("reflection"):
            logger.debug(f"🔍 记录 {record_id} 已复盘，跳过")
            return record["reflection"]

        prompt = self._build_prompt(record, trade_result)
        reflection = self._analyze(prompt)

        if reflection:
            self.memory.attach_reflection(record_id, reflection)
            logger.info(
                f"🔍 复盘完成: {record_id} | "
                f"评分={reflection.get('score')}/5 | "
                f"标签={reflection.get('pattern_tag')} | "
                f"教训: {reflection.get('lesson', '')[:40]}"
            )

        return reflection

    def reflect_all_pending(self) -> int:
        """复盘所有有交易结果但未复盘的记录"""
        pending = self.memory.get_unreflected(limit=5)
        count = 0
        for rec in pending:
            result = self.reflect_on_trade(rec["id"])
            if result:
                count += 1
        return count

    @staticmethod
    def _build_prompt(record: Dict, trade_result: Dict) -> str:
        analysis_part = json.dumps({
            "bias": record.get("bias"),
            "confidence": record.get("confidence"),
            "summary": record.get("summary"),
            "key_drivers": record.get("key_drivers", []),
            "risks": record.get("risks", []),
        }, ensure_ascii=False, indent=2)

        snapshot_part = json.dumps(
            record.get("snapshot_digest") or {},
            ensure_ascii=False, indent=2,
        )

        result_part = json.dumps(trade_result, ensure_ascii=False, indent=2)

        return (
            f"## 开仓时 AI 研判\n```json\n{analysis_part}\n```\n\n"
            f"## 开仓时市场快照\n```json\n{snapshot_part}\n```\n\n"
            f"## 交易结果\n```json\n{result_part}\n```"
        )

    def _analyze(self, prompt: str) -> Optional[Dict[str, Any]]:
        try:
            sys_prompt = read_file_prompt(
                os.path.join(_PROMPT_DIR, 'reflector.md')
            )
            resp = self.llm.chat(system_prompt=sys_prompt, prompt=prompt)
            return self._parse_json(resp)
        except Exception as e:
            logger.error(f"🔍 复盘 LLM 调用失败: {e}")
            return None

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            logger.error(f"🔍 复盘 JSON 解析失败: {text[:120]}")
            return None
