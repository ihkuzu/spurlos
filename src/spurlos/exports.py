"""Read the data a company sent back (Art. 15) and note what kinds of data it holds.

Only the kind of data, the field name and a masked example are kept, never the export itself.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILES = 5000
MAX_FILE = 50 * 1024 * 1024
MAX_TOTAL = 500 * 1024 * 1024

# Checked in this order; the first category whose word appears in a field name wins.
_WORDS = {
    "health": "health gesundheit weight gewicht height heart pulse puls sleep schlaf steps "
    "schritte diagnos medic blood blut",
    "payment": "iban bic card karte payment zahlung bank paypal billing invoice rechnung "
    "konto account_number",
    "purchases": "order bestellung purchase kauf transaction transaktion price preis amount "
    "betrag cart warenkorb product artikel",
    "location": "ip ip_address ipaddress geo lat latitude lon lng longitude location standort "
    "gps region timezone",
    "contact": "email mail e_mail phone telefon mobile mobil handy address adresse anschrift "
    "street strasse straße zip plz postal postcode city stadt ort country land",
    "identity": "name firstname first_name lastname last_name surname vorname nachname birth "
    "birthday geburt geburtsdatum dob gender geschlecht age alter username "
    "nickname anrede",
    "device": "device geraet gerät user_agent useragent browser os imei idfa advertising_id "
    "ad_id cookie app_version platform",
    "messages": "message nachricht chat comment kommentar post review bewertung text body "
    "content inhalt",
    "profiling": "interest interesse segment preference präferenz inferred inference advert "
    "werbung score scoring risk risiko audience zielgruppe affinity",
    "activity": "click klick view search suche history verlauf log event session visit besuch "
    "watch play listen login activity aktivität last_seen",
}
CATEGORIES = {k: tuple(v.split()) for k, v in _WORDS.items()}
LABELS = {
    "identity": "who you are",
    "contact": "how to reach you",
    "location": "where you are or were",
    "payment": "bank and card details",
    "purchases": "what you bought",
    "device": "your devices",
    "activity": "what you did on their service",
    "messages": "what you wrote",
    "profiling": "what they think about you",
    "health": "health data",
    "other": "other data",
}

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b")
_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
_PHONE = re.compile(r"(?<![\w+])(?:\+|00)\d{1,3}[ /-]?\(?\d{2,5}\)?(?:[ /-]?\d{2,}){2,4}\b")
_LABEL_LINE = re.compile(r"^\s*([^\W\d][\w .\-/]{1,40}?)\s*[:=]\s*(\S.{0,200})$", re.MULTILINE)
VALUE_PATTERNS = (("payment", "iban", _IBAN), ("contact", "email", _EMAIL),
                  ("location", "ip address", _IPV4), ("contact", "phone", _PHONE))  # fmt: skip


def _words(name: str) -> list[str]:
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower()
    return [w for w in re.split(r"[^\wäöüß]+|_", name) if w]


def _match(part: str) -> str | None:
    words = _words(part)
    joined = "_".join(words)
    for category, keys in CATEGORIES.items():
        for key in keys:
            if key == joined or key in words or (len(key) > 4 and key in joined):
                return category
    return None


def categorize(path: str) -> str:
    """Pick a category from a field path such as 'orders[].shipping.zipCode'."""
    parts = [p for p in re.split(r"[.\[\]/]+", path) if p]
    first = None
    for part in reversed(parts):
        found = _match(part)
        if found and found != "identity":
            return found
        first = first or found
    # a plain 'name' under 'products' is a product, not you
    return first or "other"


def mask(value) -> str:
    text = str(value).strip().replace("\n", " ")
    if not text:
        return ""
    if "@" in text and _EMAIL.fullmatch(text):
        user, domain = text.split("@", 1)
        return f"{user[:1]}***@{domain}"
    if sum(c.isdigit() for c in text) >= 5:
        return re.sub(r"\d", "•", text[:-2]) + text[-2:] if len(text) > 4 else "•" * len(text)
    if len(text) <= 3:
        return text[0] + "…"
    return text[:2] + "…" + f" ({len(text)} chars)"


@dataclass
class Found:
    category: str
    field: str
    file: str
    count: int = 0
    example: str = ""


@dataclass
class Result:
    files: int = 0
    skipped: list[str] = field(default_factory=list)
    found: dict[tuple[str, str], Found] = field(default_factory=dict)

    def note(self, category: str, name: str, file: str, value, n: int = 1) -> None:
        item = self.found.setdefault((category, name), Found(category, name, file))
        item.count += n
        if not item.example and value not in (None, "", [], {}):
            item.example = mask(value)

    def by_category(self) -> dict[str, list[Found]]:
        out: dict[str, list[Found]] = {}
        for item in sorted(self.found.values(), key=lambda f: (-f.count, f.field)):
            out.setdefault(item.category, []).append(item)
        return out


def _walk(obj, path: str, out: Result, file: str) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            _walk(value, f"{path}.{key}" if path else str(key), out, file)
    elif isinstance(obj, list):
        for value in obj:
            _walk(value, path + "[]", out, file)
    elif path:
        out.note(categorize(path), path, file, obj)


def _text(text: str, out: Result, file: str) -> None:
    for name, value in _LABEL_LINE.findall(text):
        category = categorize(name)
        if category != "other":
            out.note(category, name.strip().lower(), file, value)
    for category, name, pattern in VALUE_PATTERNS:
        hits = [m.group(0) for m in pattern.finditer(text)]
        if hits:
            out.note(category, name, file, hits[0], len(hits))
    kind = _match(Path(file).stem)
    if kind and kind != "identity":
        lines = [line for line in text.splitlines() if line.strip()]
        out.note(
            kind, f"{Path(file).name} (whole file)", file, lines[0] if lines else "", len(lines)
        )


def _csv(text: str, out: Result, file: str) -> None:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    for column in rows[0].keys() if rows else ():
        if column is None:
            continue
        values = [r[column] for r in rows if r.get(column)]
        if values:
            # the file name helps: 'date' in orders.csv is about purchases
            category = categorize(f"{Path(file).stem}.{column}")
            out.note(category, column, file, values[0], len(values))


def _pdf_text(data: bytes) -> str | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages[:200])


def read_file(name: str, data: bytes, out: Result) -> None:
    suffix = Path(name).suffix.lower()
    out.files += 1
    if suffix == ".pdf":
        text = _pdf_text(data)
        if text is None:
            out.skipped.append(f"{name} (install spurlos[pdf] to read PDFs)")
        else:
            _text(text, out, name)
        return
    text = data.decode("utf-8-sig", errors="replace")
    if suffix in (".json", ".js"):
        if suffix == ".js":
            # some exports wrap JSON as "window.x = [...]"
            text = text[text.find("=") + 1 :] if "=" in text[:200] else text
        try:
            _walk(json.loads(text), "", out, name)
        except ValueError:
            _text(text, out, name)
    elif suffix in (".csv", ".tsv"):
        _csv(text, out, name)
    elif suffix in (".txt", ".html", ".htm", ".xml", ".md", ""):
        _text(re.sub(r"<[^>]+>", " ", text) if suffix in (".html", ".htm", ".xml") else text,
              out, name)  # fmt: skip
    else:
        out.files -= 1
        out.skipped.append(name)


def _members(path: Path) -> Iterator[tuple[str, bytes]]:
    """Files inside a zip, a folder or a single file, read in memory and never unpacked to disk."""
    total = 0
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file())
        for n, p in enumerate(files):
            if n >= MAX_FILES:
                raise ValueError(f"more than {MAX_FILES} files")
            size = p.stat().st_size
            total += size
            if size > MAX_FILE or total > MAX_TOTAL:
                raise ValueError(f"{p.name} is too big to read")
            yield str(p.relative_to(path)), p.read_bytes()
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > MAX_FILES:
                raise ValueError(f"more than {MAX_FILES} files")
            for info in infos:
                total += info.file_size
                if info.file_size > MAX_FILE or total > MAX_TOTAL:
                    raise ValueError(f"{info.filename} is too big to read")
                # read at most what the header promised, so a lying zip cannot blow up memory
                with zf.open(info) as fh:
                    data = fh.read(info.file_size + 1)
                if len(data) > info.file_size:
                    raise ValueError(f"{info.filename} is bigger than it claims")
                yield info.filename, data
    else:
        if path.stat().st_size > MAX_FILE:
            raise ValueError(f"{path.name} is too big to read")
        yield path.name, path.read_bytes()


def read(path: str | Path) -> Result:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(2, "no such file", str(path))
    out = Result()
    for name, data in _members(path):
        if Path(name).suffix.lower() == ".zip":
            out.skipped.append(f"{name} (zip inside a zip, unpack it first)")
            continue
        read_file(name, data, out)
    return out


def format_result(org: str, result: Result) -> str:
    groups = result.by_category()
    lines = [f"{org}: {result.files} files read, {len(result.found)} kinds of fields"]
    for category in [*CATEGORIES, "other"]:
        items = groups.get(category)
        if not items:
            continue
        lines.append(f"\n  {LABELS[category]} ({category})")
        for f in items[:8]:
            example = f"  e.g. {f.example}" if f.example else ""
            lines.append(f"    {f.field:<38} {f.count:>6}x{example}")
        if len(items) > 8:
            lines.append(f"    … and {len(items) - 8} more")
    if result.skipped:
        lines.append(f"\n  not read: {', '.join(result.skipped[:5])}"
                     + (" …" if len(result.skipped) > 5 else ""))  # fmt: skip
    return "\n".join(lines)


def who_knows(rows) -> dict[str, dict[str, int]]:
    """org -> category -> number of fields, from the vault's holdings."""
    table: dict[str, dict[str, int]] = {}
    for r in rows:
        table.setdefault(r["org"], {})
        table[r["org"]][r["category"]] = table[r["org"]].get(r["category"], 0) + 1
    return table


def format_map(rows, category: str | None = None) -> str:
    table = who_knows(rows)
    if not table:
        return "no exports read yet; run: spurlos export import DOMAIN PATH"
    if category:
        orgs = sorted(o for o, c in table.items() if category in c)
        if not orgs:
            return f"no organisation is known to hold {LABELS.get(category, category)}"
        lines = [f"who holds {LABELS.get(category, category)}:"]
        for org in orgs:
            names = [r["field"] for r in rows if r["org"] == org and r["category"] == category]
            lines.append(f"  {org:<28} {', '.join(names[:6])}" + (" …" if len(names) > 6 else ""))
        return "\n".join(lines)
    cats = [c for c in [*CATEGORIES, "other"] if any(c in v for v in table.values())]
    head = f"{'organisation':<28}" + "".join(f"{c[:9]:>10}" for c in cats)
    lines = [head, "-" * len(head)]
    for org in sorted(table, key=lambda o: -len(table[o])):
        lines.append(f"{org:<28}" + "".join(f"{table[org].get(c, ''):>10}" for c in cats))
    lines.append(
        "\nnumbers are fields per kind of data; spurlos map --category location for details"
    )
    return "\n".join(lines)
