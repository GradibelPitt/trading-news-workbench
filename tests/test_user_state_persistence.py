from pathlib import Path
import importlib.util
import tempfile
import unittest

from auth import AuthDB


ROOT = Path(__file__).resolve().parents[1]


def load_stock_cache():
    cache_path = ROOT / "pages" / "stock-analysis-page" / "backend" / "cache.py"
    spec = importlib.util.spec_from_file_location("stock_cache_for_test", cache_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.StockCache


class UserStatePersistenceTests(unittest.TestCase):
    def test_auth_db_persists_watchlist_by_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = AuthDB(str(Path(tmp) / "auth.db"))
            _, admin = db.ensure_admin()
            user = db.create_user("persist_user", "12345")

            db.watchlist_upsert(admin["id"], "NVDA", "NVIDIA", "US", 100.0, 1.2)
            db.watchlist_upsert(user["id"], "AAPL", "Apple", "US", 200.0, -0.5)

            admin_symbols = [item["symbol"] for item in db.watchlist_get(admin["id"])]
            user_symbols = [item["symbol"] for item in db.watchlist_get(user["id"])]

            self.assertEqual(admin_symbols, ["NVDA"])
            self.assertEqual(user_symbols, ["AAPL"])

    def test_stock_cache_can_persist_indicator_preferences_without_expiry(self):
        StockCache = load_stock_cache()
        with tempfile.TemporaryDirectory() as tmp:
            cache = StockCache(str(Path(tmp) / "stock_cache.db"))
            prefs = {
                "volume": True,
                "ma": False,
                "events": True,
                "activeKeys": ["macd", "kdj"],
                "lastReportedAt": "2026-06-16T20:45:00+00:00",
            }

            cache.set_preference("indicator_state:v1", prefs)
            cache.close()
            reloaded = StockCache(str(Path(tmp) / "stock_cache.db"))

            self.assertEqual(reloaded.get_preference("indicator_state:v1"), prefs)
            reloaded.close()

    def test_frontend_loads_persisted_indicators_before_initializing_controls(self):
        js = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("async function loadPersistedIndicatorState()", js)
        self.assertIn("applyPersistedIndicatorState", js)
        self.assertLess(
            js.index("await loadPersistedIndicatorState();"),
            js.index("initIndicatorToggles();"),
        )
        self.assertLess(
            js.index("await loadPersistedIndicatorState();"),
            js.index("init3DControls();"),
        )


if __name__ == "__main__":
    unittest.main()
