"""spurlos command line."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import date

from . import census as census_mod
from .demo import ME, build_mbox
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
        elif args.command == "demo":
            n = build_mbox(args.path)
            vault.add_me(ME)
            added, _ = vault.add(read_mbox(args.path))
            print(f"wrote {n} demo messages to {args.path}, {added} imported into {vault.path}")
            print("now run: spurlos census")
    except FileNotFoundError as exc:
        print(f"not found: {exc.filename}", file=sys.stderr)
        return 2
    finally:
        vault.close()
    return 0
