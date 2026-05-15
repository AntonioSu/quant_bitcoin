"""新闻多空分析器

拉取 CryptoNewsSentiment 的资讯，调用 LLM 进行多空情绪研判。

输出:
  - sentiment: bullish / bearish / neutral
  - score: -100 ~ +100 (负=看空, 正=看多)
  - reasoning: 分析理由
  - key_signals: 关键信号列表
"""

import json
import os
from datetime import datetime
from typing import Dict, Optional

from dotenv import load_dotenv

from ..data_sources.base import DataSourceBase, DataPoint
from ..data_sources.crypto_news import CryptoNewsSentiment
from ..utils import logger
from ..utils.common_utils import read_file_prompt
from ..utils.llm_client import LLMClient

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), '..', 'prompts')


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

    def _analyze(self, news_text: str) -> Dict:
        prompt = f"以下是最新的加密货币新闻（共 {news_text.count(chr(10)) + 1} 条），请分析多空情绪:\n\n{news_text}"

        try:
            resp = self.llm.chat(system_prompt=read_file_prompt(os.path.join(_PROMPT_DIR, 'news_analyzer.md')), prompt=prompt)
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

        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return {"sentiment": "neutral", "score": 0, "reasoning": f"JSON 解析失败，原始回复: {text[:300]}"}


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
                print(f"     + {f.get('factor', '')}")
                if f.get("url"):
                    print(f"       {f['url']}")
            else:
                print(f"     + {f}")

    if raw.get("bearish_factors"):
        print("\n  📉 利空因素:")
        for f in raw["bearish_factors"]:
            if isinstance(f, dict):
                print(f"     - {f.get('factor', '')}")
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
