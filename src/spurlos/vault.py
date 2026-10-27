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
CREATE TABLE IF NOT EXISTS contacts (
    domain TEXT PRIMARY KEY,
    email TEXT,
    webform TEXT,
    name TEXT
);
CREATE TABLE IF NOT EXISTS org_info (
    domain TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    source TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    org TEXT NOT NULL,
    record TEXT,
    kind TEXT NOT NULL,
    lang TEXT NOT NULL,
    recipient TEXT NOT NULL,
    transport TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    created TEXT NOT NULL,
    sent TEXT,
    due TEXT,
    message_id TEXT
);
CREATE TABLE IF NOT EXISTS replies (
    key TEXT PRIMARY KEY,
    request_id TEXT,
    sender TEXT NOT NULL,
    date TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    attachments TEXT NOT NULL,
    matched_by TEXT NOT NULL,
    label TEXT,
    summary TEXT,
    asks_for TEXT,
    source TEXT,
    next_step TEXT,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holdings (
    org TEXT NOT NULL,
    request_id TEXT,
    category TEXT NOT NULL,
    field TEXT NOT NULL,
    count INTEGER NOT NULL,
    example TEXT NOT NULL,
    file TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY (org, category, field)
);
"""


def default_path() -> Path:
    home = os.environ.get("SPURLOS_HOME")
    base = Path(home) if home else Path.home() / ".spurlos"
    return base / "vault.db"


class Vault:
    def __init__(self, path: str | Path | None = None, readonly: bool = False):
        self.path = Path(path) if path else default_path()
        self.readonly = readonly
        if readonly:
            if not self.path.exists():
                raise FileNotFoundError(2, "no vault yet", str(self.path))
            self.db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only = ON")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(requests)")}
        if "parent" not in columns:
            # vaults from before reminders and complaints existed
            self.db.execute("ALTER TABLE requests ADD COLUMN parent TEXT")
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

    def subjects(self, org: str, limit: int = 200) -> list[str]:
        rows = self.db.execute(
            "SELECT subject FROM messages WHERE org = ? ORDER BY date DESC LIMIT ?", (org, limit)
        )
        return [r[0] for r in rows]

    def category(self, org: str) -> tuple[str, str] | None:
        row = self.db.execute(
            "SELECT category, source FROM org_info WHERE domain = ?", (org,)
        ).fetchone()
        return (row[0], row[1]) if row else None

    def set_category(self, org: str, category: str, source: str) -> None:
        if self.readonly:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO org_info VALUES (?,?,?,datetime('now'))",
            (org, category, source),
        )
        self.db.commit()

    def set_contact(
        self,
        org: str,
        email: str | None = None,
        webform: str | None = None,
        name: str | None = None,
    ) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO contacts VALUES (?,?,?,?)", (org, email, webform, name)
        )
        self.db.commit()

    def contact(self, org: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM contacts WHERE domain = ?", (org,)).fetchone()

    def profile(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT key, value FROM profile").fetchall())

    def set_profile(self, **values: str | None) -> None:
        for key, value in values.items():
            if value:
                self.db.execute("INSERT OR REPLACE INTO profile VALUES (?,?)", (key, value))
        self.db.commit()

    def add_request(self, d) -> None:
        self.db.execute(
            "INSERT INTO requests (id, org, record, kind, lang, recipient, transport, subject,"
            " body, status, created, parent)"
            " VALUES (?,?,?,?,?,?,?,?,?,'draft',datetime('now'),?)",
            (
                d.id,
                d.org,
                d.record,
                d.kind,
                d.lang,
                d.recipient,
                d.transport,
                d.subject,
                d.body,
                getattr(d, "parent", None),
            ),
        )
        self.db.commit()

    def request(self, request_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM requests WHERE id = ?", (request_id.upper(),)
        ).fetchone()

    def requests(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            rows = self.db.execute(
                "SELECT * FROM requests WHERE status = ? ORDER BY created", (status,)
            )
        else:
            rows = self.db.execute("SELECT * FROM requests ORDER BY created")
        return rows.fetchall()

    def children(self, request_id: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM requests WHERE parent = ? ORDER BY created", (request_id,)
        ).fetchall()

    def open_request(self, org: str, kind: str) -> sqlite3.Row | None:
        """A draft or sent request of this kind that has no answer yet."""
        return self.db.execute(
            "SELECT * FROM requests WHERE org = ? AND kind = ? AND status IN ('draft', 'sent')",
            (org, kind),
        ).fetchone()

    def mark_sent(self, request_id: str, sent: str, due: str, message_id: str | None) -> None:
        self.db.execute(
            "UPDATE requests SET status = 'sent', sent = ?, due = ?, message_id = ? WHERE id = ?",
            (sent, due, message_id, request_id.upper()),
        )
        self.db.commit()

    def set_status(self, request_id: str, status: str, due: str | None = None) -> None:
        if due:
            self.db.execute(
                "UPDATE requests SET status = ?, due = ? WHERE id = ?", (status, due, request_id)
            )
        else:
            self.db.execute("UPDATE requests SET status = ? WHERE id = ?", (status, request_id))
        self.db.commit()

    def has_reply(self, key: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM replies WHERE key = ?", (key,)).fetchone())

    def add_reply(self, reply, result: dict) -> None:
        req = result.get("request") or {}
        decision = result.get("decision") or {}
        self.db.execute(
            "INSERT OR REPLACE INTO replies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (
                reply.key,
                req.get("id"),
                reply.sender,
                reply.date.isoformat() if reply.date else None,
                reply.subject,
                reply.body,
                ", ".join(reply.attachments),
                result.get("matched_by", "none"),
                result.get("label"),
                result.get("summary"),
                result.get("asks_for"),
                result.get("source"),
                decision.get("next"),
            ),
        )
        self.db.commit()

    def replies(self, request_id: str | None = None) -> list[sqlite3.Row]:
        if request_id:
            rows = self.db.execute(
                "SELECT * FROM replies WHERE request_id = ? ORDER BY date", (request_id.upper(),)
            )
        else:
            rows = self.db.execute("SELECT * FROM replies ORDER BY date")
        return rows.fetchall()

    def relabel(self, key: str, label: str, next_step: str) -> None:
        self.db.execute(
            "UPDATE replies SET label = ?, source = 'you', next_step = ? WHERE key = ?",
            (label, next_step, key),
        )
        self.db.commit()

    def delete_draft(self, request_id: str) -> bool:
        cur = self.db.execute(
            "DELETE FROM requests WHERE id = ? AND status = 'draft'", (request_id.upper(),)
        )
        self.db.commit()
        return bool(cur.rowcount)

    def set_holdings(self, org: str, request_id: str | None, found) -> None:
        """Replace what is known about one organisation with a freshly read export."""
        self.db.execute("DELETE FROM holdings WHERE org = ?", (org,))
        self.db.executemany(
            "INSERT INTO holdings VALUES (?,?,?,?,?,?,?,datetime('now'))",
            [(org, request_id, f.category, f.field, f.count, f.example, f.file) for f in found],
        )
        self.db.commit()

    def holdings(self, org: str | None = None, category: str | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM holdings WHERE 1=1", []
        if org:
            sql, args = sql + " AND org = ?", [*args, org]
        if category:
            sql, args = sql + " AND category = ?", [*args, category]
        try:
            return self.db.execute(sql + " ORDER BY org, category, count DESC", args).fetchall()
        except sqlite3.OperationalError:
            # a read-only vault from before exports existed
            return []

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def close(self) -> None:
        self.db.close()
