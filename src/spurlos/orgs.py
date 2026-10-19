"""Map sender addresses to the organisation behind them."""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

import tldextract

# Mailbox providers: a sender here is a person, not a company holding your data.
FREEMAIL = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "outlook.de",
    "hotmail.com",
    "hotmail.de",
    "live.com",
    "live.de",
    "msn.com",
    "yahoo.com",
    "yahoo.de",
    "ymail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "aol.de",
    "gmx.de",
    "gmx.net",
    "gmx.at",
    "gmx.ch",
    "web.de",
    "t-online.de",
    "freenet.de",
    "arcor.de",
    "posteo.de",
    "posteo.net",
    "mailbox.org",
    "proton.me",
    "protonmail.com",
    "pm.me",
    "tutanota.com",
    "tuta.io",
    "yandex.com",
    "yandex.ru",
    "mail.ru",
    "hotmail.com.tr",
    "outlook.com.tr",
    "zoho.com",
}

# The offline snapshot bundled with tldextract; no network access.
_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

_NOISE = re.compile(
    r"\b(newsletter|news|team|service|support|info|kundenservice|no-?reply|noreply|"
    r"mailer|notifications?|benachrichtigung(en)?|account|konto)\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=4096)
def org_domain(domain: str) -> str:
    """mail.news.zalando.de -> zalando.de, shop.amazon.co.uk -> amazon.co.uk."""
    domain = domain.lower().strip(".")
    parts = _extract(domain)
    top = getattr(parts, "top_domain_under_public_suffix", None) or parts.registered_domain
    return top or domain


def is_personal(domain: str) -> bool:
    return org_domain(domain) in FREEMAIL


def label(names: Counter, domain: str) -> str:
    """Pick a readable name for an organisation from the sender names seen."""
    scores: Counter = Counter()
    for name, count in names.items():
        clean = _NOISE.sub("", name).strip(" -|:,.")
        clean = re.sub(r"\s{2,}", " ", clean)
        if clean and "@" not in clean:
            scores[clean] += count
    if scores:
        return scores.most_common(1)[0][0]
    stem = domain.split(".")[0]
    return stem[:1].upper() + stem[1:]
