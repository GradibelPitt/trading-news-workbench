"""Real Yahoo Finance-backed datasets for search, screening, heatmaps and options."""

from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional

import math
import yfinance as yf


SCREENER_PRESETS = (
    "most_actives",
    "day_gainers",
    "day_losers",
    "undervalued_large_caps",
    "growth_technology_stocks",
    "small_cap_gainers",
)

SECTOR_KEYS = (
    "basic-materials", "communication-services", "consumer-cyclical",
    "consumer-defensive", "energy", "financial-services", "healthcare",
    "industrials", "real-estate", "technology", "utilities",
)

CHINESE_SECURITIES = (
    ("AAPL", "苹果公司", "Apple Inc.", "NASDAQ", "Technology", "苹果 苹果公司 apple"),
    ("NVDA", "英伟达", "NVIDIA Corporation", "NASDAQ", "Technology", "英伟达 辉达 nvidia"),
    ("MSFT", "微软", "Microsoft Corporation", "NASDAQ", "Technology", "微软 microsoft"),
    ("GOOGL", "谷歌", "Alphabet Inc.", "NASDAQ", "Communication Services", "谷歌 google alphabet"),
    ("AMZN", "亚马逊", "Amazon.com, Inc.", "NASDAQ", "Consumer Cyclical", "亚马逊 amazon"),
    ("TSLA", "特斯拉", "Tesla, Inc.", "NASDAQ", "Consumer Cyclical", "特斯拉 tesla"),
    ("META", "Meta脸书", "Meta Platforms, Inc.", "NASDAQ", "Communication Services", "脸书 元宇宙 meta facebook"),
    ("NFLX", "奈飞", "Netflix, Inc.", "NASDAQ", "Communication Services", "奈飞 netflix"),
    ("TSM", "台积电", "Taiwan Semiconductor Manufacturing", "NYSE", "Technology", "台积电 台湾积体电路 tsm"),
    ("BABA", "阿里巴巴", "Alibaba Group Holding", "NYSE", "Consumer Cyclical", "阿里 阿里巴巴 alibaba"),
    ("TCEHY", "腾讯", "Tencent Holdings Limited", "OTC", "Communication Services", "腾讯 腾讯控股 tencent"),
    ("BIDU", "百度", "Baidu, Inc.", "NASDAQ", "Communication Services", "百度 baidu"),
    ("JD", "京东", "JD.com, Inc.", "NASDAQ", "Consumer Cyclical", "京东 jd"),
    ("PDD", "拼多多", "PDD Holdings Inc.", "NASDAQ", "Consumer Cyclical", "拼多多 pdd"),
    ("BILI", "哔哩哔哩", "Bilibili Inc.", "NASDAQ", "Communication Services", "哔哩哔哩 b站 bilibili"),
    ("NTES", "网易", "NetEase, Inc.", "NASDAQ", "Communication Services", "网易 netease"),
    ("NIO", "蔚来", "NIO Inc.", "NYSE", "Consumer Cyclical", "蔚来 nio"),
    ("XPEV", "小鹏汽车", "XPeng Inc.", "NYSE", "Consumer Cyclical", "小鹏 小鹏汽车 xpev"),
    ("LI", "理想汽车", "Li Auto Inc.", "NASDAQ", "Consumer Cyclical", "理想 理想汽车 li auto"),
    ("BYDDY", "比亚迪", "BYD Company Limited", "OTC", "Consumer Cyclical", "比亚迪 byd"),
    ("INTC", "英特尔", "Intel Corporation", "NASDAQ", "Technology", "英特尔 intel"),
    ("AMD", "超微半导体", "Advanced Micro Devices", "NASDAQ", "Technology", "超微 amd"),
    ("AVGO", "博通", "Broadcom Inc.", "NASDAQ", "Technology", "博通 broadcom"),
    ("QCOM", "高通", "QUALCOMM Incorporated", "NASDAQ", "Technology", "高通 qualcomm"),
    ("ORCL", "甲骨文", "Oracle Corporation", "NYSE", "Technology", "甲骨文 oracle"),
    ("JPM", "摩根大通", "JPMorgan Chase & Co.", "NYSE", "Financial Services", "摩根大通 jpm"),
    ("GS", "高盛", "The Goldman Sachs Group", "NYSE", "Financial Services", "高盛 goldman sachs"),
    ("KO", "可口可乐", "The Coca-Cola Company", "NYSE", "Consumer Defensive", "可口可乐 coca cola"),
    ("WMT", "沃尔玛", "Walmart Inc.", "NYSE", "Consumer Defensive", "沃尔玛 walmart"),
    ("DIS", "迪士尼", "The Walt Disney Company", "NYSE", "Communication Services", "迪士尼 disney"),
    ("BA", "波音", "The Boeing Company", "NYSE", "Industrials", "波音 boeing"),
)


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in value)


