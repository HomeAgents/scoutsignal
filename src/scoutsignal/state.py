from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    """Persistent dedup store backed by SQLite.

    Keeps a single connection for the lifetime of the instance.
    Call :meth:`close` (or use as a context manager) when done.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = sqlite3.connect(path)
        self._init_db()

    # -- lifecycle ------------------------------------------------------------

    def _init_db(self) -> None:
        c = self._conn
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_messages (
                chat_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                PRIMARY KEY (chat_key, fingerprint)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_meta (
                chat_key TEXT PRIMARY KEY,
                seeded INTEGER NOT NULL DEFAULT 0,
                seeded_at TEXT
            )
            """
        )
        c.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # -- pruning --------------------------------------------------------------

    def prune_old(self, days: int = 30) -> int:
        """Delete seen_messages rows where *first_seen* is older than *days* days.

        Returns the number of rows removed.
        """
        deleted = self._conn.execute(
            """
            DELETE FROM seen_messages
            WHERE first_seen < datetime('now', ? || ' days')
            """,
            (str(-days),),
        ).rowcount
        self._conn.commit()
        if deleted:
            log.info("Pruned %d fingerprints older than %d days.", deleted, days)
        return deleted

    # -- queries --------------------------------------------------------------

    def is_seeded(self, chat_key: str) -> bool:
        row = self._conn.execute(
            "SELECT seeded FROM chat_meta WHERE chat_key = ?", (chat_key,)
        ).fetchone()
        return bool(row and row[0])

    def mark_seeded(self, chat_key: str) -> None:
        self._conn.execute(
            """
            INSERT INTO chat_meta (chat_key, seeded, seeded_at)
            VALUES (?, 1, ?)
            ON CONFLICT(chat_key) DO UPDATE SET seeded = 1, seeded_at = excluded.seeded_at
            """,
            (chat_key, _utc_now()),
        )
        self._conn.commit()

    def has_fingerprint(self, chat_key: str, fingerprint: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_messages WHERE chat_key = ? AND fingerprint = ?",
            (chat_key, fingerprint),
        ).fetchone()
        return row is not None

    def add_fingerprint(self, chat_key: str, fingerprint: str) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO seen_messages (chat_key, fingerprint, first_seen)
            VALUES (?, ?, ?)
            """,
            (chat_key, fingerprint, _utc_now()),
        )
        self._conn.commit()
