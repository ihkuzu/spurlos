"""When a company does not answer or says no: a reminder, then a complaint to the
data protection authority. Both are fixed text with the facts filled in."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import requests as req
from .registry import Registry
from .vault import Vault

# Art. 77 lets you complain where you live; private companies in Germany fall under the state.
STATES = {
    "BW": "debawueldb", "BY": "debaylda", "BE": "deberlbdi", "BB": "debralda",
    "HB": "debrelfdi", "HH": "dehmbbfdi", "HE": "dehessbdi", "MV": "demvldi",
    "NI": "dendslfd", "NW": "denrwldi", "RP": "derlpbdi", "SL": "desaarudz",
    "SN": "desaechsdsb", "ST": "desalbd", "SH": "deshuld", "TH": "detlfdi",
}  # fmt: skip
REMINDER_DAYS = 14

TEXT = {
    "de": {
        "reminder_subject": "Erinnerung: {subject}",
        "reminder": (
            "Guten Tag,\n\n"
            "am {sent} habe ich Ihnen eine Anfrage nach Art. {article} DSGVO geschickt "
            "(Referenz {ref}). {problem}\n\n"
            "Ich bitte Sie, meine Anfrage innerhalb von zwei Wochen ab Eingang dieser Erinnerung "
            "zu beantworten. Andernfalls werde ich mich bei der zuständigen "
            "Datenschutzaufsichtsbehörde beschweren.\n\n"
            "Meine Anfrage im Wortlaut finden Sie unten.\n\n"
            "Mit freundlichen Grüßen\n{name}\n\n--- Meine Anfrage vom {sent} ---\n{original}"
        ),
        "no_answer": "Bis heute habe ich keine Antwort erhalten. Die Frist von einem Monat nach "
        "Art. 12 Abs. 3 DSGVO ist am {due} abgelaufen.",
        "complaint_subject": "Beschwerde nach Art. 77 DSGVO gegen {controller}",
        "complaint": (
            "Guten Tag,\n\n"
            "hiermit lege ich Beschwerde nach Art. 77 DSGVO ein.\n\n"
            "Am {sent} habe ich an den folgenden Verantwortlichen eine Anfrage nach "
            "Art. {article} DSGVO gerichtet:\n{controller}\n{recipient}\n\n"
            "{problem}\n\n"
            "Ich bitte Sie, das Vorgehen des Verantwortlichen zu prüfen und darauf hinzuwirken, "
            "dass er meine Anfrage beantwortet. Für Rückfragen erreichen Sie mich unter "
            "{email}.\n\n"
            "Mit freundlichen Grüßen\n{name}\n\n"
            "--- Meine Anfrage vom {sent} ---\n{original}"
        ),
        "complaint_silent": "Der Verantwortliche hat innerhalb der Frist von einem Monat "
        "(Art. 12 Abs. 3 DSGVO, abgelaufen am {due}) nicht geantwortet.",
        "complaint_reminded": " Auch auf meine Erinnerung vom {reminded} kam keine Antwort.",
        "complaint_refused": "Der Verantwortliche hat meine Anfrage abgelehnt.",
        "reason": " Seine Begründung: {reason}",
        "why": " Ich halte die Ablehnung für unberechtigt: {why}",
    },
    "en": {
        "reminder_subject": "Reminder: {subject}",
        "reminder": (
            "To Whom It May Concern:\n\n"
            "On {sent} I sent you a request under Article {article} GDPR (reference {ref}). "
            "{problem}\n\n"
            "Please answer my request within two weeks of receiving this reminder. Otherwise I "
            "will lodge a complaint with the competent data protection authority.\n\n"
            "You will find my request below.\n\n"
            "Yours sincerely,\n{name}\n\n--- My request of {sent} ---\n{original}"
        ),
        "no_answer": "I have not received an answer. The one-month period under Article 12(3) "
        "GDPR ended on {due}.",
        "complaint_subject": "Complaint under Art. 77 GDPR against {controller}",
        "complaint": (
            "To Whom It May Concern:\n\n"
            "I am hereby lodging a complaint under Article 77 GDPR.\n\n"
            "On {sent} I sent a request under Article {article} GDPR to the following "
            "controller:\n{controller}\n{recipient}\n\n"
            "{problem}\n\n"
            "Please look into this and get the controller to answer my request. You can reach "
            "me at {email}.\n\n"
            "Yours sincerely,\n{name}\n\n"
            "--- My request of {sent} ---\n{original}"
        ),
        "complaint_silent": "The controller did not answer within one month (Article 12(3) "
        "GDPR; the period ended on {due}).",
        "complaint_reminded": " My reminder of {reminded} was not answered either.",
        "complaint_refused": "The controller refused my request.",
        "reason": " Their reason: {reason}",
        "why": " I do not think the refusal is justified: {why}",
    },
}


def _day(value: str | None) -> str:
    return value[:10] if value else "?"


def authority(registry: Registry, profile: dict) -> dict:
    country = (profile.get("country") or "de").lower()
    if country == "de":
        state = (profile.get("state") or "").upper()
        if state not in STATES:
            raise ValueError(
                "set your federal state first, e.g. spurlos profile --state BY "
                f"(one of {', '.join(STATES)})"
            )
        slug = STATES[state]
    else:
        slug = next(
            (a["slug"] for a in registry.authorities if country in a.get("relevant-countries", [])),
            None,
        )
    found = next((a for a in registry.authorities if a["slug"] == slug), None)
    if found is None:
        raise ValueError(f"no data protection authority known for {country}")
    return found


def reminder(vault: Vault, request_id: str, today: date | None = None) -> req.Draft:
    r = vault.request(request_id)
    if r is None or r["kind"] not in req.KINDS:
        raise ValueError(f"no request {request_id} to remind about")
    if r["status"] != "sent":
        raise ValueError(f"{r['id']} is {r['status']}; reminders are for sent requests")
    today = today or datetime.now(timezone.utc).date()
    if r["due"] and date.fromisoformat(r["due"]) >= today:
        raise ValueError(f"{r['id']} is not overdue yet; the answer is due by {r['due']}")
    if any(c["kind"] == "reminder" for c in vault.children(r["id"])):
        raise ValueError(f"{r['id']} already has a reminder")
    t, profile = TEXT[r["lang"]], vault.profile()
    body = t["reminder"].format(
        sent=_day(r["sent"]), article=req.ARTICLE[r["kind"]], ref=r["id"],
        problem=t["no_answer"].format(due=r["due"]), name=profile["name"], original=r["body"],
    )  # fmt: skip
    d = req.Draft(
        id=req.new_ref(), org=r["org"], record=r["record"], kind="reminder", lang=r["lang"],
        recipient=r["recipient"], transport=r["transport"],
        subject=t["reminder_subject"].format(subject=r["subject"]), body=body, parent=r["id"],
    )  # fmt: skip
    vault.add_request(d)
    return d


def complaint(
    vault: Vault, registry: Registry, request_id: str, why: str = "", today: date | None = None
) -> req.Draft:
    r = vault.request(request_id)
    if r is None or r["kind"] not in req.KINDS:
        raise ValueError(f"no request {request_id} to complain about")
    today = today or datetime.now(timezone.utc).date()
    overdue = r["status"] == "sent" and r["due"] and date.fromisoformat(r["due"]) < today
    if r["status"] != "refused" and not overdue:
        raise ValueError(f"{r['id']} is {r['status']}; complain when it is refused or overdue")
    if any(c["kind"] == "complaint" for c in vault.children(r["id"])):
        raise ValueError(f"{r['id']} already has a complaint")
    profile = vault.profile()
    dpa = authority(registry, profile)
    lang = "de" if dpa.get("complaint-language") == "de" else "en"
    t = TEXT[lang]
    if r["status"] == "refused":
        problem = t["complaint_refused"]
        answer = next(
            (x for x in reversed(vault.replies(r["id"])) if x["label"] == "refused"), None
        )
        if answer and answer["summary"]:
            problem += t["reason"].format(reason=answer["summary"])
        if why:
            problem += t["why"].format(why=why)
    else:
        problem = t["complaint_silent"].format(due=r["due"])
        reminded = [c for c in vault.children(r["id"]) if c["kind"] == "reminder" and c["sent"]]
        if reminded:
            problem += t["complaint_reminded"].format(reminded=_day(reminded[0]["sent"]))
    record = registry.records.get(r["record"]) if r["record"] else None
    controller = record.name if record else r["org"]
    if record and record.address:
        controller += ", " + record.address.replace("\n", ", ")
    body = t["complaint"].format(
        sent=_day(r["sent"]), article=req.ARTICLE[r["kind"]], controller=controller,
        recipient=r["recipient"], problem=problem, email=profile["email"],
        name=profile["name"], original=r["body"],
    )  # fmt: skip
    medium = dpa.get("suggested-transport-medium") or ("email" if dpa.get("email") else "webform")
    if medium == "email" and dpa.get("email"):
        recipient, transport = dpa["email"], "email"
    elif dpa.get("webform"):
        recipient, transport = dpa["webform"], "webform"
    elif dpa.get("email"):
        recipient, transport = dpa["email"], "email"
    else:
        recipient, transport = dpa.get("address", "").replace("\n", ", "), "letter"
    d = req.Draft(
        id=req.new_ref(), org=r["org"], record=dpa["slug"], kind="complaint", lang=lang,
        recipient=recipient, transport=transport,
        subject=t["complaint_subject"].format(controller=record.name if record else r["org"]),
        body=body, parent=r["id"],
    )  # fmt: skip
    vault.add_request(d)
    return d


def due_after_sending(kind: str, sent: datetime) -> date:
    day = sent.astimezone(timezone.utc).date()
    if kind == "reminder":
        return day + timedelta(days=REMINDER_DAYS)
    if kind == "complaint":
        # Art. 78(2): the authority has to report back within three months
        return req.add_month(req.add_month(req.add_month(day)))
    return req.add_month(day)


def todo(vault: Vault, today: date | None = None) -> list[tuple[str, str, str]]:
    """What needs doing now, most urgent first: (request, what, command)."""
    today = today or datetime.now(timezone.utc).date()
    items = []
    by_id = {r["id"]: r for r in vault.requests()}
    for r in by_id.values():
        kids = {c["kind"]: c for c in vault.children(r["id"])}
        late = r["status"] == "sent" and r["due"] and date.fromisoformat(r["due"]) < today
        if r["kind"] in req.KINDS and r["status"] == "refused" and "complaint" not in kids:
            items.append((r["id"], f"{r['org']} refused", f"spurlos request complain {r['id']}"))
        elif r["kind"] in req.KINDS and late:
            rem = kids.get("reminder")
            if rem is None:
                items.append((r["id"], f"{r['org']} is overdue since {r['due']}",
                              f"spurlos request remind {r['id']}"))  # fmt: skip
            elif (
                rem["status"] == "sent"
                and rem["due"]
                and date.fromisoformat(rem["due"]) < today
                and "complaint" not in kids
            ):
                items.append((r["id"], f"{r['org']} ignored the reminder",
                              f"spurlos request complain {r['id']}"))  # fmt: skip
        elif r["status"] == "waiting_on_you":
            items.append((r["id"], f"{r['org']} needs something from you",
                          f"spurlos replies list {r['id']}"))  # fmt: skip
        elif r["status"] == "draft":
            items.append((r["id"], f"{r['kind']} to {r['org']} is not sent yet",
                          f"spurlos request send {r['id']}"))  # fmt: skip
    order = ("refused", "ignored", "overdue", "needs", "not sent")
    items.sort(key=lambda i: next((n for n, w in enumerate(order) if w in i[1]), 9))
    return items
