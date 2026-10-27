import json
import socket
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from http.server import HTTPServer

import pytest

from spurlos import escalate, inbox, outbox
from spurlos.registry import Registry
from spurlos.ui import Api, make_handler
from spurlos.vault import Vault

TODAY = date(2026, 10, 25)


@pytest.fixture
def registry():
    return Registry.load()


def make(tmp_path, registry, sent=date(2026, 9, 1), state="BY"):
    v = Vault(tmp_path / "v.db")
    v.set_profile(name="Max", email="max@gmail.com", state=state)
    v.set_contact("shop.de", email="dsb@shop.de")
    d = outbox.create(v, registry, "shop.de", "erasure")
    when = datetime(sent.year, sent.month, sent.day, tzinfo=timezone.utc)
    outbox.mark_sent(v, d.id, when, "<m1@x>")
    return v, d.id


def test_reminder_only_when_overdue(tmp_path, registry):
    v, rid = make(tmp_path, registry, sent=date(2026, 10, 10))
    with pytest.raises(ValueError, match="not overdue"):
        escalate.reminder(v, rid, today=TODAY)
    v, rid = make(tmp_path / "b", registry)
    d = escalate.reminder(v, rid, today=TODAY)
    assert d.kind == "reminder" and d.parent == rid and d.recipient == "dsb@shop.de"
    assert rid in d.body and "2026-10-01" in d.body and d.subject.startswith("Erinnerung:")
    with pytest.raises(ValueError, match="already has a reminder"):
        escalate.reminder(v, rid, today=TODAY)
    due = outbox.mark_sent(v, d.id, datetime(2026, 10, 25, tzinfo=timezone.utc))
    assert due == date(2026, 11, 8)


def test_complaint_goes_to_the_state_authority(tmp_path, registry):
    v, rid = make(tmp_path, registry)
    d = escalate.complaint(v, registry, rid, today=TODAY)
    assert d.recipient == "poststelle@lda.bayern.de" and d.kind == "complaint"
    assert "Art. 77" in d.body and "2026-10-01" in d.body and "dsb@shop.de" in d.body
    outbox.mark_sent(v, d.id)
    assert v.request(rid)["status"] == "complained"


def test_complaint_after_refusal_quotes_the_reason(tmp_path, registry):
    v, rid = make(tmp_path, registry, sent=date(2026, 10, 20))
    with pytest.raises(ValueError, match="complain when"):
        escalate.complaint(v, registry, rid, today=TODAY)
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["Subject"], m["In-Reply-To"] = "dsb@shop.de", "Re", "<m1@x>"
    m.set_content("Wir lehnen die Löschung ab.")
    inbox.process(v, [bytes(m)], None)
    assert v.request(rid)["status"] == "refused"
    d = escalate.complaint(v, registry, rid, why="I have no contract", today=TODAY)
    assert "abgelehnt" in d.body and "I have no contract" in d.body


def test_authority_needs_a_state_but_not_abroad(tmp_path, registry):
    with pytest.raises(ValueError, match="federal state"):
        escalate.authority(registry, {"country": "de"})
    assert escalate.authority(registry, {"country": "at"})["slug"] == "atdsb"
    assert escalate.authority(registry, {"state": "be"})["slug"] == "deberlbdi"


def test_todo_orders_the_work(tmp_path, registry):
    v, rid = make(tmp_path, registry)
    v.set_contact("bank.de", email="dsb@bank.de")
    outbox.create(v, registry, "bank.de", "access")
    items = escalate.todo(v, today=TODAY)
    assert [i[2].split()[2] for i in items] == ["remind", "send"]
    rem = escalate.reminder(v, rid, today=TODAY)
    outbox.mark_sent(v, rem.id, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert escalate.todo(v, today=TODAY)[0][1] == "shop.de ignored the reminder"


def test_answer_to_a_reminder_settles_the_request(tmp_path, registry):
    v, rid = make(tmp_path, registry)
    rem = escalate.reminder(v, rid, today=TODAY)
    outbox.mark_sent(v, rem.id)
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["Subject"] = "dsb@shop.de", f"Re: Erinnerung ({rem.id})"
    m.set_content("Ihre Daten wurden gelöscht.")
    inbox.process(v, [bytes(m)], None)
    assert v.request(rid)["status"] == "done"
    assert v.request(rem.id)["status"] == "answered"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path, registry):
    v, rid = make(tmp_path, registry)
    v.close()
    port = free_port()
    httpd = HTTPServer(
        ("127.0.0.1", port), make_handler(Api(tmp_path / "v.db", registry), "tok", port)
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", rid
    httpd.shutdown()
    httpd.server_close()


def call(url, token="tok", body=None, host=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None)
    if token:
        req.add_header("X-Spurlos-Token", token)
    if host:
        req.add_header("Host", host)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, res.read(), res.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


def test_ui_needs_token_and_local_host(server):
    base, _ = server
    assert call(base + "/api/overview", token=None)[0] == 403
    assert call(base + "/api/overview", token="wrong")[0] == 403
    assert call(base + "/api/overview", host="evil.example")[0] == 403
    assert call(base + "/?t=nope", token=None)[0] == 403
    status, page, headers = call(base + "/?t=tok", token=None)
    assert status == 200 and b'"tok"' in page
    assert "default-src 'none'" in headers["Content-Security-Policy"]


def test_ui_api_reads_and_acts_but_never_sends(server):
    base, rid = server
    status, body, _ = call(base + "/api/overview")
    o = json.loads(body)
    assert status == 200 and o["todo"][0]["command"].endswith(f"remind {rid}")
    reqs = json.loads(call(base + "/api/requests")[1])
    assert reqs[0]["id"] == rid and reqs[0]["days_left"] < 0 and "body" not in reqs[0]
    status, body, _ = call(base + "/api/remind", body={"id": rid})
    assert status == 200 and json.loads(body)["id"].startswith("SP-")
    status, eml, headers = call(base + f"/api/export/{rid}")
    assert (
        status == 200 and b"X-Unsent: 1" in eml and "attachment" in headers["Content-Disposition"]
    )
    assert call(base + "/api/send", body={"id": rid})[0] == 404
    assert call(base + "/api/draft", body={"domain": "shop.de"})[0] == 400
    assert call(base + "/api/relabel", body={"key": "x", "label": "pirate"})[0] == 400


def test_ui_shows_what_they_know(server, tmp_path, registry):
    base, rid = server
    from spurlos.demo import build_export
    from spurlos.exports import read

    v = Vault(tmp_path / "v.db")
    v.set_holdings("shop.de", rid, read(build_export(tmp_path / "e.zip")).found.values())
    v.close()
    data = json.loads(call(base + "/api/holdings")[1])
    assert data["labels"]["payment"] == "bank and card details"
    assert {"org": "shop.de", "category": "payment", "field": "payment.iban"}.items() <= next(
        r for r in data["rows"] if r["field"] == "payment.iban"
    ).items()
    assert "3704" not in json.dumps(data)
