import json
from datetime import date
from email.message import EmailMessage

import pytest

from spurlos import agent, inbox
from spurlos.cli import main
from spurlos.llm import LLMError, ScriptedLLM
from spurlos.replies import match, parse, strip_quotes
from spurlos.reply_eval import load_cases, run, summarize
from spurlos.reply_labels import LABELS, by_rules, check
from spurlos.vault import Vault


def mail(sender, subject, body, reply_to=None, html=False, attach=None, auto=False):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "max@gmail.com"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 22 Oct 2026 10:00:00 +0000"
    msg["Message-ID"] = f"<{abs(hash((sender, subject, body)))}@test>"
    if reply_to:
        msg["In-Reply-To"] = reply_to
    if auto:
        msg["Auto-Submitted"] = "auto-replied"
    if html:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    if attach:
        msg.add_attachment(b"data", maintype="application", subtype="zip", filename=attach)
    return bytes(msg)


def test_parse_plain_html_attachments_and_auto():
    r = parse(mail("Privacy <dpo@mail.shop.de>", "Re: x", "Hallo", reply_to="<a@b>",
                   attach="export.zip", auto=True))  # fmt: skip
    assert (r.sender, r.org, r.in_reply_to, r.attachments, r.auto) == (
        "dpo@mail.shop.de", "shop.de", ["<a@b>"], ["export.zip"], True)  # fmt: skip
    h = parse(
        mail("a@shop.de", "x", "<p>Ihre Daten wurden <b>gelöscht</b>.</p><br>Gruß", html=True)
    )
    assert "Ihre Daten wurden gelöscht ." in h.body.replace("  ", " ")
    assert "<" not in h.body


def test_strip_quotes():
    body = "Daten gelöscht.\n\nAm 22.10.2026 um 10:00 schrieb Max <max@gmail.com>:\n> Bitte löschen"
    assert strip_quotes(body) == "Daten gelöscht."
    assert strip_quotes("Done.\n\n-----Original Message-----\nFrom: x") == "Done."
    assert strip_quotes("> quoted\nmine") == "mine"


def req(rid, org="shop.de", status="sent", mid=None, sent="2026-10-01", kind="erasure"):
    return {"id": rid, "org": org, "recipient": f"dsb@{org}", "status": status, "kind": kind,
            "message_id": mid, "sent": sent, "due": "2026-11-01"}  # fmt: skip


def test_match_order():
    reqs = [req("SP-AAAAAA", mid="<m1@x>"), req("SP-BBBBBB"), req("SP-CCCCCC", org="bank.de")]
    r = parse(mail("dsb@shop.de", "Re: SP-BBBBBB", "x", reply_to="<m1@x>"))
    assert match(r, reqs)[0]["id"] == "SP-AAAAAA" and match(r, reqs)[1] == "thread"
    r = parse(mail("dsb@shop.de", "Re: Antrag (SP-BBBBBB)", "x"))
    assert match(r, reqs) == (reqs[1], "reference")
    r = parse(mail("info@bank.de", "Ihre Anfrage", "x"))
    assert match(r, reqs) == (reqs[2], "sender")
    r = parse(mail("info@shop.de", "Ihre Anfrage", "x"))
    assert match(r, reqs) == (None, "ambiguous")
    r = parse(mail("x@other.de", "hi", "x"))
    assert match(r, [req("SP-DDDDDD", status="draft")]) == (None, "none")


def test_rules_and_check():
    assert by_rules("Re", "Ihre Daten wurden gelöscht.") == "confirmed"
    assert by_rules("Out of office", "back Monday") == "auto_reply"
    assert by_rules("x", "anything", auto=True) == "auto_reply"
    assert by_rules("x", "nothing known here") == "acknowledged"
    assert check({"label": "refused", "summary": "s"}) == ""
    assert "not one of" in check({"label": "maybe"})


def run_agent(llm, body="Ihre Daten wurden gelöscht.", auto=False, kind="erasure"):
    reqs = [req("SP-AAAAAA", mid="<m1@x>", kind=kind)]
    reply = parse(mail("dsb@shop.de", "Re", body, reply_to="<m1@x>", auto=auto))
    return agent.run(llm, reqs, reply)


def test_agent_uses_model_answer():
    llm = ScriptedLLM([{"label": "confirmed", "summary": "Deleted.", "asks_for": ""}])
    s = run_agent(llm)
    assert (s["label"], s["source"], s["tries"]) == ("confirmed", "scripted", 1)
    assert s["decision"]["status"] == "done"
    assert "<<<" in llm.prompts[0] and "erasure" in llm.prompts[0]


def test_agent_retries_once_with_the_reason():
    llm = ScriptedLLM([{"label": "deleted", "summary": ""},
                       {"label": "confirmed", "summary": "ok", "asks_for": ""}])  # fmt: skip
    s = run_agent(llm)
    assert s["label"] == "confirmed" and s["tries"] == 2
    assert "rejected" in llm.prompts[1] and "'deleted'" in llm.prompts[1]


