import json
from collections import Counter
from datetime import datetime, timezone

from spurlos import census
from spurlos.cli import main
from spurlos.headers import parse_header
from spurlos.orgs import is_personal, label, org_domain
from spurlos.vault import Vault

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def msg(sender, date="Mon, 19 Oct 2026 09:00:00 +0000", subject="s", mid=None, news=False):
    lines = [f"From: {sender}", f"Date: {date}", f"Subject: {subject}"]
    if mid:
        lines.append(f"Message-ID: <{mid}>")
    if news:
        lines.append("List-Unsubscribe: <mailto:x@y>")
    return parse_header(("\n".join(lines) + "\n\n").encode())


def test_org_domain():
    assert org_domain("mail.news.zalando.de") == "zalando.de"
    assert org_domain("shop.amazon.co.uk") == "amazon.co.uk"
    assert org_domain("e.hepsiburada.com.tr") == "hepsiburada.com.tr"
    assert is_personal("googlemail.com") and not is_personal("bahn.de")


def test_label_drops_noise_words():
    assert label(Counter({"Zalando Newsletter": 3, "Zalando Team": 2}), "zalando.de") == "Zalando"
    assert label(Counter({"": 1}), "stromfix.de") == "Stromfix"


def test_vault_deduplicates(tmp_path):
    v = Vault(tmp_path / "v.db")
    m = msg("a@shop.de", mid="1@shop.de")
    assert v.add([m]) == (1, 0)
    assert v.add([m]) == (0, 1)
    assert v.count() == 1


def test_census_groups_and_leaves_out_people_and_me(tmp_path):
    v = Vault(tmp_path / "v.db")
    v.add(
        [
            msg("News <news@mail.shop.de>", mid="1", news=True),
            msg("Shop <order@shop.de>", mid="2", date="Mon, 01 Jan 2018 09:00:00 +0000"),
            msg("friend@gmail.com", mid="3"),
            msg("me@example.org", mid="4"),
            msg("old@forum.net", mid="5", date="Mon, 01 Jan 2015 09:00:00 +0000"),
        ]
    )
    v.add_me("me@example.org")
    result = census.build(v, now=NOW)
    assert [o.domain for o in result.orgs] == ["shop.de", "forum.net"]
    shop = result.orgs[0]
    assert shop.messages == 2 and shop.addresses == ["news@mail.shop.de", "order@shop.de"]
    assert shop.first_seen.startswith("2018") and shop.last_seen.startswith("2026")
    assert shop.newsletter_share == 0.5
    assert result.personal == 1
    assert [o.domain for o in result.dormant()] == ["forum.net"]
    report = census.format_report(result)
    assert report.startswith("2 organisations")


def test_cli_demo_then_census(tmp_path, capsys):
    db = str(tmp_path / "v.db")
    assert main(["--vault", db, "demo", "--path", str(tmp_path / "d.mbox")]) == 0
    assert main(["--vault", db, "census", "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out[out.index("{") :])
    assert len(data["orgs"]) == 12
    assert all("example.org" not in o["domain"] for o in data["orgs"])


def test_cli_missing_file(tmp_path):
    assert main(["--vault", str(tmp_path / "v.db"), "import", "mbox", str(tmp_path / "no")]) == 2
