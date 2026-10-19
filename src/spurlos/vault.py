"""The local SQLite file that holds everything spurlos knows. Nothing leaves the machine."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .headers import Header
from .orgs import org_domain

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    key TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    org TEXT NOT NULL,
    date TEXT,
    subject TEXT NOT NULL,
    unsubscribe INTEGER NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_org ON messages(org);
CREATE TABLE IF NOT EXISTS me (address TEXT PRIMARY KEY);
"""


def default_path() -> Path:
    home = os.environ.get("SPURLOS_HOME")
    base = Path(home) if home else Path.home() / ".spurlos"
    return base / "vault.db"


class Vault:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def add(self, headers: Iterable[Header]) -> tuple[int, int]:
        """Store headers and return (added, already known)."""
        added = seen = 0
        for h in headers:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    h.key,
                    h.address,
                    h.name,
                    h.domain,
                    org_domain(h.domain),
                    h.date.isoformat() if h.date else None,
                    h.subject,
                    int(h.unsubscribe),
                    h.source,
                ),
            )
            if cur.rowcount:
                added += 1
            else:
                seen += 1
        self.db.commit()
        return added, seen

    def add_me(self, *addresses: str) -> None:
        self.db.executemany(
            "INSERT OR IGNORE INTO me VALUES (?)", [(a.strip().lower(),) for a in addresses if a]
        )
        self.db.commit()

    def me(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT address FROM me")}

    def messages(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM messages").fetchall()

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def close(self) -> None:
        self.db.close()
