import os
import sys
from datetime import datetime, timezone

import pytest
from mcp import Client, StdioServerParameters

from spurlos import outbox
from spurlos.demo import CONTACTS, ME, build_export, build_mbox
from spurlos.exports import read
from spurlos.mcp_server import build_server
from spurlos.registry import Registry
from spurlos.sources import read_mbox
from spurlos.vault import Vault


@pytest.fixture(scope="module")
def registry():
    return Registry.load()


@pytest.fixture
def vault_path(tmp_path, registry):
    path = tmp_path / "v.db"
    v = Vault(path)
    build_mbox(tmp_path / "demo.mbox")
    v.add_me(ME)
    v.set_profile(name="Max Mustermann", email=ME, state="BY")
    for domain, email in CONTACTS.items():
        v.set_contact(domain, email=email)
    v.add(read_mbox(tmp_path / "demo.mbox"))
    d = outbox.create(v, registry, "brotkorb-shop.example", "access")
    outbox.mark_sent(v, d.id, datetime(2026, 8, 1, tzinfo=timezone.utc), "<a@x>")
    v.set_holdings(
        "brotkorb-shop.example", d.id, read(build_export(tmp_path / "e.zip")).found.values()
    )
    v.close()
    return path


async def test_tools_are_all_read_only(vault_path, registry):
    async with Client(build_server(vault_path, registry)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert "overview" in client.instructions
    assert set(tools) == {"overview", "list_organisations", "organisation", "list_requests",
                          "get_request", "todo", "who_holds"}  # fmt: skip
    assert all(t.annotations.read_only_hint for t in tools.values())


async def test_tools_answer_from_the_vault(vault_path, registry):
    before = vault_path.read_bytes()
    async with Client(build_server(vault_path, registry)) as client:
        o = (await client.call_tool("overview", {})).structured_content
        orgs = (await client.call_tool("list_organisations", {"limit": 3})).structured_content
        org = (
            await client.call_tool("organisation", {"domain": "brotkorb-shop.example"})
        ).structured_content
        todo = (await client.call_tool("todo", {})).structured_content
        who = (await client.call_tool("who_holds", {"category": "payment"})).structured_content
        missing = await client.call_tool("get_request", {"request_id": "SP-ZZZZZZ"})
        bad = await client.call_tool("who_holds", {"category": "shoes"})
    assert o["organisations"] > 5 and o["requests"] == {"sent": 1} and o["exports_read"] == 1
    assert len(orgs["result"]) == 3
    assert org["requests"][0]["days_left"] < 0 and any(
        h["category"] == "payment" for h in org["data_held"]
    )
    assert todo["result"][0]["command"].startswith("spurlos request remind")
    assert who["result"] == [
        {"org": "brotkorb-shop.example", "fields": ["payment.iban", "payment.method"]}
    ]
    assert missing.is_error and bad.is_error
    # the server never writes, not even the triage cache
    assert vault_path.read_bytes() == before


async def test_missing_vault_is_a_clear_error(tmp_path, registry):
    async with Client(build_server(tmp_path / "none.db", registry)) as client:
        res = await client.call_tool("overview", {})
    assert res.is_error and "no vault" in res.content[0].text


async def test_server_over_stdio(vault_path):
    # a real subprocess: anything printed to stdout would break the protocol
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "spurlos", "--vault", str(vault_path), "mcp"],
        env=dict(os.environ),
    )
    async with Client(params) as client:
        res = await client.call_tool("list_requests", {"status": "sent"})
    assert res.structured_content["result"][0]["org"] == "brotkorb-shop.example"
