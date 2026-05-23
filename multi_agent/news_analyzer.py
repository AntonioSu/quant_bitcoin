"""新闻多空分析器

拉取 CryptoNewsSentiment 的资讯，调用 LLM 进行多空情绪研判。

与 MarketAnalyzer 类似，本模块也是一个 LLM agent：
- 角色定义在 multi_agent/prompts/news_analyzer.md
- 长期知识（分类法 / 权重表 / 噪音规则）在 multi_agent/knowledge/news/*.md
  会自动 glob 注入到 system prompt，便于以后单独迭代规则

输出:
  - sentiment:        bullish / bearish / neutral
  - score:            -100 ~ +100 (负=看空, 正=看多)
  - bullish_factors:  [{factor, category, weight, source_authority, truth_level, impact_scope, score_contribution, url}]
  - bearish_factors:  [{factor, category, weight, source_authority, truth_level, impact_scope, score_contribution, url}]
  - key_signals:      关键信号列表
  - reasoning:        分析理由
"""

import json
import os
from datetime import datetime
from typing import Dict, Optional

from dotenv import load_dotenv

from data_sources.base import DataSourceBase, DataPoint
from data_sources.crypto_news import CryptoNewsSentiment
from utils import logger
from utils.common_utils import read_file_prompt
from utils.llm_client import LLMClient

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), 'prompts')
_KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), 'knowledge')


class NewsAnalyzer(DataSourceBase):
    """新闻多空分析器

    value: score (-100 ~ +100)
    raw:   包含完整分析结果
    """

    def __init__(
        self,
        currencies: str = "BTC",
        max_items: int = 20,
        model_name: str = None,
    ):
        super().__init__("News Analyzer")
        self.news_source = CryptoNewsSentiment(
            currencies=currencies,
            max_items_per_source=max_items,
        )
        self.llm = LLMClient(
            model_name=model_name or os.getenv("LLM_MODEL_NAME"),
            key=os.getenv("LLM_API_KEY"),
            api_url=os.getenv("LLM_API_URL"),
            timeout=90,
            max_tokens=3000,
            # 启用服务端 Prompt Caching：news 的 system_prompt(角色 + 知识库)字节稳定，
            # 每次调用前缀都能命中，输入费用和首字节延迟大幅下降。
            # 同时关闭 thinking，新闻分类打分不需要 CoT。
            # 后端不识别这些字段时会被自动忽略，无副作用。
            extra_body={
                "caching": {"type": "enabled", "prefix": True},
                "thinking": {"type": "disabled"},
            },
        )
        self._cache_ttl = 1800

    def fetch(self) -> DataPoint:
        news_data = self.news_source.fetch()
        raw = news_data.raw or {}
        articles = raw.get("articles", [])

        if not articles:
            logger.warning("📰 无新闻数据，跳过 LLM 分析")
            return DataPoint(
                value=0.0,
                timestamp=datetime.now(),
                source=self.name,
                raw={"sentiment": "neutral", "score": 0, "reasoning": "无可用新闻数据"},
            )

        news_text = self._format_news(articles)
        analysis = self._analyze(news_text)

        score = analysis.get("score", 0)
        logger.info(
            f"📊 新闻多空分析: {analysis.get('sentiment', 'neutral').upper()} "
            f"(score={score}) — {analysis.get('reasoning', '')[:60]}"
        )

        return DataPoint(
            value=float(score),
            timestamp=datetime.now(),
            source=self.name,
            raw=analysis,
        )

    def _format_news(self, articles: list) -> str:
        lines = []
        for i, a in enumerate(articles[:20], 1):
            title = a.get("title", "")
            summary = a.get("summary", "")
            url = a.get("url", "")
            entry = f"{i}. {title}"
            if url:
                entry += f"\n   链接: {url}"
            if summary:
                entry += f"\n   摘要: {summary[:200]}"
            lines.append(entry)
        return "\n".join(lines)

    @staticmethod
    def _load_knowledge() -> str:
        """加载 multi_agent/knowledge/news/*.md 作为长期知识库

        与 MarketAnalyzer 加载 knowledge/ 下除 news/ 外的所有 md 不同，
        News 侧只加载 news/ 子目录，避免把 regime_matrix 等无关知识塞进来。
        """
        news_dir = os.path.join(_KNOWLEDGE_DIR, 'news')
        if not os.path.isdir(news_dir):
            return ""

        chunks = []
        for fname in sorted(os.listdir(news_dir)):
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(news_dir, fname)
            if os.path.isfile(fpath):
                chunks.append(read_file_prompt(fpath))
        return "\n\n".join(chunks)

    def _analyze(self, news_text: str) -> Dict:
        prompt = (
            f"以下是最新的加密货币新闻（共 {news_text.count(chr(10)) + 1} 行），"
            f"请按系统提示词与知识库规则分析多空情绪:\n\n{news_text}"
        )

        try:
            sys_prompt = read_file_prompt(
                os.path.join(_PROMPT_DIR, 'news_analyzer.md')
            )
            knowledge = self._load_knowledge()
            if knowledge:
                sys_prompt += "\n\n" + knowledge

            resp = self.llm.chat(
                system_prompt=sys_prompt,
                prompt=prompt,
                usage_tag="[news]",
            )
            return self._parse_json(resp)
        except Exception as e:
            logger.error(f"📰 LLM 分析失败: {e}")
            return {"sentiment": "neutral", "score": 0, "reasoning": f"LLM 调用失败: {e}"}

    @staticmethod
    def _parse_json(text: str) -> Dict:
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]

        stripped = text.strip()
        try:
            return NewsAnalyzer._normalize_analysis(json.loads(stripped))
        except json.JSONDecodeError as e:
            total_len = len(stripped)
            ends_well = stripped.endswith("}")
            tail = stripped[-200:]
            head = stripped[:200]
            likely_truncated = (not ends_well) and total_len > 1000
            diag = (
                f"JSON 解析失败（{e.msg} at pos {e.pos}）: "
                f"len={total_len}, ends_with_brace={ends_well}, "
                f"likely_truncated={likely_truncated}\n"
                f"  head: {head}\n  tail: {tail}"
            )
            logger.error(f"📰 {diag}")
            return {
                "sentiment": "neutral",
                "score": 0,
                "reasoning": (
                    f"JSON 解析失败（{'疑似输出被截断，需调高 max_tokens' if likely_truncated else '格式错误'}）: "
                    f"len={total_len}, tail={tail[-120:]}"
                ),
            }

    @staticmethod
    def _normalize_analysis(raw: Dict) -> Dict:
        """Return a stable shape for downstream market analysis and UI rendering."""
        if not isinstance(raw, dict):
            return {
                "sentiment": "neutral",
                "score": 0,
                "bullish_factors": [],
                "bearish_factors": [],
                "key_signals": [],
                "reasoning": "LLM 输出不是 JSON 对象，已按中性处理",
            }

        score = NewsAnalyzer._coerce_score(raw.get("score", 0))
        sentiment = raw.get("sentiment")
        if sentiment not in {"bullish", "bearish", "neutral"}:
            sentiment = "bullish" if score >= 20 else "bearish" if score <= -20 else "neutral"

        normalized = dict(raw)
        normalized["sentiment"] = sentiment
        normalized["score"] = score
        normalized["bullish_factors"] = NewsAnalyzer._normalize_factors(raw.get("bullish_factors"))
        normalized["bearish_factors"] = NewsAnalyzer._normalize_factors(raw.get("bearish_factors"))
        normalized["key_signals"] = NewsAnalyzer._normalize_strings(raw.get("key_signals"))[:3]
        normalized["reasoning"] = str(raw.get("reasoning") or "")[:300]
        return normalized

    @staticmethod
    def _normalize_factors(factors) -> list:
        if not isinstance(factors, list):
            return []

        allowed_weights = {"high", "medium", "low"}
        allowed_sources = {"official", "tier1_media", "crypto_media", "social", "unknown"}
        allowed_truth = {"confirmed", "multi_source", "single_source", "rumor", "false"}
        allowed_impact = {"market_wide", "sector", "single_project", "minor"}

        normalized = []
        for item in factors:
            if isinstance(item, str):
                factor = item.strip()
                if not factor:
                    continue
                normalized.append({
                    "factor": factor,
                    "category": "其他",
                    "weight": "low",
                    "source_authority": "unknown",
                    "truth_level": "single_source",
                    "impact_scope": "single_project",
                    "score_contribution": 0,
                    "url": "",
                })
                continue

            if not isinstance(item, dict):
                continue

            factor = str(item.get("factor") or item.get("description") or "").strip()
            if not factor:
                continue

            weight = item.get("weight") if item.get("weight") in allowed_weights else "low"
            source = item.get("source_authority") if item.get("source_authority") in allowed_sources else "unknown"
            truth = item.get("truth_level") if item.get("truth_level") in allowed_truth else "single_source"
            impact = item.get("impact_scope") if item.get("impact_scope") in allowed_impact else "single_project"

            normalized.append({
                "factor": factor,
                "category": str(item.get("category") or "其他"),
                "weight": weight,
                "source_authority": source,
                "truth_level": truth,
                "impact_scope": impact,
                "score_contribution": NewsAnalyzer._coerce_score(item.get("score_contribution", 0)),
                "url": str(item.get("url") or ""),
            })

        return normalized

    @staticmethod
    def _normalize_strings(value) -> list:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _coerce_score(value) -> int:
        try:
            score = int(round(float(value)))
        except (TypeError, ValueError):
            score = 0
        return max(-100, min(100, score))


