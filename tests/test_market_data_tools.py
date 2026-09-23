import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
STOCK_PAGE = ROOT / "pages" / "stock-analysis-page"
sys.path.insert(0, str(STOCK_PAGE))

from backend.market_data import normalize_screen_quote, search_equities  # noqa: E402


class MarketDataToolsTests(unittest.TestCase):
    def test_normalize_screen_quote_keeps_real_values_and_technical_flags(self):
        row = normalize_screen_quote({
            "symbol": "AAPL", "shortName": "Apple Inc.", "regularMarketPrice": 210,
            "regularMarketChangePercent": 1.25, "marketCap": 3_000_000_000_000,
            "regularMarketVolume": 60_000_000, "averageDailyVolume3Month": 50_000_000,
            "fiftyDayAverage": 205, "twoHundredDayAverage": 190,
        })
        self.assertEqual(row["symbol"], "AAPL")
        self.assertEqual(row["changePct"], 1.25)
        self.assertEqual(row["volumeRatio"], 1.2)
        self.assertTrue(row["above50d"])
        self.assertTrue(row["above200d"])

    @patch("backend.market_data.yf.Search")
    def test_company_name_search_filters_non_equity_results(self, search_cls):
        search_cls.return_value.quotes = [
            {"symbol": "AAPL", "quoteType": "EQUITY", "longname": "Apple Inc.", "exchDisp": "NASDAQ"},
            {"symbol": "AAPL240", "quoteType": "OPTION", "longname": "Option"},
        ]
        results = search_equities("Apple", limit=8)
        self.assertEqual([item["symbol"] for item in results], ["AAPL"])

    @patch("backend.market_data.yf.Search", side_effect=RuntimeError("offline"))
    def test_chinese_company_alias_search_works_without_remote_chinese_support(self, _search_cls):
        results = search_equities("英伟达", limit=8)
        self.assertEqual(results[0]["symbol"], "NVDA")
        self.assertIn("英伟达", results[0]["name"])
        self.assertEqual(results[0]["matchedBy"], "chinese_alias")

    @patch("backend.market_data.yf.Search", side_effect=RuntimeError("offline"))
    def test_chinese_company_alias_supports_typo_tolerance(self, _search_cls):
        results = search_equities("苹裹", limit=8)
        self.assertTrue(results)
        self.assertEqual(results[0]["symbol"], "AAPL")


if __name__ == "__main__":
    unittest.main()
