from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_messages (
                    chat_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    PRIMARY KEY (chat_key, fingerprint)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_meta (
                    chat_key TEXT PRIMARY KEY,
                    seeded INTEGER NOT NULL DEFAULT 0,
                    seeded_at TEXT
                )
                """
            )
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
        finally:
            conn.close()

    def is_seeded(self, chat_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT seeded FROM chat_meta WHERE chat_key = ?", (chat_key,)
            ).fetchone()
            return bool(row and row[0])

    def mark_seeded(self, chat_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_meta (chat_key, seeded, seeded_at)
                VALUES (?, 1, ?)
                ON CONFLICT(chat_key) DO UPDATE SET seeded = 1, seeded_at = excluded.seeded_at
                """,
                (chat_key, _utc_now()),
            )
            conn.commit()

    def has_fingerprint(self, chat_key: str, fingerprint: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_messages WHERE chat_key = ? AND fingerprint = ?",
                (chat_key, fingerprint),
            ).fetchone()
            return row is not None

    def add_fingerprint(self, chat_key: str, fingerprint: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO seen_messages (chat_key, fingerprint, first_seen)
                VALUES (?, ?, ?)
                """,
                (chat_key, fingerprint, _utc_now()),
            )
            conn.commit()