def main():
    """独立运行测试"""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    analyzer = NewsAnalyzer(currencies="BTC", max_items=15)
    result = analyzer.fetch()

    raw = result.raw or {}
    sentiment = raw.get("sentiment", "unknown")
    score = raw.get("score", 0)

    print(f"\n{'=' * 70}")
    print(f"  多空判断: {sentiment.upper()}  |  评分: {score:+d}/100")
    print(f"{'=' * 70}")

    if raw.get("bullish_factors"):
        print("\n  📈 利多因素:")
        for f in raw["bullish_factors"]:
            if isinstance(f, dict):
                weight = f.get("weight", "")
                tag = f"[{weight}] " if weight else ""
                print(f"     + {tag}{f.get('factor', '')}")
                if f.get("url"):
                    print(f"       {f['url']}")
            else:
                print(f"     + {f}")

    if raw.get("bearish_factors"):
        print("\n  📉 利空因素:")
        for f in raw["bearish_factors"]:
            if isinstance(f, dict):
                weight = f.get("weight", "")
                tag = f"[{weight}] " if weight else ""
                print(f"     - {tag}{f.get('factor', '')}")
                if f.get("url"):
                    print(f"       {f['url']}")
            else:
                print(f"     - {f}")

    if raw.get("key_signals"):
        print("\n  🔑 关键信号:")
        for s in raw["key_signals"]:
            print(f"     ★ {s}")

    if raw.get("reasoning"):
        print(f"\n  💡 分析: {raw['reasoning']}")

    print(f"\n{'=' * 70}")


if __name__ == "__main__":
    main()
