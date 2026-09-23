"""User authentication and session management for the trading dashboard.

This is a local-desktop-app auth module. It uses:
- pbkdf2_hmac (sha256, 200k iterations) for password hashing
- SHA-256 for server-side session token hashing
- HttpOnly cookies for token delivery (no localStorage tokens)
- SQLite at data/auth.db for persistence

Usernames are restricted to ^[a-zA-Z0-9_-]{3,32}$ to prevent path injection.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_AUTH_DB = DATA_DIR / "auth.db"

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
PASSWORD_MIN_LENGTH = 4


def _hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """Return (password_hash_hex, salt_hex). If salt is None, generate a new one."""
    if salt is None:
        salt = secrets.token_hex(16)
    password_bytes = password.encode("utf-8")
    salt_bytes = salt.encode("utf-8") if isinstance(salt, str) else salt
    digest = hashlib.pbkdf2_hmac("sha256", password_bytes, salt_bytes, 200_000)
    return digest.hex(), salt


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthDB:
    """Manage users and sessions in a local SQLite database."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            db_path = str(DEFAULT_AUTH_DB)
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    username        TEXT UNIQUE NOT NULL,
                    password_hash   TEXT NOT NULL,
                    salt            TEXT NOT NULL,
                    display_name    TEXT DEFAULT '',
                    created_at      REAL NOT NULL,
                    last_login_at   REAL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash  TEXT PRIMARY KEY,
                    user_id     INTEGER NOT NULL,
                    created_at  REAL NOT NULL,
                    expires_at  REAL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);

                CREATE TABLE IF NOT EXISTS watchlist (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    symbol      TEXT    NOT NULL,
                    name        TEXT    DEFAULT '',
                    exchange    TEXT    DEFAULT '',
                    last_price  REAL,
                    change_pct  REAL,
                    updated_at  REAL    NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    UNIQUE(user_id, symbol)
                );

                CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist (user_id);
            """)
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------
    # Watchlist CRUD
    # ------------------------------------------------------------------

    def watchlist_get(self, user_id: int) -> list[dict]:
        """Return all watchlist entries for a user, newest first."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM watchlist WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def watchlist_upsert(self, user_id: int, symbol: str, name: str = "",
                         exchange: str = "", last_price: float = None,
                         change_pct: float = None) -> dict:
        """Insert or update a watchlist entry. Returns the row as dict."""
        now = _now()
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT INTO watchlist (user_id, symbol, name, exchange, last_price, change_pct, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, symbol) DO UPDATE SET
                     name=excluded.name, exchange=excluded.exchange,
                     last_price=excluded.last_price, change_pct=excluded.change_pct,
                     updated_at=excluded.updated_at""",
                (user_id, symbol.upper(), name, exchange, last_price, change_pct, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM watchlist WHERE user_id = ? AND symbol = ?",
                (user_id, symbol.upper()),
            ).fetchone()
            conn.close()
            return dict(row) if row else {}

    def watchlist_remove(self, user_id: int, symbol: str) -> bool:
        """Remove a stock from watchlist. Returns True if deleted."""
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "DELETE FROM watchlist WHERE user_id = ? AND symbol = ?",
                (user_id, symbol.upper()),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            conn.close()
            return deleted

    def ensure_admin(self, password: str = "12345") -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Create the default admin user if it does not exist (idempotent).

        Returns (is_newly_created, user_dict).
        """
        with self._lock:
            conn = self._connect()
            password_hash, salt = _hash_password(password)
            now = _now()
            # INSERT OR IGNORE ensures idempotency across multiple starts
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                ("admin", password_hash, salt, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE username = ?", ("admin",)).fetchone()
            was_new = (row["created_at"] == now) if row else False
            conn.close()
            return was_new, self._row_to_user(row) if row else (False, None)

    def cleanup_expired_sessions(self) -> int:
        """Delete sessions where expires_at is set and has passed. Returns count."""
        now = _now()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "DELETE FROM sessions WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            count = cur.rowcount
            conn.commit()
            conn.close()
            return count

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Verify username/password. Return user dict or None."""
        if not username or not password:
            return None
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            conn.close()
            if not row:
                return None
            user = self._row_to_user(row)
            computed, _ = _hash_password(password, user["salt"])
            if not secrets.compare_digest(computed, user["password_hash"]):
                return None
            # Update last_login_at
            self._update_last_login(user["id"])
            return user

    def create_user(self, username: str, password: str) -> Dict[str, Any]:
        """Create a new user. Raises ValueError on invalid username or duplicate."""
        if not USERNAME_RE.match(username):
            raise ValueError("Username must be 3-32 chars: letters, digits, underscore, hyphen only")
        if len(password) < PASSWORD_MIN_LENGTH:
            raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")

        password_hash, salt = _hash_password(password)
        now = _now()
        with self._lock:
            conn = self._connect()
            existing = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            if existing:
                conn.close()
                raise ValueError(f"Username '{username}' already exists")
            conn.execute(
                "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, salt, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            conn.close()
            return self._row_to_user(row)

    def create_session(self, user_id: int) -> str:
        """Generate a session token, store its hash, return the raw token."""
        self.cleanup_expired_sessions()  # Housekeeping before creating new session
        token = secrets.token_hex(32)  # 64-char hex string
        th = _token_hash(token)
        now = _now()
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at) VALUES (?, ?, ?)",
                (th, user_id, now),
            )
            conn.commit()
            conn.close()
        return token

    def validate_session(self, token: str) -> Optional[Dict[str, Any]]:
        """Look up a session token. Return user dict or None."""
        if not token:
            return None
        th = _token_hash(token)
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT u.* FROM users u
                   JOIN sessions s ON u.id = s.user_id
                   WHERE s.token_hash = ?""",
                (th,),
            ).fetchone()
            conn.close()
            if not row:
                return None
            return self._row_to_user(row)

    def delete_session(self, token: str) -> None:
        """Remove a session (logout)."""
        if not token:
            return
        th = _token_hash(token)
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            conn.commit()
            conn.close()

    def _update_last_login(self, user_id: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (_now(), user_id),
            )
            conn.commit()
            conn.close()

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "password_hash": row["password_hash"],
            "salt": row["salt"],
            "display_name": row["display_name"],
            "created_at": row["created_at"],
            "last_login_at": row["last_login_at"],
        }


def _now() -> float:
    import time
    return time.time()
