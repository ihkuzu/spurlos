import json

import pytest

from spurlos.classify import CATEGORIES, by_rules, classify, prompt
from spurlos.cli import main
from spurlos.demo import build_mbox
from spurlos.llm import LLMError, ScriptedLLM
from spurlos.registry import Record, Registry, pack
from spurlos.signals import collect, detect
from spurlos.sources import read_mbox
from spurlos.triage import Item, build, decide
from spurlos.vault import Vault


def test_signals_in_three_languages():
    assert detect("Willkommen bei FitWerk") == {"account"}
    assert detect("Your order has shipped") == {"order"}
    assert detect("Şifre sıfırlama isteği") == {"password"}
    assert detect("Neue Anmeldung bei Ihrem Konto") == {"security"}
    assert detect("Border collie news") == set()
    assert collect(["Ihre Bestellung", "Ihre Rechnung", "Hallo"]) == {"order": 2}


def test_rules_use_name_and_domain():
    assert by_rules("Apotheke Rosengarten", "apotheke-rosengarten.example") == "health"
    assert by_rules("Kontora", "kontora-bank.example") == "finance"
    assert by_rules("Lesefuchs", "lesefuchs.example") is None


def test_registry_beats_rules_and_model():
    record = Record(slug="x", name="X", categories=["telecommunication"])
    llm = ScriptedLLM([])
    assert classify("Bank", "bank.de", [], record, llm) == ("telecommunication", "registry")
    assert llm.prompts == []


def test_model_only_for_unknowns_and_answer_is_checked():
    llm = ScriptedLLM([{"category": "commerce"}, {"category": "pirates"}])
    assert classify("Lesefuchs", "lesefuchs.example", ["Ihre Bestellung"], None, llm) == (
        "commerce",
        "scripted",
    )
    assert "Ihre Bestellung" in llm.prompts[0]
    with pytest.raises(LLMError):
        classify("Lesefuchs", "lesefuchs.example", [], None, llm)
    assert classify("Lesefuchs", "lesefuchs.example", [], None, None) == ("other", "none")


def test_prompt_lists_every_category_and_caps_subjects():
    text = prompt("A", "a.de", [f"s{i}" for i in range(20)])
    assert all(c in text for c in CATEGORIES)
    assert "s7" in text and "s8" not in text


def item(**kw):
    base = {"domain": "x.de", "name": "X", "messages": 5, "last_seen": None, "dormant": False,
            "category": "other", "category_source": "none"}  # fmt: skip
    base.update(kw)
    return Item(**base)


@pytest.mark.parametrize(
    ("kw", "share", "action", "priority"),
    [
        ({"category": "addresses"}, 0, "access+objection", "high"),
        ({"category": "health"}, 0, "access", "high"),
        ({"dormant": True, "signals": {"account": 1}}, 0, "erasure", "high"),
        ({"dormant": True}, 0, "erasure", "medium"),
        ({"signals": {"order": 3}}, 0, "access", "medium"),
        ({"category": "finance"}, 0, "access", "medium"),
        ({}, 0.9, "unsubscribe", "low"),
        ({}, 0.2, "review", "low"),
    ],
)
def test_decide(kw, share, action, priority):
    i = item(**kw)
    decide(i, share)
    assert (i.action, i.priority) == (action, priority)


def demo_vault(tmp_path):
    v = Vault(tmp_path / "v.db")
    build_mbox(tmp_path / "d.mbox")
    v.add_me("max.mustermann@example.org")
    v.add(read_mbox(tmp_path / "d.mbox"))
    return v


def empty_registry():
    return Registry(pack([], [], {}, ""))


def test_build_caches_categories_and_falls_back_when_model_fails(tmp_path):
    v = demo_vault(tmp_path)
    errors = []
    llm = ScriptedLLM([{"category": "commerce"}])  # one answer, then it fails
    items = build(v, empty_registry(), llm=llm, on_error=lambda d, e: errors.append(d))
    assert len(items) == 12
    sources = {i.category_source for i in items}
    assert sources <= {"rules", "scripted", "none"}
    assert "scripted" in sources and errors
    # cached results are reused, so a second run asks nothing
    again = build(v, empty_registry(), llm=None)
    assert [i.category for i in again] == [i.category for i in items]


def test_own_contact_wins_over_registry(tmp_path):
    v = demo_vault(tmp_path)
    reg = Registry(pack([{"slug": "bk", "name": "Brotkorb GmbH", "address": "", "sources": [],
                          "relevant-countries": ["de"], "web": "https://brotkorb-shop.example",
                          "email": "dsb@brotkorb-shop.example"}], [], {}, ""))  # fmt: skip
    items = {i.domain: i for i in build(v, reg)}
    assert items["brotkorb-shop.example"].contact == "dsb@brotkorb-shop.example"
    assert items["brotkorb-shop.example"].name == "Brotkorb GmbH"
    v.set_contact("brotkorb-shop.example", email="privacy@brotkorb-shop.example")
    items = {i.domain: i for i in build(v, reg)}
    assert items["brotkorb-shop.example"].contact == "privacy@brotkorb-shop.example"
    assert items["brotkorb-shop.example"].record == "own"


def test_cli_triage_json(tmp_path, capsys):
    db = str(tmp_path / "v.db")
    main(["--vault", db, "demo", "--path", str(tmp_path / "d.mbox")])
    capsys.readouterr()
    assert main(["--vault", db, "triage", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 12
    assert sum(1 for d in data if d["contact"]) == 5
    assert data[0]["priority"] == "high"


def test_cli_registry_show(capsys):
    assert main(["registry", "show", "zalando.de"]) == 0
    assert "datenschutz@zalando.de" in capsys.readouterr().out
    assert main(["registry", "show", "nothing-here.example"]) == 1
