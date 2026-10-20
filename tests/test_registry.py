import io
import json
import tarfile

import pytest

from spurlos.registry import Registry, pack, pack_checkout, pack_tarball, save


def company(slug, web=None, email=None, **extra):
    d = {"slug": slug, "name": slug.title(), "address": "Street 1\n10115 Berlin", "sources": []}
    d["relevant-countries"] = extra.pop("countries", ["de"])
    if web:
        d["web"] = web
    if email:
        d["email"] = email
    d.update(extra)
    return d


@pytest.fixture
def reg():
    return Registry(
        pack(
            [
                company("shop", web="https://www.shop.de/", email="datenschutz@shop.de",
                        categories=["commerce"]),
                company("shop-gb", web="https://shop.de/uk", email="privacy@shop.co.uk",
                        countries=["gb"]),
                company("group", web="https://group.com", runs=["Brand X (www.brandx.de)"]),
                company("a", web="https://a.de", email="dsb@dpo-service.de"),
                company("b", web="https://b.de", email="dsb@dpo-service.de"),
                company("c", web="https://c.de", email="dsb@dpo-service.de"),
                company("freemail", email="someone@gmail.com"),
                company("form", web="https://form.de", webform="https://form.de/privacy",
                        **{"suggested-transport-medium": "webform"}),
            ],
            [],
            {},
            "abc",
        )
    )  # fmt: skip


def test_lookup_by_website_subdomain_and_country(reg):
    assert reg.lookup("mail.shop.de").slug == "shop"
    assert reg.lookup("shop.de", country="gb").slug == "shop-gb"
    assert [r.slug for r in reg.candidates("shop.de")] == ["shop", "shop-gb"]


def test_runs_domains_point_to_parent(reg):
    assert reg.lookup("news.brandx.de").slug == "group"


def test_shared_dpo_mailbox_and_freemail_are_not_keys(reg):
    assert reg.candidates("dpo-service.de") == []
    assert reg.candidates("gmail.com") == []


def test_record_defaults(reg):
    assert reg.records["shop"].transport == "email"
    assert reg.records["group"].transport == "letter"
    assert reg.records["form"].transport == "webform"


def test_pack_checkout_and_tarball_agree(tmp_path):
    root = tmp_path / "data-master"
    (root / "companies").mkdir(parents=True)
    (root / "supervisory-authorities").mkdir()
    (root / "templates" / "en").mkdir(parents=True)
    (root / "templates" / "fr").mkdir(parents=True)
    (root / "companies" / "shop.json").write_text(json.dumps(company("shop", web="shop.de")))
    (root / "supervisory-authorities" / "x.json").write_text(json.dumps({"slug": "x"}))
    (root / "templates" / "en" / "access-default.txt").write_text("Dear Sir")
    (root / "templates" / "fr" / "access-default.txt").write_text("Madame")
    a = pack_checkout(root)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(root, arcname="data-master")
    b = pack_tarball(buf.getvalue())
    for packed in (a, b):
        assert [c["slug"] for c in packed["companies"]] == ["shop"]
        assert packed["authorities"] == [{"slug": "x"}]
        assert packed["templates"]["en"] == {"access-default": "Dear Sir"}
        assert "fr" not in packed["templates"]
    path = save(a, tmp_path / "r.json.gz")
    assert Registry.load(path).lookup("shop.de").slug == "shop"


def test_bundled_snapshot_loads():
    reg = Registry.load()
    assert len(reg) > 1000
    assert reg.meta["license"] == "CC0-1.0"
    assert {"access-default", "erasure-default", "objection-default"} <= set(reg.templates["de"])
