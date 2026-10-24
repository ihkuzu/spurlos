"""Read message headers from an mbox file, a folder of .eml files or an IMAP account."""

from __future__ import annotations

import imaplib
import mailbox
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from .headers import FIELDS, Header, parse_header

BATCH = 200


def _head(raw: bytes) -> bytes:
    for sep in (b"\r\n\r\n", b"\n\n"):
        idx = raw.find(sep)
        if idx != -1:
            return raw[: idx + len(sep)]
    return raw


def read_mbox(path: str | Path) -> Iterator[Header]:
    if not Path(path).is_file():
        raise FileNotFoundError(2, "no such file", str(path))
    box = mailbox.mbox(str(path), create=False)
    try:
        for key in box.iterkeys():
            header = parse_header(_head(box.get_bytes(key)), source=f"mbox:{Path(path).name}")
            if header:
                yield header
    finally:
        box.close()


def raw_messages(path: str | Path) -> Iterator[bytes]:
    """Whole messages from an mbox file or a folder of .eml files."""
    path = Path(path)
    if path.is_dir():
        for file in sorted(path.rglob("*.eml")):
            yield file.read_bytes()
        return
    if not path.is_file():
        raise FileNotFoundError(2, "no such file", str(path))
    box = mailbox.mbox(str(path), create=False)
    try:
        for key in box.iterkeys():
            yield box.get_bytes(key)
    finally:
        box.close()


def read_eml_dir(path: str | Path) -> Iterator[Header]:
    if not Path(path).is_dir():
        raise FileNotFoundError(2, "no such folder", str(path))
    for file in sorted(Path(path).rglob("*.eml")):
        header = parse_header(_head(file.read_bytes()), source=f"eml:{file.name}")
        if header:
            yield header


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ImapSource:
    """Read-only IMAP access that only ever asks the server for header fields."""

    def __init__(self, host: str, user: str, password: str, port: int = 993):
        self.conn = imaplib.IMAP4_SSL(host, port)
        self.conn.login(user, password)

    def folders(self) -> list[str]:
        status, rows = self.conn.list()
        if status != "OK":
            return []
        names = []
        for row in rows or []:
            line = row.decode("utf-8", "replace") if isinstance(row, bytes) else str(row)
            if "\\Noselect" in line:
                continue
            names.append(line.rsplit(' "', 1)[-1].rstrip('"') if ' "' in line else line.split()[-1])
        return names

    def headers(self, folder: str = "INBOX", since: date | None = None) -> Iterator[Header]:
        status, _ = self.conn.select(_quote(folder), readonly=True)
        if status != "OK":
            raise RuntimeError(f"cannot open folder {folder}")
        query = ["ALL"] if since is None else ["SINCE", since.strftime("%d-%b-%Y")]
        status, data = self.conn.uid("SEARCH", None, *query)
        if status != "OK":
            raise RuntimeError(f"search failed in {folder}")
        uids = data[0].split() if data and data[0] else []
        wanted = "BODY.PEEK[HEADER.FIELDS (" + " ".join(f.upper() for f in FIELDS) + ")]"
        for start in range(0, len(uids), BATCH):
            chunk = b",".join(uids[start : start + BATCH]).decode()
            status, rows = self.conn.uid("FETCH", chunk, f"({wanted})")
            if status != "OK":
                continue
            for row in rows or []:
                if isinstance(row, tuple) and len(row) == 2:
                    header = parse_header(row[1], source=f"imap:{folder}")
                    if header:
                        yield header

    def search(self, folder: str, *criteria: str) -> list[bytes]:
        status, _ = self.conn.select(_quote(folder), readonly=True)
        if status != "OK":
            raise RuntimeError(f"cannot open folder {folder}")
        status, data = self.conn.uid("SEARCH", None, *criteria)
        if status != "OK":
            return []
        return data[0].split() if data and data[0] else []

    def full_messages(self, folder: str, uids: list[bytes]) -> Iterator[bytes]:
        """Whole messages, only for the UIDs asked for; BODY.PEEK keeps them unread."""
        self.conn.select(_quote(folder), readonly=True)
        for start in range(0, len(uids), 50):
            chunk = b",".join(uids[start : start + 50]).decode()
            status, rows = self.conn.uid("FETCH", chunk, "(BODY.PEEK[])")
            if status != "OK":
                continue
            for row in rows or []:
                if isinstance(row, tuple) and len(row) == 2:
                    yield row[1]

    def close(self) -> None:
        try:
            self.conn.close()
        except imaplib.IMAP4.error:
            pass
        self.conn.logout()