def _chinese_alias_results(query: str, limit: int) -> List[Dict[str, Any]]:
    normalized = "".join(query.casefold().split())
    scored = []
    for symbol, zh_name, en_name, exchange, sector, alias_text in CHINESE_SECURITIES:
        aliases = ["".join(item.casefold().split()) for item in alias_text.split()]
        best = 0.0
        for alias in aliases:
            if normalized == alias:
                best = max(best, 1.0)
            elif normalized in alias or alias in normalized:
                best = max(best, 0.9)
            else:
                best = max(best, SequenceMatcher(None, normalized, alias).ratio())
        if best >= 0.48:
            scored.append((best, {
                "symbol": symbol, "name": f"{zh_name} · {en_name}", "exchange": exchange,
                "quoteType": "EQUITY", "sector": sector, "industry": None,
                "score": round(best * 10000, 2), "matchedBy": "chinese_alias",
            }))
    scored.sort(key=lambda item: (-item[0], item[1]["symbol"]))
    return [item for _, item in scored[:limit]]


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return round(result, 6) if math.isfinite(result) else None


def search_equities(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = _chinese_alias_results(query, limit) if _contains_cjk(query) else []
    seen = {item["symbol"] for item in results}
    try:
        search = yf.Search(
            query,
            max_results=max(1, min(20, limit * 2)),
            news_count=0,
            lists_count=0,
            include_research=False,
            enable_fuzzy_query=True,
            raise_errors=False,
        )
        quotes = search.quotes or []
    except Exception:
        quotes = []
    for quote in quotes:
        quote_type = str(quote.get("quoteType") or "").upper()
        if quote_type not in {"EQUITY", "ETF"}:
            continue
        symbol = str(quote.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        results.append({
            "symbol": symbol,
            "name": quote.get("longname") or quote.get("shortname") or symbol,
            "exchange": quote.get("exchDisp") or quote.get("exchange") or "",
            "quoteType": quote_type,
            "sector": quote.get("sectorDisp") or quote.get("sector"),
            "industry": quote.get("industryDisp") or quote.get("industry"),
            "score": _number(quote.get("score")),
        })
        if len(results) >= limit:
            break
    return results[:limit]


def normalize_screen_quote(quote: Dict[str, Any]) -> Dict[str, Any]:
    volume = _number(quote.get("regularMarketVolume"))
    avg_volume = _number(quote.get("averageDailyVolume3Month"))
    price = _number(quote.get("regularMarketPrice"))
    market_cap = _number(quote.get("marketCap"))
    return {
        "symbol": str(quote.get("symbol") or "").upper(),
        "name": quote.get("displayName") or quote.get("shortName") or quote.get("longName") or quote.get("symbol"),
        "exchange": quote.get("fullExchangeName") or quote.get("exchange"),
        "sector": quote.get("sector") or "Unknown",
        "industry": quote.get("industry") or "Unknown",
        "currency": quote.get("currency") or "USD",
        "price": price,
        "changePct": _number(quote.get("regularMarketChangePercent")),
        "change": _number(quote.get("regularMarketChange")),
        "marketCap": market_cap,
        "volume": volume,
        "averageVolume": avg_volume,
        "volumeRatio": _number(volume / avg_volume) if volume is not None and avg_volume else None,
        "peTtm": _number(quote.get("trailingPE")),
        "peForward": _number(quote.get("forwardPE")),
        "pb": _number(quote.get("priceToBook")),
        "dividendYieldPct": _number(float(quote.get("trailingAnnualDividendYield")) * 100) if quote.get("trailingAnnualDividendYield") is not None else None,
        "beta": _number(quote.get("beta")),
        "fiftyDayAverage": _number(quote.get("fiftyDayAverage")),
        "twoHundredDayAverage": _number(quote.get("twoHundredDayAverage")),
        "fiftyTwoWeekHigh": _number(quote.get("fiftyTwoWeekHigh")),
        "fiftyTwoWeekLow": _number(quote.get("fiftyTwoWeekLow")),
        "marketState": quote.get("marketState"),
        "quoteSource": quote.get("quoteSourceName") or "Yahoo Finance",
        "delayMinutes": _number(quote.get("exchangeDataDelayedBy")),
        "regularMarketTime": quote.get("regularMarketTime"),
        "above50d": bool(price is not None and _number(quote.get("fiftyDayAverage")) is not None and price > float(quote["fiftyDayAverage"])),
        "above200d": bool(price is not None and _number(quote.get("twoHundredDayAverage")) is not None and price > float(quote["twoHundredDayAverage"])),
    }


def fetch_screen_preset(preset: str, count: int = 100) -> List[Dict[str, Any]]:
    if preset not in SCREENER_PRESETS:
        raise ValueError(f"Unsupported screener preset: {preset}")
    response = yf.screen(preset, count=max(1, min(250, count)))
    return [normalize_screen_quote(item) for item in response.get("quotes", []) if item.get("symbol")]


def merge_market_universe(presets: Iterable[str] = SCREENER_PRESETS, count_each: int = 60) -> List[Dict[str, Any]]:
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for preset in presets:
        try:
            rows = fetch_screen_preset(preset, count_each)
        except Exception:
            continue
        for row in rows:
            symbol = row.get("symbol")
            if not symbol:
                continue
            existing = by_symbol.get(symbol)
            if existing is None:
                row["presets"] = [preset]
                by_symbol[symbol] = row
            else:
                existing.setdefault("presets", []).append(preset)
                for key, value in row.items():
                    if existing.get(key) is None and value is not None:
                        existing[key] = value
    # Yahoo screener rows often omit sector metadata.  Enrich only from Yahoo's
    # own sector top-company tables; unknown symbols remain explicitly Unknown.
    sector_map: Dict[str, str] = {}
    for sector_key in SECTOR_KEYS:
        try:
            companies = yf.Sector(sector_key).top_companies
            for symbol in companies.index:
                sector_map[str(symbol).upper()] = sector_key
        except Exception:
            continue
    # Yahoo sector tables sometimes list only one share class.
    if "GOOG" in sector_map:
        sector_map.setdefault("GOOGL", sector_map["GOOG"])
    if "BRK-B" in sector_map:
        sector_map.setdefault("BRK.B", sector_map["BRK-B"])
    for symbol, row in by_symbol.items():
        if row.get("sector") in {None, "", "Unknown"} and symbol in sector_map:
            row["sector"] = sector_map[symbol]
    return list(by_symbol.values())


def option_chain_payload(symbol: str, expiration: Optional[str] = None) -> Dict[str, Any]:
    ticker = yf.Ticker(symbol)
    expirations = list(ticker.options or [])
    if not expirations:
        return {
            "status": "missing",
            "symbol": symbol,
            "expirationDates": [],
            "selectedExpiration": None,
            "calls": [],
            "puts": [],
            "missingFields": ["option_chain"],
            "source": "Yahoo Finance via yfinance",
            "asOf": datetime.now(timezone.utc).isoformat(),
        }
    selected = expiration if expiration in expirations else expirations[0]
    chain = ticker.option_chain(selected)
    history = ticker.history(period="5d", interval="1d", auto_adjust=False)
    underlying = _number(history["Close"].dropna().iloc[-1]) if history is not None and not history.empty else None

    def normalize_contracts(frame) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for record in frame.to_dict(orient="records") if frame is not None else []:
            rows.append({
                "contractSymbol": record.get("contractSymbol"),
                "lastTradeDate": str(record.get("lastTradeDate")) if record.get("lastTradeDate") is not None else None,
                "strike": _number(record.get("strike")),
                "lastPrice": _number(record.get("lastPrice")),
                "bid": _number(record.get("bid")),
                "ask": _number(record.get("ask")),
                "change": _number(record.get("change")),
                "percentChange": _number(record.get("percentChange")),
                "volume": _number(record.get("volume")),
                "openInterest": _number(record.get("openInterest")),
                "impliedVolatility": _number(record.get("impliedVolatility")),
                "inTheMoney": bool(record.get("inTheMoney")),
                "contractSize": record.get("contractSize"),
                "currency": record.get("currency"),
            })
        return rows

    return {
        "status": "ready",
        "symbol": symbol,
        "underlyingPrice": underlying,
        "currency": "USD",
        "asOf": datetime.now(timezone.utc).isoformat(),
        "delayed": True,
        "source": "Yahoo Finance via yfinance",
        "expirationDates": expirations,
        "selectedExpiration": selected,
        "calls": normalize_contracts(chain.calls),
        "puts": normalize_contracts(chain.puts),
        "missingFields": [] if underlying is not None else ["underlyingPrice"],
    }
