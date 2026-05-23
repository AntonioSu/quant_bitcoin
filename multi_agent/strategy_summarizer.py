"""策略备忘录生成器

把多次 Reflection 压缩成简短的策略备忘录，注入到 MarketAnalyzer 的 prompt 中，
让后续研判吸取历史交易教训。
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Any, Optional

from dotenv import load_dotenv

from indicators.analysis_memory import AnalysisMemory
from utils import logger
from utils.common_utils import read_file_prompt
from utils.llm_client import LLMClient

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), 'prompts')
_DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
)


class StrategySummarizer:
    """把复盘经验压缩成策略备忘录"""

    MIN_TRADES_FOR_SUMMARY = 3

    def __init__(self, memory: AnalysisMemory,
                 model_name: Optional[str] = None,
                 data_dir: Optional[str] = None):
        self.memory = memory
        self.llm = LLMClient(
            model_name=model_name or os.getenv("LLM_MODEL_NAME"),
            key=os.getenv("LLM_API_KEY"),
            api_url=os.getenv("LLM_API_URL"),
            timeout=90,
        )
        data_dir = data_dir or _DEFAULT_DATA_DIR
        os.makedirs(data_dir, exist_ok=True)
        self._memo_path = os.path.join(data_dir, "strategy_memo.json")
        self._memo_cache: Optional[Dict] = None
        self._load_memo()

    def generate(self, performance: Optional[Dict] = None,
                 since_days: int = 30) -> Optional[Dict[str, Any]]:
        """生成策略备忘录

        Args:
            performance: PerformanceTracker.calculate() 的输出
            since_days: 回溯天数
        """
        reflections = self.memory.get_all_reflections(since_days=since_days)

        if len(reflections) < self.MIN_TRADES_FOR_SUMMARY:
            logger.info(
                f"📋 复盘记录不足 {self.MIN_TRADES_FOR_SUMMARY} 条 "
                f"(当前 {len(reflections)}), 跳过备忘录生成"
            )
            return None

        prompt = self._build_prompt(reflections, performance)
        result = self._analyze(prompt)

        if result:
            self._memo_cache = {
                "generated_at": datetime.now().isoformat(),
                "trade_count": len(reflections),
                "result": result,
            }
            self._save_memo()
            logger.info(
                f"📋 策略备忘录已生成 "
                f"({len(reflections)} 笔交易, "
                f"{len(result.get('rules', []))} 条规则)"
            )

        return result

    def get_memo_text(self) -> Optional[str]:
        """获取当前策略备忘录文本（用于注入研判 prompt）

        把 LLM 总结的 5 段（effective_signals / weak_signals /
        systematic_biases / rules / memo_text）全部展开为 markdown，
        让下游 MarketAnalyzer 看到带统计证据的可执行规则，
        而不是只有一段总结句。

        数据已完整保存在 strategy_memo.json，这里只是"读取侧用足"。
        """
        if not self._memo_cache:
            return None
        result = self._memo_cache.get("result", {})
        if not result:
            return None

        parts: List[str] = []

        if text := result.get("memo_text"):
            parts.append(f"### 总结\n{text}")

        if items := result.get("effective_signals"):
            parts.append(
                "### 历史验证的有效信号（含胜率统计）\n"
                + "\n".join(f"- ✅ {s}" for s in items)
            )

        if items := result.get("weak_signals"):
            parts.append(
                "### 历史亏损模式（务必规避）\n"
                + "\n".join(f"- ❌ {s}" for s in items)
            )

        if items := result.get("systematic_biases"):
            parts.append(
                "### 系统性偏差提醒\n"
                + "\n".join(f"- ⚠️ {s}" for s in items)
            )

        if items := result.get("rules"):
            parts.append(
                "### 强制规则（覆盖知识库默认；如与知识库冲突仍以知识库为准）\n"
                + "\n".join(f"- {r}" for r in items)
            )

        return "\n\n".join(parts) if parts else None

    def get_full_memo(self) -> Optional[Dict]:
        """获取完整备忘录"""
        return self._memo_cache

    @staticmethod
    def _build_prompt(reflections: List[Dict],
                      performance: Optional[Dict]) -> str:
        perf_text = "暂无绩效数据"
        if performance:
            perf_text = (
                f"总交易: {performance.get('total_trades', 0)} 笔, "
                f"胜率: {performance.get('win_rate', 0)}%, "
                f"盈亏比: {performance.get('profit_factor', 'N/A')}, "
                f"夏普: {performance.get('sharpe_ratio', 'N/A')}, "
                f"最大回撤: {performance.get('max_drawdown_pct', 0)}%"
            )

        records_text = []
        for r in reflections:
            ref = r.get("reflection", {})
            tr = r.get("trade_result", {})
            records_text.append(
                f"- [{r.get('bias')}/{r.get('confidence')}%] "
                f"PnL=${tr.get('pnl', 0):+.2f} | "
                f"评分={ref.get('score', '?')}/5 | "
                f"标签={ref.get('pattern_tag', '?')} | "
                f"教训: {ref.get('lesson', '无')}"
            )

        return (
            f"## 绩效概览\n{perf_text}\n\n"
            f"## 复盘记录 ({len(reflections)} 笔)\n"
            + "\n".join(records_text)
        )

    def _analyze(self, prompt: str) -> Optional[Dict[str, Any]]:
        try:
            sys_prompt = read_file_prompt(
                os.path.join(_PROMPT_DIR, 'strategy_summarizer.md')
            )
            resp = self.llm.chat(system_prompt=sys_prompt, prompt=prompt)
            return self._parse_json(resp)
        except Exception as e:
            logger.error(f"📋 策略备忘录 LLM 失败: {e}")
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
            logger.error(f"📋 策略备忘录 JSON 解析失败: {text[:120]}")
            return None

    def _load_memo(self):
        if not os.path.exists(self._memo_path):
            return
        try:
            with open(self._memo_path, "r", encoding="utf-8") as f:
                self._memo_cache = json.load(f)
        except Exception as e:
            logger.error(f"加载策略备忘录失败: {e}")

    def _save_memo(self):
        try:
            with open(self._memo_path, "w", encoding="utf-8") as f:
                json.dump(self._memo_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存策略备忘录失败: {e}")
