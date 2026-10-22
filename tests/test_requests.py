import smtplib
from datetime import date, datetime, timezone
from typing import ClassVar

import pytest

from spurlos import mailer, outbox
from spurlos.cli import main
from spurlos.registry import Record, Registry, pack
from spurlos.requests import add_month, draft, is_reserved, render
from spurlos.vault import Vault

PROFILE = {"name": "Max Mustermann", "email": "max@example.org", "address": "Weg 1, 91052 Erlangen"}


@pytest.fixture
def registry():
    return Registry.load()


def test_render_blocks_fields_and_tags():
    text = "A [x>yes ][y>no ]<italic>all</italic> {who}{gone}\n\n\n\nend"
    assert render(text, {"x"}, {"who": "me"}) == "A yes all me\n\nend"


@pytest.mark.parametrize(
    ("day", "due"),
    [
        (date(2026, 1, 31), date(2026, 2, 28)),
        (date(2028, 1, 31), date(2028, 2, 29)),
        (date(2026, 12, 15), date(2027, 1, 15)),
        (date(2026, 10, 22), date(2026, 11, 22)),
    ],
)
def test_one_month_deadline(day, due):
    assert add_month(day) == due


def test_german_draft_has_reference_and_only_needed_data(registry):
    d = draft(registry, "shop.de", "erasure", PROFILE, "dsb@shop.de", "email", lang="de")
    assert d.subject.startswith("Antrag auf Löschung nach Art. 17 DSGVO (SP-")
    assert d.id in d.subject and f"Referenz: {d.id}" in d.body
    assert "Name: Max Mustermann" in d.body and "max@example.org" in d.body
    assert "Erlangen" not in d.body
    assert "[" not in d.body and "{" not in d.body
    assert d.body.endswith("Mit freundlichen Grüßen\nMax Mustermann")


def test_english_access_with_runs_portability_and_required_address(registry):
    record = Record(slug="x", name="X Ltd", countries=["gb"], runs=["Brand A", "Brand B"],
                    required=[{"desc": "Address", "type": "address"}])  # fmt: skip
    d = draft(registry, "x.co.uk", "access", PROFILE, "p@x.co.uk", "email", record=record,
              portability=True, fields={"Customer number": "42"})  # fmt: skip
    assert d.lang == "en" and d.subject.startswith("Request for access under Art. 15 GDPR")
    assert "Brand A, Brand B" in d.body
    assert "machine-readable format" in d.body
    assert "Postal address: Weg 1, 91052 Erlangen" in d.body
    assert "Customer number: 42" in d.body


def test_objection_and_errors(registry):
    d = draft(registry, "ads.de", "objection", PROFILE, "a@ads.de", "email", lang="en")
    assert "Article 21(2) GDPR" in d.body and d.id in d.body
    with pytest.raises(ValueError, match="kind"):
        draft(registry, "a.de", "rectification", PROFILE, "a@a.de", "email")
    with pytest.raises(ValueError, match="profile"):
        draft(registry, "a.de", "access", {"name": "Max"}, "a@a.de", "email")


def test_reserved_addresses():
    assert is_reserved("dpo@codecamp.example")
    assert is_reserved("a@b.test") and is_reserved("x@localhost.localhost")
    assert not is_reserved("datenschutz@zalando.de")


def vault_with_profile(tmp_path):
    v = Vault(tmp_path / "v.db")
    v.set_profile(**PROFILE)
    return v


def test_outbox_contacts_and_duplicates(tmp_path):
    v = vault_with_profile(tmp_path)
    reg = Registry(pack([{"slug": "shop", "name": "Shop GmbH", "address": "Str 1\nBerlin",
                          "sources": [], "relevant-countries": ["de"], "web": "https://shop.de",
                          "email": "dsb@shop.de"},
                         {"slug": "post", "name": "Post AG", "address": "Str 2\nBonn",
                          "sources": [], "relevant-countries": ["de"], "web": "https://post.de"}],
                        [], Registry.load().templates, ""))  # fmt: skip
    d = outbox.create(v, reg, "mail.shop.de", "access")
    assert (d.org, d.recipient, d.record) == ("shop.de", "dsb@shop.de", "shop")
    with pytest.raises(ValueError, match="already an open access request"):
        outbox.create(v, reg, "shop.de", "access")
    assert outbox.create(v, reg, "post.de", "erasure").transport == "letter"
    with pytest.raises(outbox.NoContact):
        outbox.create(v, reg, "nobody.de", "access")
    v.set_contact("nobody.de", email="privacy@nobody.de")
    assert outbox.create(v, reg, "nobody.de", "access").recipient == "privacy@nobody.de"


