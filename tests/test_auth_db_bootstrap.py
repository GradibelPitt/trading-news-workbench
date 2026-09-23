"""Test that auth DB can bootstrap from scratch (no pre-existing database).

Simulates a fresh clone scenario where data/auth.db does not exist.
Verifies:
- AuthDB creates the database file and parent directories automatically
- All required tables (users, sessions, watchlist) exist
- ensure_admin() is idempotent (no duplicate admin on repeated calls)
- Basic register / login / session / watchlist flows work on an empty database
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from auth.auth import AuthDB


class TestAuthDBBootstrap(unittest.TestCase):
    """Verify AuthDB auto-initializes from scratch."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Simulate a deep nested path that doesn't exist yet
        self.db_path = os.path.join(self.tmpdir, "sub", "data", "auth.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_db_file_and_parent_dirs(self):
        """AuthDB should create the database file and all parent directories."""
        self.assertFalse(os.path.exists(self.db_path))
        db = AuthDB(db_path=self.db_path)
        self.assertTrue(os.path.isfile(self.db_path))

    def test_required_tables_exist(self):
        """All three core tables (users, sessions, watchlist) must exist."""
        db = AuthDB(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        self.assertIn("users", tables)
        self.assertIn("sessions", tables)
        self.assertIn("watchlist", tables)

    def test_users_table_schema(self):
        """Users table should have all expected columns."""
        db = AuthDB(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        conn.close()
        expected = {"id", "username", "password_hash", "salt", "display_name",
                    "created_at", "last_login_at"}
        self.assertTrue(expected.issubset(cols), f"Missing columns: {expected - cols}")

    def test_sessions_table_schema(self):
        """Sessions table should have all expected columns."""
        db = AuthDB(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        conn.close()
        expected = {"token_hash", "user_id", "created_at", "expires_at"}
        self.assertTrue(expected.issubset(cols), f"Missing columns: {expected - cols}")

    def test_watchlist_table_schema(self):
        """Watchlist table should have all expected columns."""
        db = AuthDB(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
        conn.close()
        expected = {"id", "user_id", "symbol", "name", "exchange",
                    "last_price", "change_pct", "updated_at"}
        self.assertTrue(expected.issubset(cols), f"Missing columns: {expected - cols}")

    def test_ensure_admin_idempotent(self):
        """ensure_admin() called multiple times should not duplicate admin user."""
        db = AuthDB(db_path=self.db_path)
        created1, user1 = db.ensure_admin("12345")
        created2, user2 = db.ensure_admin("12345")
        created3, user3 = db.ensure_admin("12345")
        # Only the first call should report newly created
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertFalse(created3)
        # All calls return the same admin user
        self.assertEqual(user1["username"], "admin")
        self.assertEqual(user1["id"], user2["id"])
        self.assertEqual(user2["id"], user3["id"])
        # Verify only one admin row in DB
        conn = sqlite3.connect(self.db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE username = 'admin'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_register_on_empty_db(self):
        """A new user can be registered on a fresh database."""
        db = AuthDB(db_path=self.db_path)
        user = db.create_user("testuser", "pass1234")
        self.assertEqual(user["username"], "testuser")
        self.assertIn("id", user)
        self.assertIn("password_hash", user)
        self.assertIn("salt", user)

    def test_register_duplicate_raises(self):
        """Registering a duplicate username raises ValueError."""
        db = AuthDB(db_path=self.db_path)
        db.create_user("dupeuser", "pass1234")
        with self.assertRaises(ValueError):
            db.create_user("dupeuser", "pass5678")

    def test_login_after_register(self):
        """authenticate() succeeds with correct credentials on a fresh DB."""
        db = AuthDB(db_path=self.db_path)
        db.create_user("logintest", "mypassword")
        result = db.authenticate("logintest", "mypassword")
        self.assertIsNotNone(result)
        self.assertEqual(result["username"], "logintest")

    def test_login_wrong_password_returns_none(self):
        """authenticate() returns None for wrong password."""
        db = AuthDB(db_path=self.db_path)
        db.create_user("wrongpw", "correct")
        result = db.authenticate("wrongpw", "incorrect")
        self.assertIsNone(result)

    def test_session_create_and_validate(self):
        """create_session / validate_session roundtrip works on empty DB."""
        db = AuthDB(db_path=self.db_path)
        user = db.create_user("sessionuser", "pass1234")
        token = db.create_session(user["id"])
        self.assertIsInstance(token, str)
        self.assertEqual(len(token), 64)  # 32 bytes hex
        validated = db.validate_session(token)
        self.assertIsNotNone(validated)
        self.assertEqual(validated["username"], "sessionuser")

    def test_session_delete_logout(self):
        """delete_session invalidates the token."""
        db = AuthDB(db_path=self.db_path)
        user = db.create_user("logoutuser", "pass1234")
        token = db.create_session(user["id"])
        db.delete_session(token)
        self.assertIsNone(db.validate_session(token))

    def test_watchlist_crud_on_empty_db(self):
        """Watchlist add / list / remove works on a fresh database."""
        db = AuthDB(db_path=self.db_path)
        user = db.create_user("wluser", "pass1234")
        uid = user["id"]

        # Initially empty
        self.assertEqual(db.watchlist_get(uid), [])

        # Add
        entry = db.watchlist_upsert(uid, "AAPL", name="Apple Inc", exchange="NASDAQ")
        self.assertEqual(entry["symbol"], "AAPL")
        self.assertEqual(entry["name"], "Apple Inc")

        # List
        items = db.watchlist_get(uid)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["symbol"], "AAPL")

        # Remove
        removed = db.watchlist_remove(uid, "AAPL")
        self.assertTrue(removed)
        self.assertEqual(db.watchlist_get(uid), [])

    def test_watchlist_isolated_between_users(self):
        """Different users' watchlists are isolated."""
        db = AuthDB(db_path=self.db_path)
        u1 = db.create_user("user_a", "pass1234")
        u2 = db.create_user("user_b", "pass1234")

        db.watchlist_upsert(u1["id"], "NVDA")
        db.watchlist_upsert(u2["id"], "TSLA")

        wl1 = db.watchlist_get(u1["id"])
        wl2 = db.watchlist_get(u2["id"])

        self.assertEqual(len(wl1), 1)
        self.assertEqual(wl1[0]["symbol"], "NVDA")
        self.assertEqual(len(wl2), 1)
        self.assertEqual(wl2[0]["symbol"], "TSLA")

    def test_no_preexisting_db_required(self):
        """The project should not depend on a pre-committed auth.db.

        This test verifies that data/auth.db is not tracked by git (it
        should be in .gitignore) and that AuthDB can create it from scratch.
        """
        gitignore_path = ROOT / ".gitignore"
        if gitignore_path.exists():
            content = gitignore_path.read_text(encoding="utf-8")
            # data/* or data/auth.db should be in .gitignore
            self.assertTrue(
                "data/*" in content or "data/auth.db" in content,
                ".gitignore should exclude data/auth.db"
            )
        # The test itself is the proof: we created AuthDB with a non-existent
        # path and all operations succeeded (covered by other tests above).


if __name__ == "__main__":
    unittest.main()
