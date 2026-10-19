"""A made-up mailbox so the tool can be tried without touching a real account."""

from __future__ import annotations

import mailbox
import random
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

ME = "max.mustermann@example.org"

# name, sender address, subjects, newsletter, years active (from, to) counted back from now
COMPANIES = [
    (
        "Brotkorb Shop",
        "bestellung@mail.brotkorb-shop.de",
        ["Ihre Bestellung #{n}", "Ihre Lieferung kommt heute", "Neue Angebote für Sie"],
        True,
        (6, 0),
    ),
    (
        "Schienenwerk",
        "noreply@schienenwerk.de",
        ["Ihre Fahrkarte", "Buchungsbestätigung {n}", "Verspätung Ihrer Verbindung"],
        False,
        (5, 0),
    ),
    (
        "Kontora Bank",
        "service@kontora-bank.de",
        ["Neues Dokument in Ihrem Postfach", "Ihr Kontoauszug"],
        False,
        (4, 0),
    ),
    (
        "FitWerk Studios",
        "info@fitwerk-studios.de",
        ["Willkommen bei FitWerk", "Ihr Mitgliedsbeitrag", "Kurse diese Woche"],
        True,
        (7, 3),
    ),
    (
        "Pixelforum",
        "notifications@pixelforum.net",
        ["Welcome to Pixelforum", "Someone replied to your post", "Weekly digest"],
        True,
        (11, 8),
    ),
    (
        "TravelNest",
        "deals@news.travelnest.com",
        ["Your booking is confirmed", "Last chance: 30% off", "Rate your stay"],
        True,
        (6, 2),
    ),
    (
        "Stromfix",
        "kundenservice@stromfix.de",
        ["Ihre Jahresabrechnung", "Abschlag angepasst"],
        False,
        (3, 0),
    ),
    (
        "CodeCamp Online",
        "hello@codecamp.io",
        ["Confirm your email", "Your certificate is ready", "New course: Python"],
        True,
        (9, 6),
    ),
    (
        "Mietradar",
        "alerts@mietradar.de",
        ["Neue Wohnungen in Erlangen", "Ihr Suchauftrag"],
        True,
        (4, 3),
    ),
    (
        "Lesefuchs",
        "newsletter@lesefuchs.de",
        ["Buchtipps im {month}", "Ihre Bestellung wurde versendet"],
        True,
        (10, 1),
    ),
    (
        "Kartenhaus Games",
        "no-reply@kartenhaus-games.com",
        ["Verify your account", "Password reset request"],
        False,
        (12, 10),
    ),
    (
        "Apotheke Rosengarten",
        "shop@apotheke-rosengarten.de",
        ["Ihre Bestellung", "Ihr Rezept ist da"],
        False,
        (2, 0),
    ),
]

FRIENDS = ["anna.schmidt@gmail.com", "mehmet.yilmaz@web.de", "lukas.weber@gmx.de"]


def build_mbox(path: str | Path, seed: int = 7, now: datetime | None = None) -> int:
    rng = random.Random(seed)
    now = now or datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    box = mailbox.mbox(str(path))
    count = 0

    def add(sender: str, name: str, subject: str, when: datetime, unsubscribe: bool) -> None:
        nonlocal count
        msg = EmailMessage()
        msg["From"] = f"{name} <{sender}>"
        msg["To"] = ME
        msg["Date"] = format_datetime(when)
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=sender.split("@")[1])
        if unsubscribe:
            msg["List-Unsubscribe"] = f"<mailto:unsubscribe@{sender.split('@')[1]}>"
        msg.set_content("Demo message.")
        box.add(msg)
        count += 1

    for name, sender, subjects, news, (start, end) in COMPANIES:
        for _ in range(rng.randint(4, 30)):
            days = rng.randint(end * 365, start * 365)
            when = now - timedelta(days=days, minutes=rng.randint(0, 1440))
            subject = rng.choice(subjects).format(
                n=rng.randint(1000, 9999), month=when.strftime("%B")
            )
            add(sender, name, subject, when, news and rng.random() < 0.7)
    for friend in FRIENDS:
        for _ in range(rng.randint(2, 6)):
            add(
                friend,
                friend.split("@")[0].replace(".", " ").title(),
                "Hallo!",
                now - timedelta(days=rng.randint(1, 900)),
                False,
            )
    box.flush()
    box.close()
    return count
