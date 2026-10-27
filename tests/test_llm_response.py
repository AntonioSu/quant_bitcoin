"""Model responses may omit content when the output budget is exhausted."""
import unittest
from unittest.mock import Mock

from utils.llm_client import LLMClient, LLMOutputLimitError
from multi_agent.news_analyzer import NewsAnalyzer


class LLMResponseTests(unittest.TestCase):
    def client(self, response):
        client = LLMClient(max_tokens=3000)
        client._make_request = Mock(return_value=response)
        return client

    def test_missing_content_at_budget_is_output_limit(self):
        client = self.client({'choices': [{'message': {}}],
                              'usage': {'completion_tokens': 3000}})
        with self.assertRaises(LLMOutputLimitError):
            client.chat(prompt='news')

    def test_partial_content_is_not_accepted(self):
        client = self.client({'choices': [{'message': {'content': '{"score":'},
                                          'finish_reason': 'length'}]})
        with self.assertRaises(LLMOutputLimitError):
            client.chat(prompt='news')

    def test_empty_response_has_explicit_error(self):
        for response in ({}, {'choices': [{'message': {'content': None}}]}):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    self.client(response).chat(prompt='news')

    def test_valid_response_is_returned(self):
        client = self.client({'choices': [{'message': {'content': '{"score": 6}'},
                                          'finish_reason': 'stop'}]})
        self.assertEqual(client.chat(prompt='news'), '{"score": 6}')

    def test_news_retries_output_limit_once(self):
        analyzer = NewsAnalyzer()
        analyzer.llm.chat = Mock(side_effect=[LLMOutputLimitError('length'),
                                             '{"score": 6, "sentiment": "neutral"}'])
        self.assertEqual(analyzer._analyze('News')['score'], 6)
        self.assertEqual(analyzer.llm.chat.call_count, 2)
        self.assertEqual(analyzer.llm.chat.call_args.kwargs['extra_body']['max_tokens'], 16000)

    def test_news_retry_is_bounded(self):
        analyzer = NewsAnalyzer()
        analyzer.llm.chat = Mock(side_effect=LLMOutputLimitError('length'))
        self.assertTrue(analyzer._analyze('News')['error'])
        self.assertEqual(analyzer.llm.chat.call_count, 2)


if __name__ == '__main__':
    unittest.main()
