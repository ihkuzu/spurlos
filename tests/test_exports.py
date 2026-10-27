import json
import sqlite3
import zipfile

import pytest

from spurlos import exports
from spurlos.cli import main
from spurlos.demo import build_export
from spurlos.vault import Vault


@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("customer.firstName", "identity"),
        ("addresses[].zip", "contact"),
        ("orders[].product.name", "purchases"),
        ("devices[].userAgent", "device"),
        ("loginHistory[].ipAddress", "location"),
        ("payment.iban", "payment"),
        ("marketing.segments[]", "profiling"),
        ("watchHistory[].title", "activity"),
        ("fitness.heartRate", "health"),
        ("internal.ref", "other"),
    ],
)
def test_categorize(path, category):
    assert exports.categorize(path) == category


def test_mask_hides_most_of_the_value():
    assert exports.mask("max@gmail.com") == "m***@gmail.com"
    assert exports.mask("DE89 3704 0044 0532 0130 00") == "DE•• •••• •••• •••• •••• 00"
    assert exports.mask("192.0.2.14").endswith("14") and "192" not in exports.mask("192.0.2.14")
    assert exports.mask("Mustermann") == "Mu… (10 chars)"


def test_demo_export_is_read_in_memory(tmp_path):
    result = exports.read(build_export(tmp_path / "x.zip"))
    groups = result.by_category()
    assert result.files == 3 and not result.skipped
    assert {
        "identity",
        "contact",
        "payment",
        "purchases",
        "device",
        "profiling",
        "location",
    } <= set(groups)
    orders = next(f for f in groups["purchases"] if f.field == "product")
    assert orders.count == 14
    ips = next(f for f in groups["location"] if f.field == "ip address")
    assert ips.count == 9 and ips.example.endswith("10")
    assert {f.field for f in groups["activity"]} == {"logins.txt (whole file)"}
    assert "other" not in groups
    iban = next(f for f in groups["payment"] if f.field == "payment.iban")
    assert "3704" not in iban.example
    assert not list(tmp_path.glob("*.json"))


def test_folder_and_plain_text(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "data.json").write_text(json.dumps({"user": {"email": "a@b.de"}}))
    (tmp_path / "a" / "letter.txt").write_text("Name: Max\nTelefon: +49 151 2345678\nfoo bar")
    (tmp_path / "a" / "photo.jpg").write_bytes(b"\xff\xd8")
    result = exports.read(tmp_path / "a")
    fields = {(f.category, f.field) for f in result.found.values()}
    assert ("contact", "user.email") in fields and ("identity", "name") in fields
    assert ("contact", "phone") in fields and result.skipped == ["photo.jpg"]


def test_limits_stop_huge_exports(tmp_path, monkeypatch):
    path = tmp_path / "big.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", "x" * 5000)
        zf.writestr("inner.zip", b"PK")
    monkeypatch.setattr(exports, "MAX_FILE", 1000)
    with pytest.raises(ValueError, match="too big"):
        exports.read(path)
    monkeypatch.setattr(exports, "MAX_FILE", 10_000)
    assert "inner.zip (zip inside a zip, unpack it first)" in exports.read(path).skipped


def test_cli_import_marks_access_request_done_and_map(tmp_path, capsys):
    db = str(tmp_path / "v.db")
    v = Vault(db)
    v.db.execute(
        "INSERT INTO requests (id, org, kind, lang, recipient, transport, subject, body, status,"
        " created, sent, due) VALUES ('SP-ABCDEF','brotkorb-shop.example','access','de',"
        "'d@brotkorb-shop.example','email','s','b','sent','2026-09-01','2026-09-01','2026-10-01')"
    )
    v.db.commit()
    v.close()
    export = build_export(tmp_path / "e.zip")
    assert (
        main(["--vault", db, "export", "import", "brotkorb-shop.example", str(export), "--dry-run"])
        == 0
    )
    assert Vault(db).holdings() == []
    assert main(["--vault", db, "export", "import", "brotkorb-shop.example", str(export)]) == 0
    out = capsys.readouterr().out
    assert "SP-ABCDEF is answered with data" in out and "DE89" not in out
    v = Vault(db)
    assert v.request("SP-ABCDEF")["status"] == "done"
    assert v.holdings()[0]["request_id"] == "SP-ABCDEF"
    v.close()
    capsys.readouterr()
    assert main(["--vault", db, "map"]) == 0
    assert "brotkorb-shop.example" in capsys.readouterr().out
    assert main(["--vault", db, "map", "--category", "payment"]) == 0
    assert "payment.iban" in capsys.readouterr().out
    assert (
        main(
            [
                "--vault",
                db,
                "export",
                "import",
                "other.example",
                str(export),
                "--request",
                "SP-ABCDEF",
            ]
        )
        == 2
    )


def test_readonly_vault_cannot_write(tmp_path):
    Vault(tmp_path / "v.db").close()
    v = Vault(tmp_path / "v.db", readonly=True)
    v.set_category("x.de", "shop", "rules")
    with pytest.raises(sqlite3.OperationalError):
        v.add_me("a@b.de")
    with pytest.raises(FileNotFoundError):
        Vault(tmp_path / "missing.db", readonly=True)


def test_pdf_letter(tmp_path):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    path = tmp_path / "auskunft.pdf"
    c = canvas.Canvas(str(path))
    for i, line in enumerate(
        ["Vorname: Max", "E-Mail: max@example.org", "IBAN: DE89370400440532013000"]
    ):
        c.drawString(72, 750 - 20 * i, line)
    c.save()
    fields = {(f.category, f.field) for f in exports.read(path).found.values()}
    assert {("identity", "vorname"), ("contact", "email"), ("payment", "iban")} <= fields
