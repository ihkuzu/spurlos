"""Hints in subject lines that you hold an account somewhere, not just a newsletter."""

from __future__ import annotations

import re

# German, English and Turkish wording, matched case-insensitively.
PATTERNS = {
    "account": r"willkommen|welcome|hoş ?geldin|registrier|registration|anmeldung bestätig|"
    r"bestätigen sie|e-?mail.?adresse bestätig|confirm your (e-?mail|account)|verify your|"
    r"activate your|konto (wurde )?(erstellt|eröffnet)|account (was )?created|hesabınız",
    "order": r"bestellung|\border\b|rechnung|invoice|receipt|quittung|lieferung|versand|shipped|"
    r"delivery|zahlung|payment|sipariş|fatura|kargo",
    "password": r"passwort|password|kennwort|şifre|\breset\b|zurücksetzen",
    "security": r"sicherheit|security|neue anmeldung|new (sign-?in|login)|login (from|von)|"
    r"anmeldung von|güvenlik|zwei-?faktor|two-?factor|verification code|bestätigungscode",
    "contract": r"vertrag|contract|abo(nnement)?\b|subscription|mitglied|membership|kündigung|"
    r"tarif|abschlag|jahresabrechnung|kontoauszug|\bpolice\b|üyelik|abonelik",
    "booking": r"buchung|booking|reservierung|reservation|fahrkarte|\bticket\b|check-?in|"
    r"boarding|rezervasyon|bilet",
}
_COMPILED = {name: re.compile(p, re.IGNORECASE) for name, p in PATTERNS.items()}

# Signals that show more data than an email address: name, address, payment, history.
STRONG = {"account", "order", "password", "security", "contract", "booking"}


def detect(subject: str) -> set[str]:
    return {name for name, rx in _COMPILED.items() if rx.search(subject or "")}


def collect(subjects: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for subject in subjects:
        for name in detect(subject):
            counts[name] = counts.get(name, 0) + 1
    return counts