def test_mark_sent_and_overdue(tmp_path, registry):
    v = vault_with_profile(tmp_path)
    v.set_contact("shop.de", email="dsb@shop.de")
    d = outbox.create(v, registry, "shop.de", "erasure")
    due = outbox.mark_sent(v, d.id, datetime(2026, 9, 1, 10, tzinfo=timezone.utc), "<m@x>")
    assert due == date(2026, 10, 1)
    row = v.request(d.id.lower())
    assert row["status"] == "sent" and row["message_id"] == "<m@x>"
    [line] = outbox.status_lines(v, today=date(2026, 10, 22))
    assert "OVERDUE by 21 days" in line
    [line] = outbox.status_lines(v, today=date(2026, 9, 20))
    assert "answer due in 11 days" in line
    assert not v.delete_draft(d.id)


def test_build_and_export(tmp_path):
    r = {"id": "SP-AAAAAA", "recipient": "dsb@shop.de", "subject": "S (SP-AAAAAA)", "body": "B"}
    msg = mailer.build(r, "max@example.org", "Max")
    assert msg["Bcc"] == "max@example.org" and "SP-AAAAAA" in msg["Message-ID"]
    path = mailer.export_eml(msg, tmp_path / "a.eml")
    raw = path.read_text()
    assert "X-Unsent: 1" in raw and "Bcc" not in raw and "dsb@shop.de" in raw


class FakeSMTP:
    log: ClassVar[list] = []

    def __init__(self, host, port, **kw):
        FakeSMTP.log.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        FakeSMTP.log.append(("starttls",))

    def login(self, user, password):
        FakeSMTP.log.append(("login", user))

    def send_message(self, msg):
        FakeSMTP.log.append(("send", msg["To"]))


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.log = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def test_send_uses_tls(fake_smtp):
    r = {"id": "SP-AAAAAA", "recipient": "dsb@shop.de", "subject": "S", "body": "B"}
    msg = mailer.build(r, "max@gmx.de", "Max")
    mailer.send(msg, "mail.gmx.net", 587, "max@gmx.de", "pw")
    assert fake_smtp.log == [("connect", "mail.gmx.net", 587), ("starttls",),
                             ("login", "max@gmx.de"), ("send", "dsb@shop.de")]  # fmt: skip
    fake_smtp.log = []
    with pytest.raises(mailer.SendError):
        mailer.send(mailer.build({**r, "recipient": "a@b.example"}, "m@gmx.de", "M"), "h", 465,
                    "u", "p")  # fmt: skip
    assert fake_smtp.log == []


def cli_setup(tmp_path):
    db = str(tmp_path / "v.db")
    main(["--vault", db, "profile", "--name", "Max", "--email", "max@gmail.com"])
    main(["--vault", db, "contact", "shop.de", "--email", "dsb@shop.de"])
    main(["--vault", db, "request", "new", "shop.de", "--kind", "access", "--lang", "en"])
    v = Vault(db)
    rid = v.requests()[0]["id"]
    v.close()
    return db, rid


def test_cli_send_needs_a_yes(tmp_path, monkeypatch, fake_smtp, capsys):
    db, rid = cli_setup(tmp_path)
    monkeypatch.setenv("SPURLOS_SMTP_PASSWORD", "pw")
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert main(["--vault", db, "request", "send", rid]) == 1
    assert fake_smtp.log == []
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert main(["--vault", db, "request", "send", rid]) == 0
    assert ("connect", "smtp.gmail.com", 465) in fake_smtp.log
    assert "answer is due by" in capsys.readouterr().out
    assert main(["--vault", db, "request", "send", rid]) == 1


def test_cli_letter_is_not_emailed(tmp_path, capsys):
    db = str(tmp_path / "v.db")
    main(["--vault", db, "profile", "--name", "Max", "--email", "max@gmail.com"])
    assert main(["--vault", db, "request", "new", "linkedin.com", "--kind", "access"]) == 0
    rid = capsys.readouterr().out.split("draft ")[1].split()[0]
    assert main(["--vault", db, "request", "send", rid, "--yes"]) == 1
    assert "mark-sent" in capsys.readouterr().out
