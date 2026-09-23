import json
from pathlib import Path
from datetime import datetime
import sys
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "crawler_cache"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 动态添加 opensource_community 路径
COMMUNITY_DIR = PROJECT_ROOT / "pages" / "news" / "opensource_community"
if str(COMMUNITY_DIR) not in sys.path:
    sys.path.insert(0, str(COMMUNITY_DIR))

# 动态添加 crawler 路径
CRAWLER_DIR = PROJECT_ROOT / "pages" / "analysis" / "crawler"
if str(CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(CRAWLER_DIR))

try:
    import news_core
except ImportError:
    news_core = None

try:
    import crawler as opensource_crawler
except ImportError:
    opensource_crawler = None

try:
    import news_data_tools
except ImportError:
    news_data_tools = None

try:
    import stocktwits
except ImportError:
    stocktwits = None

try:
    import reddit
except ImportError:
    reddit = None


def load_json_cache(filename: str, default=None):
    path = CACHE_DIR / filename
    if not path.exists():
        return default if default is not None else {
            "status": "missing",
            "items": [],
            "error": f"{filename} not found"
        }

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {
            "status": "error",
            "items": [],
            "error": str(e)
        }


def is_valid_cache(raw):
    return raw is not None and isinstance(raw, dict) and raw.get("status") not in ("missing", "error")


def get_market_news_context(limit=30):
    raw = load_json_cache("market_news.json", None)
    if not is_valid_cache(raw):
        return raw

    items = raw.get("items", [])[:limit]
    return {
        "source_name": "market_news",
        "loaded_at": datetime.now().isoformat(timespec="seconds"),
        "item_count": len(items),
        "items": [
            {
                "title": item.get("title", ""),
                "summary": item.get("summary", ""),
                "source": item.get("source", ""),
                "time": item.get("time", ""),
                "url": item.get("url", ""),
                "related_tickers": item.get("related_tickers", [])
            }
            for item in items
        ]
    }


def get_opensource_context(limit=20):
    raw = load_json_cache("opensource_community.json", None)
    if not is_valid_cache(raw):
        return raw

    items = raw.get("items", [])[:limit]
    return {
        "source_name": "opensource_community",
        "loaded_at": datetime.now().isoformat(timespec="seconds"),
        "item_count": len(items),
        "items": [
            {
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "url": item.get("url", ""),
                "stars": item.get("stars", None),
                "language": item.get("language", ""),
                "tags": item.get("tags", [])
            }
            for item in items
        ]
    }


def get_academic_context(limit=20):
    raw = load_json_cache("academic_ai.json", None)
    if not is_valid_cache(raw):
        return raw

    items = raw.get("items", [])[:limit]
    return {
        "source_name": "academic_ai",
        "loaded_at": datetime.now().isoformat(timespec="seconds"),
        "item_count": len(items),
        "items": [
            {
                "title": item.get("title", ""),
                "authors": item.get("authors", []),
                "summary": item.get("summary", ""),
                "url": item.get("url", ""),
                "published": item.get("published", "")
            }
            for item in items
        ]
    }


def get_yfinance_news_context(ticker: str, limit=10):
    # 获取 yfinance 的实时新闻并格式化为标准上下文
    items = []
    if news_data_tools is not None:
        try:
            # 真实获取 news 并解析
            raw_news = news_data_tools.fetch_yfinance_news(ticker)
            items = raw_news[:limit]
        except Exception as e:
            print(f"Error fetching live yfinance news: {e}")
            
    return {
        "source_name": "yfinance_news",
        "ticker": ticker,
        "loaded_at": datetime.now().isoformat(timespec="seconds"),
        "item_count": len(items),
        "items": [
            {
                "title": item.get("title", ""),
                "publisher": item.get("publisher", ""),
                "time": datetime.fromtimestamp(item.get("providerPublishTime")).isoformat() if item.get("providerPublishTime") else "",
                "url": item.get("link", "")
            }
            for item in items
        ]
    }


def get_stocktwits_context(ticker: str, limit=30):
    content = ""
    if stocktwits is not None:
        try:
            content = stocktwits.fetch_stocktwits_messages(ticker, limit=limit)
        except Exception as e:
            content = f"Error fetching StockTwits: {e}"
            
    return {
        "source_name": "stocktwits",
        "ticker": ticker,
        "loaded_at": datetime.now().isoformat(timespec="seconds"),
        "raw_text": content
    }


def get_reddit_context(ticker: str, limit_per_sub=5):
    content = ""
    if reddit is not None:
        try:
            content = reddit.fetch_reddit_posts(ticker, limit_per_sub=limit_per_sub)
        except Exception as e:
            content = f"Error fetching Reddit: {e}"
            
    return {
        "source_name": "reddit",
        "ticker": ticker,
        "loaded_at": datetime.now().isoformat(timespec="seconds"),
        "raw_text": content
    }


def build_agent_context(intent: str, ticker=None):
    context = {
        "intent": intent,
        "ticker": ticker,
        "data_sources": {}
    }

    if intent in ["market_analysis", "news_summary"]:
        context["data_sources"]["market_news"] = get_market_news_context()

    if intent == "open_source_analysis":
        context["data_sources"]["opensource_community"] = get_opensource_context()

    if intent == "academic_analysis":
        context["data_sources"]["academic_ai"] = get_academic_context()

    if ticker:
        context["data_sources"]["yfinance_news"] = get_yfinance_news_context(ticker)
        context["data_sources"]["stocktwits"] = get_stocktwits_context(ticker)
        context["data_sources"]["reddit"] = get_reddit_context(ticker)

    return context
