"""Lightweight account + device-token store for the relay.

Deliberately minimal (the PRD's v1 is "no account system beyond a per-device
token"; this adds just enough for a self-serve signup/login/download dashboard):
one account -> one device token. Passwords are salted PBKDF2 hashes, never stored
in plaintext. Backed by a single SQLite file; fine for one relay instance.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path

_ITERATIONS = 200_000


def _hash_pw(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS).hex()


class AuthError(Exception):
    pass


class AuthStore:
    def __init__(self, db_path: str | None = None):
        default_db = "/tmp/relay.db" if os.environ.get("VERCEL") else "relay.db"
        self.db_path = db_path or os.environ.get("CONTOUR_RELAY_DB", default_db)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                   email      TEXT PRIMARY KEY,
                   salt       TEXT NOT NULL,
                   pw_hash    TEXT NOT NULL,
                   token      TEXT UNIQUE NOT NULL,
                   created_at INTEGER NOT NULL
               )"""
        )
        self.conn.commit()

    def signup(self, email: str, password: str) -> str:
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise AuthError("a valid email is required")
        if len(password or "") < 8:
            raise AuthError("password must be at least 8 characters")
        if self.conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise AuthError("an account with that email already exists")
        salt = secrets.token_bytes(16)
        token = "contour_" + secrets.token_urlsafe(24)
        self.conn.execute(
            "INSERT INTO users(email,salt,pw_hash,token,created_at) VALUES(?,?,?,?,?)",
            (email, salt.hex(), _hash_pw(password, salt), token, int(time.time())),
        )
        self.conn.commit()
        return token

    def login(self, email: str, password: str) -> str:
        email = (email or "").strip().lower()
        row = self.conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row:
            raise AuthError("no account with that email")
        if _hash_pw(password, bytes.fromhex(row["salt"])) != row["pw_hash"]:
            raise AuthError("incorrect password")
        return row["token"]

    def valid_token(self, token: str) -> bool:
        if not token:
            return False
        return bool(self.conn.execute("SELECT 1 FROM users WHERE token=?", (token,)).fetchone())

    def close(self) -> None:
        self.conn.close()
