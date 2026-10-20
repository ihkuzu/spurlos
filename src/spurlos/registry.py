"""Privacy contacts, supervisory authorities and request templates from the open
Datenanfragen.de database (CC0), packed into one file that ships with spurlos."""

from __future__ import annotations

import gzip
import io
import json
import re
import tarfile
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from .orgs import FREEMAIL, org_domain
from .vault import default_path

SOURCE = "https://github.com/datenanfragen/data"
TARBALL = "https://codeload.github.com/datenanfragen/data/tar.gz/refs/heads/master"
QUALITY = {"tested": 0, "verified": 1, "imported": 2, "scraped": 3}
TEMPLATE_LANGS = ("de", "en")
KEEP = (
    "slug", "name", "categories", "email", "webform", "address", "web", "runs",
    "relevant-countries", "suggested-transport-medium", "request-language",
    "needs-id-document", "required-elements", "quality", "nsfw", "sources",
)  # fmt: skip
_DOMAIN = re.compile(r"\b((?:[a-z0-9-]+\.)+[a-z]{2,})\b")
# Domains that companies send mail from but that are not on their record.
ALIASES = {"facebookmail.com": "facebook", "paypal.de": "paypal", "mail.instagram.com": "instagram"}


@dataclass
class Record:
    slug: str
    name: str
    categories: list[str] = field(default_factory=list)
    email: str | None = None
    webform: str | None = None
    address: str = ""
    web: str | None = None
    countries: list[str] = field(default_factory=list)
    transport: str | None = None
    language: str | None = None
    needs_id: bool = False
    quality: str = "verified"

    @classmethod
    def from_json(cls, d: dict) -> Record:
        transport = d.get("suggested-transport-medium") or ("email" if d.get("email") else "letter")
        return cls(
            slug=d["slug"],
            name=d["name"],
            categories=list(d.get("categories", [])),
            email=d.get("email"),
            webform=d.get("webform"),
            address=d.get("address", ""),
            web=d.get("web"),
            countries=list(d.get("relevant-countries", [])),
            transport=transport,
            language=d.get("request-language"),
            needs_id=bool(d.get("needs-id-document")),
            quality=d.get("quality", "verified"),
        )


def _host(url: str | None) -> str | None:
    if not url:
        return None
    url = re.sub(r"^[a-z]+://", "", url.strip().lower())
    host = url.split("/")[0].split(":")[0]
    return host or None


def _keys(d: dict) -> list[tuple[str, int]]:
    """Domains that point to a record, with a rank: website first, then runs, then email."""
    keys = []
    host = _host(d.get("web"))
    if host:
        keys.append((org_domain(host), 0))
    for line in d.get("runs", []):
        for match in _DOMAIN.findall(line.lower()):
            keys.append((org_domain(match), 1))
    email = d.get("email") or ""
    if "@" in email:
        keys.append((org_domain(email.rsplit("@", 1)[1]), 2))
    return keys


def pack(companies: list[dict], authorities: list[dict], templates: dict, commit: str) -> dict:
    return {
        "source": SOURCE,
        "license": "CC0-1.0",
        "commit": commit,
        "built": datetime.now(timezone.utc).date().isoformat(),
        "companies": [{k: c[k] for k in KEEP if k in c} for c in companies],
        "authorities": authorities,
        "templates": templates,
    }


def pack_checkout(root: str | Path, commit: str = "") -> dict:
    root = Path(root)
    companies = [json.loads(p.read_text("utf-8")) for p in sorted(root.glob("companies/*.json"))]
    authorities = [
        json.loads(p.read_text("utf-8"))
        for p in sorted(root.glob("supervisory-authorities/*.json"))
    ]
    templates = {
        lang: {
            p.stem: p.read_text("utf-8") for p in sorted((root / "templates" / lang).glob("*.txt"))
        }
        for lang in TEMPLATE_LANGS
    }
    return pack(companies, authorities, templates, commit)


def pack_tarball(data: bytes, commit: str = "") -> dict:
    companies, authorities = [], []
    templates: dict[str, dict[str, str]] = {lang: {} for lang in TEMPLATE_LANGS}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/")[1:]
            if len(parts) < 2:
                continue
            raw = tar.extractfile(member)
            if raw is None:
                continue
            if parts[0] == "companies" and parts[-1].endswith(".json"):
                companies.append(json.loads(raw.read().decode("utf-8")))
            elif parts[0] == "supervisory-authorities" and parts[-1].endswith(".json"):
                authorities.append(json.loads(raw.read().decode("utf-8")))
            elif parts[0] == "templates" and len(parts) == 3 and parts[1] in TEMPLATE_LANGS:
                templates[parts[1]][parts[2].removesuffix(".txt")] = raw.read().decode("utf-8")
    companies.sort(key=lambda c: c["slug"])
    authorities.sort(key=lambda a: a["slug"])
    return pack(companies, authorities, templates, commit)


def user_path() -> Path:
    return default_path().parent / "registry.json.gz"


def save(packed: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(packed, ensure_ascii=False).encode("utf-8"), 9))
    return path


def update(path: str | Path | None = None, url: str = TARBALL) -> Path:
    """Download the latest database. Only public data travels; nothing about you is sent."""
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    return save(pack_tarball(data, commit="master@" + datetime.now(timezone.utc).date().isoformat()),
                path or user_path())  # fmt: skip


class Registry:
    def __init__(self, packed: dict):
        self.meta = {k: packed.get(k) for k in ("source", "license", "commit", "built")}
        self.records = {c["slug"]: Record.from_json(c) for c in packed["companies"]}
        self.authorities = packed.get("authorities", [])
        self.templates = packed.get("templates", {})
        index: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        email_users: dict[str, set] = defaultdict(set)
        for c in packed["companies"]:
            ranks: dict[str, int] = {}
            for domain, rank in _keys(c):
                if rank == 2:
                    email_users[domain].add(c["slug"])
                    # website and privacy mailbox on the same domain is the strongest match
                    if ranks.get(domain) == 0:
                        rank = -1
                ranks[domain] = min(rank, ranks.get(domain, rank))
            quality = QUALITY.get(c.get("quality", "verified"), 3)
            for domain, rank in ranks.items():
                index[domain].append((rank, quality, c["slug"]))
        self._index = {}
        for domain, hits in index.items():
            if domain in FREEMAIL:
                continue
            # a privacy mailbox shared by many firms belongs to a service provider
            if len(email_users.get(domain, ())) > 2:
                hits = [h for h in hits if h[0] != 2]
            if hits:
                self._index[domain] = [slug for _, _, slug in sorted(hits)]
        for domain, slug in ALIASES.items():
            if slug in self.records:
                self._index.setdefault(org_domain(domain), [slug])

    @classmethod
    def load(cls, path: str | Path | None = None) -> Registry:
        if path is None:
            path = user_path() if user_path().exists() else None
        if path is None:
            raw = resources.files("spurlos").joinpath("data/registry.json.gz").read_bytes()
        else:
            raw = Path(path).read_bytes()
        return cls(json.loads(gzip.decompress(raw).decode("utf-8")))

    def candidates(self, domain: str) -> list[Record]:
        return [self.records[s] for s in self._index.get(org_domain(domain), [])]

    def lookup(self, domain: str, country: str = "de") -> Record | None:
        found = self.candidates(domain)
        local = [r for r in found if country in r.countries or "all" in r.countries]
        return (local or found or [None])[0]

    def __len__(self) -> int:
        return len(self.records)