def test_agent_falls_back_to_rules():
    s = run_agent(ScriptedLLM([{"label": "x"}, {"label": "y"}]))
    assert s["label"] == "confirmed" and s["source"] == "rules (model answer invalid)"

    class Broken:
        name = "broken"

        def json(self, *a):
            raise LLMError("down")

    s = run_agent(Broken())
    assert s["label"] == "confirmed" and s["source"].startswith("rules (model failed")


def test_auto_reply_header_skips_the_model():
    llm = ScriptedLLM([])
    s = run_agent(llm, auto=True)
    assert s["label"] == "auto_reply" and s["source"] == "header" and llm.prompts == []
    assert s["decision"]["status"] is None


@pytest.mark.parametrize(
    ("label", "status"),
    [("confirmed", "done"), ("no_data", "done"), ("data_provided", "answered"),
     ("id_required", "waiting_on_you"), ("refused", "refused"), ("acknowledged", None),
     ("auto_reply", None)],
)  # fmt: skip
def test_decide(label, status):
    assert agent.decide("access", label, "", "2026-11-01")["status"] == status


def test_extension_adds_two_months():
    d = agent.decide("access", "extension", "", "2026-11-30")
    assert d["status"] == "sent" and d["due"] == "2027-01-30"
    assert set(LABELS) >= {"extension", "refused"}


def setup_vault(tmp_path):
    v = Vault(tmp_path / "v.db")
    v.set_profile(name="Max", email="max@gmail.com")
    v.set_contact("shop.de", email="dsb@shop.de")
    from spurlos import outbox
    from spurlos.registry import Registry

    d = outbox.create(v, Registry.load(), "shop.de", "erasure")
    outbox.mark_sent(v, d.id, message_id="<m1@x>")
    return v, d.id


def test_process_only_keeps_replies_to_requests(tmp_path):
    v, rid = setup_vault(tmp_path)
    raws = [
        mail("max@gmail.com", f"Antrag ({rid})", "my own copy"),
        mail("news@other.de", "Sale!", "50% off"),
        mail("dsb@shop.de", "Re: Antrag", "Ihre Daten wurden gelöscht.", reply_to="<m1@x>"),
    ]
    results = inbox.process(v, raws, None)
    assert len(results) == 1 and len(v.replies()) == 1
    assert v.request(rid)["status"] == "done"
    assert inbox.process(v, raws, None) == []
    assert "confirmed" in inbox.format_results(results)


def test_dry_run_changes_nothing(tmp_path):
    v, rid = setup_vault(tmp_path)
    raw = mail("dsb@shop.de", "Re", "Wir lehnen die Löschung ab.", reply_to="<m1@x>")
    inbox.process(v, [raw], None, apply=False)
    assert v.request(rid)["status"] == "sent" and v.replies()[0]["label"] == "refused"


def test_imap_criteria():
    crit = inbox.imap_criteria({"shop.de"}, date(2026, 10, 1))
    assert crit == [["SINCE", "01-Oct-2026", "SUBJECT", "SP-"],
                    ["SINCE", "01-Oct-2026", "FROM", "shop.de"]]  # fmt: skip


def test_cli_replies_flow(tmp_path, capsys):
    db = str(tmp_path / "v.db")
    main(["--vault", db, "profile", "--name", "Max", "--email", "max@gmail.com"])
    main(["--vault", db, "contact", "shop.de", "--email", "dsb@shop.de"])
    main(["--vault", db, "request", "new", "shop.de", "--kind", "access"])
    v = Vault(db)
    rid = v.requests()[0]["id"]
    v.close()
    main(["--vault", db, "request", "mark-sent", rid])
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "a.eml").write_bytes(
        mail("dsb@shop.de", f"Re: ({rid})", "Bitte senden Sie uns eine Kopie Ihres Ausweises.")
    )
    capsys.readouterr()
    assert main(["--vault", db, "replies", "import", str(tmp_path / "in")]) == 0
    out = capsys.readouterr().out
    assert "id_required" in out
    assert main(["--vault", db, "replies", "list"]) == 0
    key = capsys.readouterr().out.split()[0]
    assert main(["--vault", db, "replies", "relabel", key, "data_provided"]) == 0
    v = Vault(db)
    assert v.request(rid)["status"] == "answered"


def test_eval_set_and_rules_baseline(tmp_path, capsys):
    cases = load_cases()
    assert len(cases) == 40 and {c["label"] for c in cases} == set(LABELS)
    s = summarize(run(cases))
    assert s["total"] == 40 and s["correct"] >= 25
    out = tmp_path / "r.json"
    assert main(["eval-replies", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["rules"]["summary"]["total"] == 40


def test_eval_with_scripted_model():
    cases = load_cases()[:3]
    answers = [{"label": c["label"], "summary": "", "asks_for": ""} for c in cases]
    s = summarize(run(cases, ScriptedLLM(answers)))
    assert s["correct"] == 3
