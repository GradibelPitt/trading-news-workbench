import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
STOCK_PAGE = ROOT / "pages" / "stock-analysis-page"
sys.path.insert(0, str(STOCK_PAGE))

from backend import app as stock_app  # noqa: E402


class QuoteStatsTests(unittest.TestCase):
    def test_build_quote_stats_uses_history_and_quote_info(self):
        hist = pd.DataFrame(
            [
                {"Open": 100.0, "High": 110.0, "Low": 95.0, "Close": 102.0, "Volume": 1000},
                {"Open": 104.0, "High": 115.0, "Low": 101.0, "Close": 112.0, "Volume": 2500},
            ],
            index=pd.to_datetime(["2026-06-15", "2026-06-16"]),
        )
        info = {
            "marketCap": 1120000000000,
            "sharesOutstanding": 10000000000,
            "floatShares": 8000000000,
            "trailingPE": 32.5,
            "forwardPE": 28.1,
            "priceToBook": 12.34,
            "beta": 1.25,
            "trailingAnnualDividendRate": 0.8,
            "fiftyTwoWeekHigh": 130.0,
            "fiftyTwoWeekLow": 80.0,
        }
        fast_info = {
            "open": 104.5,
            "previous_close": 102.0,
            "day_high": 115.0,
            "day_low": 101.0,
            "year_high": 130.0,
            "year_low": 80.0,
        }

        stats = stock_app._build_quote_stats(hist, info, fast_info, 102.0)

        self.assertEqual(stats["dayHigh"], 115.0)
        self.assertEqual(stats["dayLow"], 101.0)
        self.assertEqual(stats["open"], 104.5)
        self.assertEqual(stats["previousClose"], 102.0)
        self.assertEqual(stats["volume"], 2500.0)
        self.assertEqual(stats["amount"], 280000.0)
        self.assertEqual(stats["marketCap"], 1120000000000.0)
        self.assertEqual(stats["floatMarketCap"], 896000000000.0)
        self.assertEqual(stats["turnoverRatePct"], 0.000031)
        self.assertEqual(stats["amplitudePct"], 13.72549)
        self.assertEqual(stats["peTtm"], 32.5)
        self.assertEqual(stats["pb"], 12.34)
        self.assertEqual(stats["beta"], 1.25)
        self.assertEqual(stats["lotSize"], 1)


if __name__ == "__main__":
    unittest.main()
