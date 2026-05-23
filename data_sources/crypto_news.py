"""加密货币新闻资讯数据源

数据来源: RSS feeds (无需 API Key)
- CoinTelegraph: https://cointelegraph.com/rss
- Decrypt:       https://decrypt.co/feed

可选: 若配置了 CryptoPanic API Key，优先使用
CRYPTOPANIC_API_KEY=your_token_here  (.env 可选)
"""

import os
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, List, Dict, Tuple

from data_sources.base import DataSourceBase, DataPoint
from utils import logger, retry_request


class CryptoNewsSentiment(DataSourceBase):
    """加密货币新闻资讯

    value: 返回拉取到的新闻条数
    raw:   包含新闻标题列表和文章详情
    """

    RSS_SOURCES = [
        {
            "name": "CoinTelegraph",
            "url": "https://cointelegraph.com/rss",
            "weight": 1.0,
        },
        {
            "name": "Decrypt",
            "url": "https://decrypt.co/feed",
            "weight": 1.0,
        },
    ]

    CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"

    def __init__(
        self,
        currencies: str = "BTC",
        max_items_per_source: int = 15,
    ):
        super().__init__("Crypto News")
        self.currencies = [c.strip().upper() for c in currencies.split(",")]
        self.max_items = max_items_per_source
        self.cryptopanic_key = os.getenv("CRYPTOPANIC_API_KEY", "")
        self._cache_ttl = 900

    def fetch(self) -> DataPoint:
        if self.cryptopanic_key:
            try:
                return self._fetch_cryptopanic()
            except Exception as e:
                logger.warning(f"📰 CryptoPanic 失败，降级 RSS: {e}")
        return self._fetch_rss()

    # ------------------------------------------------------------------
    # CryptoPanic
    # ------------------------------------------------------------------

    @retry_request(max_retries=2, delay=3.0)
    def _fetch_cryptopanic(self) -> DataPoint:
        params = {
            "auth_token": self.cryptopanic_key,
            "currencies": ",".join(self.currencies),
            "kind": "news",
            "limit": 20,
        }
        resp = requests.get(self.CRYPTOPANIC_URL, params=params, timeout=15)
        resp.raise_for_status()

        results = resp.json().get("results", [])
        if not results:
            raise ValueError("CryptoPanic 返回空结果")

        headlines = [item.get("title", "") for item in results[:20]]
        articles = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "summary": item.get("body", "") or item.get("title", ""),
            }
            for item in results[:20]
        ]
        logger.info(f"📰 [CryptoPanic] 获取 {len(headlines)} 条新闻")

        return DataPoint(
            value=float(len(headlines)),
            timestamp=datetime.now(),
            source="CryptoPanic",
            raw={
                "news_count": len(headlines),
                "headlines": headlines[:5],
                "articles": articles,
            },
        )

    # ------------------------------------------------------------------
    # RSS 免费方案
    # ------------------------------------------------------------------

    def _fetch_rss(self) -> DataPoint:
        all_items: List[Tuple[str, str, str]] = []  # (title, link, summary)
        fetched_sources = []

        for src in self.RSS_SOURCES:
            try:
                items = self._parse_rss(src["url"])
                all_items.extend(items)
                fetched_sources.append(src["name"])
            except Exception as e:
                logger.warning(f"📰 [{src['name']}] RSS 拉取失败: {e}")

        if not all_items:
            logger.warning("📰 所有 RSS 源均失败，返回空结果")
            return DataPoint(
                value=0.0,
                timestamp=datetime.now(),
                source=self.name,
                raw={"error": "all sources failed", "news_count": 0, "headlines": [], "articles": []},
            )

        crypto_terms = set(self.currencies) | {"bitcoin", "crypto", "btc", "eth", "blockchain"}
        filtered = [
            (t, l, s) for t, l, s in all_items
            if any(c.lower() in t.lower() for c in crypto_terms)
        ] or all_items

        articles = [{"title": t, "url": l, "summary": s} for t, l, s in filtered[:30]]
        headlines = [a["title"] for a in articles]
        source_label = f"RSS({'+'.join(fetched_sources)})"
        logger.info(f"📰 [{source_label}] 获取 {len(filtered)} 条新闻")

        return DataPoint(
            value=float(len(filtered)),
            timestamp=datetime.now(),
            source=source_label,
            raw={
                "news_count": len(filtered),
                "headlines": headlines[:5],
                "articles": articles,
            },
        )

    @staticmethod
    def _strip_html(text: str) -> str:
        """去除 HTML 标签和多余空白"""
        clean = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\s+", " ", clean).strip()

    @retry_request(max_retries=2, delay=2.0)
    def _parse_rss(self, url: str) -> List[Tuple[str, str, str]]:
        """拉取并解析 RSS，返回 [(title, link, summary), ...]"""
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        items = []

        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            desc_el = item.find("description")
            if title_el is not None and title_el.text:
                link = ""
                if link_el is not None:
                    link = (link_el.text or link_el.tail or "").strip()
                if not link:
                    guid_el = item.find("guid")
                    if guid_el is not None and guid_el.text and guid_el.text.startswith("http"):
                        link = guid_el.text.strip()
                summary = ""
                if desc_el is not None and desc_el.text:
                    summary = self._strip_html(desc_el.text)
                items.append((title_el.text.strip(), link, summary))
            if len(items) >= self.max_items:
                break

        if not items:
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                title_el = entry.find("{http://www.w3.org/2005/Atom}title")
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                summary_el = entry.find("{http://www.w3.org/2005/Atom}summary")
                if title_el is not None and title_el.text:
                    link = link_el.get("href", "") if link_el is not None else ""
                    summary = ""
                    if summary_el is not None and summary_el.text:
                        summary = self._strip_html(summary_el.text)
                    items.append((title_el.text.strip(), link, summary))
                if len(items) >= self.max_items:
                    break

        return items

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def get_headlines(self) -> List[str]:
        """获取最新新闻标题"""
        data = self.get()
        return data.raw.get("headlines", []) if data.raw else []

    def get_articles(self) -> List[Dict]:
        """获取最新新闻列表 [{title, url}, ...]"""
        data = self.get()
        return data.raw.get("articles", []) if data.raw else []


def main():
    """测试"""
    import logging
    logging.disable(logging.CRITICAL)

    news = CryptoNewsSentiment(currencies="BTC", max_items_per_source=20)
    data = news.fetch()

    raw = data.raw or {}
    print(f"\n{'='*70}")
    print(f"  新闻条数: {int(data.value)}")
    print(f"  数据源: {data.source}")
    print(f"{'='*70}")
    for i, article in enumerate(raw.get("articles", []), 1):
        print(f"\n  {i:>2}. {article['title']}")
        if article.get("summary"):
            print(f"      摘要: {article['summary']}")
        if article.get("url"):
            print(f"      {article['url']}")


if __name__ == "__main__":
    main()
