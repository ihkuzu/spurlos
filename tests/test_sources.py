import imaplib
from typing import ClassVar

import pytest

from spurlos import sources
from spurlos.demo import build_mbox
from spurlos.sources import ImapSource, read_eml_dir, read_mbox


def test_mbox_reads_every_message(tmp_path):
    n = build_mbox(tmp_path / "demo.mbox")
    headers = list(read_mbox(tmp_path / "demo.mbox"))
    assert len(headers) == n
    assert all(h.source == "mbox:demo.mbox" for h in headers)


def test_eml_dir(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.eml").write_bytes(b"From: shop@x.de\r\nSubject: hi\r\n\r\nbody")
    (tmp_path / "two.eml").write_bytes(b"From: news@y.com\nSubject: yo\n\nbody")
    (tmp_path / "skip.txt").write_bytes(b"From: no@z.de\n\n")
    assert sorted(h.address for h in read_eml_dir(tmp_path)) == ["news@y.com", "shop@x.de"]


class FakeImap:
    instances: ClassVar[list] = []

    def __init__(self, host, port):
        self.calls = []
        FakeImap.instances.append(self)

    def login(self, user, password):
        self.calls.append(("login", user))

    def list(self):
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\Noselect) "/" "[Gmail]"',
            b'(\\HasNoChildren) "/" "[Gmail]/Sent Mail"',
        ]

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def uid(self, command, *args):
        self.calls.append(("uid", command) + args)
        if command == "SEARCH":
            return "OK", [b"1 2"]
        return "OK", [
            (b"1 (UID 1 BODY[HEADER.FIELDS (FROM)] {20}", b"From: a@shop.de\r\n\r\n"),
            b")",
            (b"2 (UID 2 BODY[HEADER.FIELDS (FROM)] {20}", b"From: b@bank.de\r\n\r\n"),
            b")",
        ]

    def close(self):
        pass

    def logout(self):
        pass


@pytest.fixture
def fake_imap(monkeypatch):
    FakeImap.instances.clear()
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    return FakeImap


def test_imap_is_read_only_and_never_fetches_bodies(fake_imap):
    src = ImapSource("imap.example.org", "me@example.org", "secret")
    got = [h.address for h in src.headers("INBOX")]
    src.close()
    assert got == ["a@shop.de", "b@bank.de"]
    calls = fake_imap.instances[0].calls
    assert ("select", '"INBOX"', True) in calls
    fetch = next(c for c in calls if c[:2] == ("uid", "FETCH"))
    assert "BODY.PEEK[HEADER.FIELDS (" in fetch[3]
    assert "RFC822" not in fetch[3] and "BODY[]" not in fetch[3]
    for field in sources.FIELDS:
        assert field.upper() in fetch[3]


def test_imap_folders_skip_noselect(fake_imap):
    src = ImapSource("h", "u", "p")
    assert src.folders() == ["INBOX", "[Gmail]/Sent Mail"]
