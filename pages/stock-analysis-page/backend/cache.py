"""
Persistent SQLite cache for stock-analysis data.

Three-tier architecture:
  L1  in-memory dict  (kept in app.py, 5 min TTL — hot path)
  L2  this module      (SQLite on disk, category-based TTL — survives restart)
  L3  external sources (yfinance / moomoo / futunn — slow, rate-limited)

Thread-safety: sqlite3 in Python already serialises writes when using
check_same_thread=False.  We open one connection per StockCache instance
and rely on WAL mode for concurrent read throughput.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Default TTLs per category (seconds).
DEFAULT_TTL: Dict[str, int] = {
    "ohlcv":   4 * 3600,     # 4 hours  — intraday data changes often
    "meta":    7 * 86400,    # 7 days   — company name / exchange rarely change
    "rating":  24 * 3600,    # 24 hours — analyst consensus updates slowly
    "events":  48 * 3600,    # 48 hours — historical corporate events are stable
    "payload": 5 * 60,       # 5 min    — assembled payload (same as L1)
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    category   TEXT    NOT NULL,
    cache_key  TEXT    NOT NULL,
    data       TEXT    NOT NULL,
    updated_at REAL   NOT NULL,
    PRIMARY KEY (category, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_cache_updated
    ON cache (category, updated_at);

CREATE TABLE IF NOT EXISTS preferences (
    pref_key   TEXT    PRIMARY KEY,
    data       TEXT    NOT NULL,
    updated_at REAL   NOT NULL
);
"""


class StockCache:
    """Persistent per-category cache backed by a local SQLite file."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            data_dir = Path(__file__).resolve().parent / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "stock_cache.db")

        self._db_path = db_path
        # Callers may provide the default data path explicitly (the FastAPI
        # app does this for its per-user cache scope).  Ensure its parent
        # exists on a fresh checkout before SQLite attempts to create the DB.
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        # Stats counters (not persisted — reset on restart).
        self._hits = 0
        self._misses = 0
        self._stale_hits = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(
        self,
        category: str,
        key: str,
        max_age: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return cached data if fresh enough, else ``None``.

        *max_age* defaults to the category's default TTL if not supplied.
        """
        if max_age is None:
            max_age = DEFAULT_TTL.get(category, 300)

        row = self._fetch_row(category, key)
        if row is None:
            self._misses += 1
            return None

        data_json, updated_at = row
        age = time.time() - updated_at
        if age > max_age:
            self._misses += 1
            return None

        self._hits += 1
        try:
            return json.loads(data_json)
        except (json.JSONDecodeError, TypeError):
            return None

    def get_stale(
        self,
        category: str,
        key: str,
        max_stale_age: Optional[int] = None,
        max_age: Optional[int] = None,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Return ``(data, is_stale)`` — returns data even if past TTL.

        *max_stale_age* is the absolute maximum age (in seconds) we are
        willing to serve.  Defaults to 4× the category TTL.  If the data
        is older than this, ``(None, False)`` is returned.
        """
        ttl = max_age if max_age is not None else DEFAULT_TTL.get(category, 300)
        if max_stale_age is None:
            max_stale_age = ttl * 4

        row = self._fetch_row(category, key)
        if row is None:
            return None, False

        data_json, updated_at = row
        age = time.time() - updated_at

        if age > max_stale_age:
            return None, False

        try:
            parsed = json.loads(data_json)
        except (json.JSONDecodeError, TypeError):
            return None, False

        is_stale = age > ttl
        if is_stale:
            self._stale_hits += 1
        else:
            self._hits += 1
        return parsed, is_stale

    def set(self, category: str, key: str, data: Any) -> None:
        """Insert or replace a cache entry."""
        data_json = json.dumps(data, ensure_ascii=False, default=str)
        now = time.time()
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (category, cache_key, data, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (category, key, data_json, now),
        )
        self._conn.commit()

    def get_preference(self, key: str) -> Optional[Dict[str, Any]]:
        """Return a persistent user preference with no TTL expiry."""
        row = self._conn.execute(
            "SELECT data FROM preferences WHERE pref_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None

    def set_preference(self, key: str, data: Any) -> None:
        """Insert or replace a persistent user preference."""
        data_json = json.dumps(data, ensure_ascii=False, default=str)
        now = time.time()
        self._conn.execute(
            "INSERT OR REPLACE INTO preferences (pref_key, data, updated_at) "
            "VALUES (?, ?, ?)",
            (key, data_json, now),
        )
        self._conn.commit()

    def invalidate(self, category: str, key: str) -> None:
        """Remove a single cache entry."""
        self._conn.execute(
            "DELETE FROM cache WHERE category = ? AND cache_key = ?",
            (category, key),
        )
        self._conn.commit()

    def clear(self, category: Optional[str] = None) -> int:
        """Clear all entries, or all entries in *category*.  Returns count deleted."""
        if category:
            cursor = self._conn.execute(
                "DELETE FROM cache WHERE category = ?", (category,)
            )
        else:
            cursor = self._conn.execute("DELETE FROM cache")
        self._conn.commit()
        return cursor.rowcount

    def cleanup(self) -> int:
        """Remove entries older than 4× their category TTL."""
        total = 0
        for cat, ttl in DEFAULT_TTL.items():
            cutoff = time.time() - ttl * 4
            cursor = self._conn.execute(
                "DELETE FROM cache WHERE category = ? AND updated_at < ?",
                (cat, cutoff),
            )
            total += cursor.rowcount
        self._conn.commit()
        return total

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        rows = self._conn.execute(
            "SELECT category, COUNT(*) FROM cache GROUP BY category"
        ).fetchall()
        by_category = {cat: count for cat, count in rows}
        total = sum(by_category.values())
        total_requests = self._hits + self._misses + self._stale_hits
        return {
            "total_entries": total,
            "by_category": by_category,
            "hits": self._hits,
            "misses": self._misses,
            "stale_hits": self._stale_hits,
            "hit_rate": round(self._hits / total_requests, 4) if total_requests else 0,
            "db_path": self._db_path,
        }

    def close(self) -> None:
        """Close the underlying database connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_row(
        self, category: str, key: str
    ) -> Optional[Tuple[str, float]]:
        row = self._conn.execute(
            "SELECT data, updated_at FROM cache "
            "WHERE category = ? AND cache_key = ?",
            (category, key),
        ).fetchone()
        return row
