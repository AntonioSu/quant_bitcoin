"""Exercise refresh handlers without starting exchange clients or the server."""
import ast
import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import time


def load_function(path, name, namespace):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), namespace)
    return namespace[name]


class NewsRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = SimpleNamespace(news_refresh_lock=asyncio.Lock(),
                                     news_last_refresh_ts=0, notify_frontend=AsyncMock())
        self.market = SimpleNamespace(news=SimpleNamespace(
            value=42, timestamp=datetime.now(), raw={'sentiment': 'bullish'}))
        self.refresh = AsyncMock()
        self.handler = load_function('server/api.py', 'refresh_news_analysis_manual', {
            'app_state': self.state, 'market': self.market, 'time': time,
            'refresh_news_data_async': self.refresh, 'logger': Mock(),
        })

    async def test_success_returns_analysis_and_broadcasts(self):
        result = await self.handler()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['news_score'], 42)
        self.refresh.assert_awaited_once()
        self.state.notify_frontend.assert_awaited_once()
        self.assertEqual((await self.handler())['status'], 'cooldown')
        self.assertEqual(self.refresh.await_count, 1)

    async def test_running_does_not_start_duplicate(self):
        async with self.state.news_refresh_lock:
            self.assertEqual((await self.handler())['status'], 'running')
        self.refresh.assert_not_awaited()

    async def test_failure_releases_lock_and_allows_retry(self):
        self.refresh.side_effect = RuntimeError('offline')
        self.assertEqual((await self.handler())['status'], 'error')
        self.assertFalse(self.state.news_refresh_lock.locked())
        self.assertEqual(self.state.news_last_refresh_ts, 0)
        self.state.notify_frontend.assert_not_awaited()

    async def test_failed_analysis_preserves_previous_news(self):
        previous = self.market.news
        analyzer = Mock()
        analyzer.fetch.return_value = SimpleNamespace(raw={'error': True, 'reasoning': 'no news'})
        refresh = load_function('core/market_data.py', 'refresh_news_data_async', {
            'market': self.market, '_news_analyzer': analyzer, 'logger': Mock(),
        })
        with self.assertRaises(ValueError):
            await refresh()
        self.assertIs(self.market.news, previous)
        self.assertEqual(self.market.news_status, 'error')
        self.assertTrue(self.market.news_error)

    async def test_permission_failure_has_safe_actionable_message(self):
        analyzer = Mock()
        analyzer.fetch.side_effect = RuntimeError('403 Forbidden: private provider details')
        refresh = load_function('core/market_data.py', 'refresh_news_data_async', {
            'market': self.market, '_news_analyzer': analyzer, 'logger': Mock(),
        })
        with self.assertRaises(RuntimeError):
            await refresh()
        self.assertIn('API Key', self.market.news_error)
        self.assertNotIn('private', self.market.news_error)

    async def test_success_clears_previous_error(self):
        self.market.news_error = 'previous failure'
        analyzer = Mock()
        analyzer.fetch.return_value = self.market.news
        refresh = load_function('core/market_data.py', 'refresh_news_data_async', {
            'market': self.market, '_news_analyzer': analyzer, 'logger': Mock(),
        })
        await refresh()
        self.assertEqual(self.market.news_status, 'ok')
        self.assertIsNone(self.market.news_error)


if __name__ == '__main__':
    unittest.main()
