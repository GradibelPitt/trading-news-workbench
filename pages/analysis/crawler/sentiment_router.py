import sys
import os
import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

try:
    from news_data_tools import get_news
except ImportError:
    from crawler.news_data_tools import get_news

def prefetch_and_analyze_sentiment(ticker: str, start_date: str = None, end_date: str = None) -> dict:
    news_block = get_news.func(ticker, start_date, end_date)
    print(f"Fetched news block for {ticker}: {len(news_block)} bytes")
    return {
        "ticker": ticker,
        "news_block": news_block,
        "status": "prefetched",
        "prefetched_at": datetime.datetime.now().isoformat()
    }

if __name__ == "__main__":
    result = prefetch_and_analyze_sentiment("NVDA")
    print(result["news_block"][:300] + "...")
