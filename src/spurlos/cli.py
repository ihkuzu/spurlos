"""spurlos command line."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import date

from . import census as census_mod
from . import registry as registry_mod
from . import triage as triage_mod
from .demo import CONTACTS, ME, build_mbox
from .llm import OllamaLLM
from .orgs import org_domain
from .sources import ImapSource, read_eml_dir, read_mbox
from .vault import Vault


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spurlos", description=__doc__)
    parser.add_argument("--vault", help="SQLite file, default ~/.spurlos/vault.db or $SPURLOS_HOME")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="read message headers into the vault")
    src = imp.add_subparsers(dest="source", required=True)
    mb = src.add_parser("mbox", help="an mbox file, e.g. from Google Takeout or Thunderbird")
    mb.add_argument("path")
    eml = src.add_parser("eml", help="a folder with .eml files")
    eml.add_argument("path")
    im = src.add_parser("imap", help="an IMAP account, opened read-only")
    im.add_argument("--host", required=True)
    im.add_argument("--user", required=True)
    im.add_argument("--port", type=int, default=993)
    im.add_argument("--folder", action="append", help="repeat for several, default INBOX")
    im.add_argument("--all-folders", action="store_true")
    im.add_argument("--list-folders", action="store_true", help="only print the folder names")
    im.add_argument("--since", type=date.fromisoformat, help="YYYY-MM-DD")

    me = sub.add_parser("me", help="mark addresses as your own so they are left out")
    me.add_argument("addresses", nargs="+")

    cen = sub.add_parser("census", help="list the organisations that have your address")
    cen.add_argument("--limit", type=int, default=25, help="0 lists all")
    cen.add_argument("--json", action="store_true")

    con = sub.add_parser("contact", help="set the privacy contact for an organisation yourself")
    con.add_argument("domain")
    who = con.add_mutually_exclusive_group(required=True)
    who.add_argument("--email")
    who.add_argument("--webform")
    con.add_argument("--name")

    tri = sub.add_parser("triage", help="what to do about each organisation")
    tri.add_argument("--llm", action="store_true", help="ask a local Ollama model when rules fail")
    tri.add_argument("--model", help="Ollama model, default $SPURLOS_MODEL or llama3.2:3b")
    tri.add_argument("--refresh", action="store_true", help="classify again, ignore the cache")
    tri.add_argument("--limit", type=int, default=25, help="0 lists all")
    tri.add_argument("--json", action="store_true")

    reg = sub.add_parser("registry", help="the privacy contact database")
    regsub = reg.add_subparsers(dest="action", required=True)
    regsub.add_parser("info", help="where the data comes from and how big it is")
    show = regsub.add_parser("show", help="the records that match a domain")
    show.add_argument("domain")
    regsub.add_parser("update", help="download the latest database from GitHub")
    build = regsub.add_parser("build", help="pack a local checkout of datenanfragen/data")
    build.add_argument("checkout")
    build.add_argument("--out", required=True)
    build.add_argument("--commit", default="")

    demo = sub.add_parser("demo", help="write a made-up mailbox and import it")
    demo.add_argument("--path", default="data/demo.mbox")
    return parser


def _import_imap(vault: Vault, args) -> int:
    password = os.environ.get("SPURLOS_IMAP_PASSWORD") or getpass.getpass("IMAP password: ")
    source = ImapSource(args.host, args.user, password, args.port)
    try:
        if args.list_folders:
            print("\n".join(source.folders()))
            return 0
        folders = source.folders() if args.all_folders else (args.folder or ["INBOX"])
        vault.add_me(args.user)
        for folder in folders:
            added, seen = vault.add(source.headers(folder, args.since))
            print(f"{folder}: {added} new, {seen} already in the vault")
    finally:
        source.close()
    return 0


def _registry(args) -> int:
    if args.action == "update":
        path = registry_mod.update()
        print(f"saved {len(registry_mod.Registry.load(path))} records to {path}")
        return 0
    if args.action == "build":
        packed = registry_mod.pack_checkout(args.checkout, args.commit)
        path = registry_mod.save(packed, args.out)
        print(f"packed {len(packed['companies'])} records into {path}")
        return 0
    reg = registry_mod.Registry.load()
    if args.action == "info":
        meta = reg.meta
        print(f"{len(reg)} organisations, {len(reg.authorities)} supervisory authorities")
        print(f"source: {meta['source']} ({meta['license']}), commit {meta['commit']}")
        print(f"packed on {meta['built']}")
        return 0
    found = reg.candidates(args.domain)
    if not found:
        print(f"no record for {args.domain} yet; you can add one at {registry_mod.SOURCE}")
        return 1
    for rec in found:
        print(f"{rec.slug}: {rec.name} [{', '.join(rec.categories) or '-'}] quality={rec.quality}")
        print(
            f"  {rec.transport}: {rec.email or rec.webform or rec.address.replace(chr(10), ', ')}"
        )
    return 0


def _triage(vault: Vault, args) -> int:
    llm = OllamaLLM(args.model) if args.llm else None
    failed = []
    items = triage_mod.build(
        vault,
        registry_mod.Registry.load(),
        llm=llm,
        refresh=args.refresh,
        on_error=lambda domain, error: failed.append((domain, error)),
    )
    if failed:
        print(f"model failed for {len(failed)} organisations, first error: {failed[0][1]}",
              file=sys.stderr)  # fmt: skip
    if args.json:
        print(triage_mod.to_json(items))
    else:
        print(triage_mod.format_report(items, args.limit or len(items)))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "registry":
        return _registry(args)
    vault = Vault(args.vault)
    try:
        if args.command == "import":
            if args.source == "imap":
                return _import_imap(vault, args)
            reader = read_mbox if args.source == "mbox" else read_eml_dir
            added, seen = vault.add(reader(args.path))
            print(f"{added} new, {seen} already in the vault ({vault.path})")
        elif args.command == "me":
            vault.add_me(*args.addresses)
            print("your addresses:", ", ".join(sorted(vault.me())))
        elif args.command == "census":
            result = census_mod.build(vault)
            if args.json:
                print(result.to_json())
            else:
                print(census_mod.format_report(result, args.limit or len(result.orgs)))
        elif args.command == "contact":
            domain = org_domain(args.domain)
            vault.set_contact(domain, args.email, args.webform, args.name)
            print(f"saved contact for {domain}")
        elif args.command == "triage":
            return _triage(vault, args)
        elif args.command == "demo":
            n = build_mbox(args.path)
            vault.add_me(ME)
            for domain, email in CONTACTS.items():
                vault.set_contact(domain, email=email)
            added, _ = vault.add(read_mbox(args.path))
            print(f"wrote {n} demo messages to {args.path}, {added} imported into {vault.path}")
            print("now run: spurlos census, then spurlos triage")
    except FileNotFoundError as exc:
        print(f"not found: {exc.filename}", file=sys.stderr)
        return 2
    finally:
        vault.close()
    return 0
