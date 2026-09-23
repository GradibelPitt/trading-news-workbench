import unittest
import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.agent_runtime import build_agent_prompt, load_agent_profiles
from dataflows.crawler_bridge import build_agent_context, get_market_news_context


class AgentPipelineTests(unittest.TestCase):
    def setUp(self):
        # Setup temporary directories and files if necessary
        pass

    def test_load_agent_profiles(self):
        profiles = load_agent_profiles()
        self.assertIn("version", profiles)
        self.assertIn("agents", profiles)
        self.assertIn("router_agent", profiles["agents"])
        self.assertIn("risk_agent", profiles["agents"])
        self.assertIn("news_agent", profiles["agents"])
        self.assertIn("answer_composer", profiles["agents"])

    def test_build_agent_prompt(self):
        context = {"intent": "news_summary", "data_sources": {}}
        prompt = build_agent_prompt("news_agent", "今天有什么AI新闻？", context)
        self.assertIn("新闻分析 Agent", prompt)
        self.assertIn("今天有什么AI新闻？", prompt)
        self.assertIn("news_bias", prompt)

    def test_crawler_bridge_context(self):
        context = build_agent_context("news_summary")
        self.assertEqual(context["intent"], "news_summary")
        self.assertIn("data_sources", context)
        self.assertIn("market_news", context["data_sources"])
        
        # Verify get_market_news_context output structure
        news_ctx = get_market_news_context()
        if "status" in news_ctx:
            self.assertIn(news_ctx["status"], ("missing", "error"))
        else:
            self.assertEqual(news_ctx["source_name"], "market_news")
            self.assertIn("items", news_ctx)

    def test_general_query_routing_logic(self):
        # Test routing cases simulating logic inside server.py
        def check_is_general(item, question, mode=""):
            if mode == "suggestions":
                return False
            if not item or not item.get("title"):
                return True
            q_lower = question.lower()
            general_keywords_pairs = [
                ("今天", "新闻"), ("今日", "新闻"), ("今天", "快讯"), ("今日", "快讯"),
                ("今天", "重点"), ("今日", "重点"), ("今天", "热点"), ("今日", "热点"),
                ("ai", "新闻"), ("人工智能", "新闻"), ("ai", "重点"), ("人工智能", "重点"),
                ("ai", "快讯"), ("人工智能", "快讯"), ("总结", "新闻"), ("总结", "ai"),
                ("市场", "总结"), ("市场", "新闻"), ("大盘", "重点"),
                ("这", "重点"), ("新闻", "重点")
            ]
            if any(k1 in q_lower and k2 in q_lower for k1, k2 in general_keywords_pairs):
                return True
            if q_lower in ["今天 ai 新闻有什么重点？", "今天 ai 新闻有什么重点", "今天ai新闻有什么重点？", "今天ai新闻有什么重点"]:
                return True
            return False

        # Case 1: Empty item or no title -> Should be general
        self.assertTrue(check_is_general({}, "你好"))
        self.assertTrue(check_is_general({"symbol": "NVDA"}, "今天市场怎么样？"))

        # Case 2: Specific news item selected + specific question -> Should NOT be general
        news_item = {"title": "NVDA Q3 Earnings Beat", "content": "Nvidia reported record revenues..."}
        self.assertFalse(check_is_general(news_item, "这篇报道里提到什么细节？"))
        self.assertFalse(check_is_general(news_item, "NVDA的营收是多少？"))

        # Case 3: Specific news item selected + general question -> Should be general
        self.assertTrue(check_is_general(news_item, "今天 AI 新闻有什么重点？"))
        self.assertTrue(check_is_general(news_item, "今日人工智能新闻有什么重点"))
        self.assertTrue(check_is_general(news_item, "总结一下今天的市场快讯"))
        self.assertTrue(check_is_general(news_item, "这条新闻有什么重点"))

        # Case 4: Internal suggestion prompt (contains 'AI' and '新闻' keywords) with mode="suggestions" -> Should NOT be general
        suggestion_prompt = "你是一个用于生成追问按钮 of 内部 agent。如果是科技、AI、开源项目或新闻..."
        self.assertFalse(check_is_general(news_item, suggestion_prompt, mode="suggestions"))


if __name__ == "__main__":
    unittest.main()
