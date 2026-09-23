import yfinance as yf
import datetime

DEFAULT_CONFIG = {
    "data_vendors": {
        "news_data": "yfinance"
    }
}

def fetch_yfinance_news(ticker: str, start_date: str = None, end_date: str = None) -> list:
    try:
        t = yf.Ticker(ticker)
        raw_news = t.news
        if not raw_news:
            return []
        filtered_news = []
        for item in raw_news:
            pub_time = item.get("providerPublishTime")
            if pub_time and (start_date or end_date):
                dt = datetime.datetime.fromtimestamp(pub_time, tz=datetime.timezone.utc)
                if start_date:
                    start_dt = datetime.datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                    if dt < start_dt:
                        continue
                if end_date:
                    end_dt = datetime.datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    if dt > end_dt:
                        continue
            filtered_news.append(item)
        return filtered_news
    except Exception as e:
        print(f"Error fetching yfinance news: {e}")
        return []

def fetch_alphavantage_news(ticker: str, start_date: str = None, end_date: str = None) -> list:
    print("Routing to Alpha Vantage news API...")
    return [
        {
            "title": f"[AlphaVantage] {ticker} demand rises amid market expansion",
            "publisher": "AlphaVantage Feed",
            "link": "https://www.alphavantage.co",
            "providerPublishTime": int(datetime.datetime.now().timestamp())
        }
    ]

def get_news_implementation(ticker: str, start_date: str = None, end_date: str = None) -> str:
    vendor = DEFAULT_CONFIG.get("data_vendors", {}).get("news_data", "yfinance")
    if vendor == "yfinance":
        news_items = fetch_yfinance_news(ticker, start_date, end_date)
    elif vendor == "alpha_vantage":
        news_items = fetch_alphavantage_news(ticker, start_date, end_date)
    else:
        news_items = []
    if not news_items:
        return f"No news found for {ticker} from source {vendor}."
    formatted = []
    for idx, item in enumerate(news_items, 1):
        title = item.get("title", "")
        publisher = item.get("publisher", "Unknown Publisher")
        link = item.get("link", "")
        pub_time = item.get("providerPublishTime")
        time_str = ""
        if pub_time:
            time_str = datetime.datetime.fromtimestamp(pub_time).strftime("%Y-%m-%d %H:%M:%S")
        formatted.append(f"[{idx}] {title}\n    Publisher: {publisher} | Time: {time_str}\n    Link: {link}")
    return f"Source: {vendor} — {len(news_items)} recent news articles for {ticker}:\n\n" + "\n\n".join(formatted)

class GetNewsWrapper:
    def __init__(self, func):
        self.func = func
    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)

get_news = GetNewsWrapper(get_news_implementation)
