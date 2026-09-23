import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
STOCK_PAGE = ROOT / "pages" / "stock-analysis-page"
sys.path.insert(0, str(STOCK_PAGE))

from backend import app as stock_app  # noqa: E402
from backend.trade_plan.engine import _session_vwap, build_trade_plan  # noqa: E402


def market_frame(periods=120, freq="5min", volume=1000.0):
    index = pd.date_range("2026-07-08 13:30", periods=periods, freq=freq, tz="UTC")
    close = np.linspace(100.0, 106.0, periods) + np.sin(np.arange(periods) / 7.0) * 0.25
    frame = pd.DataFrame(
        {
            "Open": close - 0.08,
            "High": close + 0.30,
            "Low": close - 0.30,
            "Close": close,
            "Volume": np.full(periods, volume),
            "EMA20": pd.Series(close).ewm(span=20, adjust=False).mean().to_numpy(),
            "EMA50": pd.Series(close).ewm(span=50, adjust=False).mean().to_numpy(),
            "ATR14": np.full(periods, 0.62),
            "ADX": np.full(periods, 28.0),
            "CHOP": np.full(periods, 42.0),
            "KC_Middle": pd.Series(close).ewm(span=20, adjust=False).mean().to_numpy(),
        },
        index=index,
    )
    return frame


class TradePlanTests(unittest.TestCase):
    def test_intraday_cache_ttls_are_short(self):
        self.assertEqual(stock_app._interval_cache_ttl("5m"), 90)
        self.assertEqual(stock_app._interval_cache_ttl("15m"), 180)
        self.assertEqual(stock_app._interval_cache_ttl("1d"), 4 * 3600)

    def test_missing_real_volume_blocks_plan(self):
        execution = market_frame(volume=0.0)
        context = market_frame(periods=140, freq="1D")
        plan = build_trade_plan(
            "TEST", "intraday_short", execution, execution, context,
            execution_interval="5m",
            now=datetime(2026, 7, 9, 22, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(plan["status"], "insufficient_data")
        self.assertEqual(plan["action"], "no_trade")
        self.assertIn("unreliable_volume", plan["dataQuality"]["missing"])

    def test_trade_plan_returns_explainable_scenarios(self):
        execution = market_frame()
        context = market_frame(periods=160, freq="1D")
        plan = build_trade_plan(
            "TEST", "intraday_short", execution, execution, context,
            execution_interval="5m",
            now=datetime(2026, 7, 9, 22, 0, tzinfo=timezone.utc),
        )
        self.assertIn(plan["status"], {"ready", "no_trade"})
        self.assertIn(plan["regime"]["name"], {"trend_up", "trend_down", "range", "transition"})
        self.assertIsNotNone(plan["long"])
        self.assertIn("entryZone", plan["long"])
        self.assertIn("sources", plan["long"])
        self.assertIn("profileProxy", plan["levels"])

    def test_session_vwap_uses_latest_market_day_only(self):
        first = market_frame(periods=8)
        second = first.copy()
        second.index = second.index + pd.Timedelta(days=1)
        second[["Open", "High", "Low", "Close"]] += 20
        combined = pd.concat([first, second])
        vwap, _, _ = _session_vwap(combined)
        self.assertGreater(vwap, 118.0)


if __name__ == "__main__":
    unittest.main()
