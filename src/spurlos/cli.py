"""spurlos command line."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import census as census_mod
from . import inbox, mailer, outbox, reply_eval
from . import registry as registry_mod
from . import triage as triage_mod
from .demo import CONTACTS, ME, build_mbox
from .llm import OllamaLLM
from .orgs import org_domain
from .requests import is_reserved
from .sources import ImapSource, read_eml_dir, read_mbox
from .sources import raw_messages as read_raw
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

    pro = sub.add_parser("profile", help="your name and email for requests")
    pro.add_argument("--name")
    pro.add_argument("--email")
    pro.add_argument("--address", help="only used when a company requires it")

    rq = sub.add_parser("request", help="GDPR requests: draft, send, track")
    rqs = rq.add_subparsers(dest="action", required=True)
    new = rqs.add_parser("new", help="draft a request for one organisation")
    new.add_argument("domain")
    new.add_argument("--kind", choices=["access", "erasure", "objection"], required=True)
    new.add_argument("--record", help="registry slug, when several match (see registry show)")
    new.add_argument("--lang", choices=["de", "en"])
    new.add_argument(
        "--field", action="append", default=[], help='extra id data, "Kundennummer=123"'
    )
    new.add_argument(
        "--portability", action="store_true", help="also ask for a machine-readable copy"
    )
    new.add_argument("--objection", action="store_true", help="erasure: also object to processing")
    bat = rqs.add_parser("batch", help="draft requests for all triage items of one priority")
    bat.add_argument("--priority", choices=["high", "medium", "low"], default="high")
    rqs.add_parser("list", help="all requests and their deadlines")
    for name, text in (("show", "print a request"), ("delete", "delete a draft")):
        rqs.add_parser(name, help=text).add_argument("id")
    exp = rqs.add_parser("export", help="save as .eml to send from your own mail program")
    exp.add_argument("id")
    exp.add_argument("--out", required=True)
    snd = rqs.add_parser("send", help="send by SMTP from your own address, after you confirm")
    snd.add_argument("id")
    snd.add_argument("--smtp-host")
    snd.add_argument("--smtp-port", type=int)
    snd.add_argument("--smtp-user", help="default: your profile email")
    snd.add_argument("--no-bcc", action="store_true", help="do not send a copy to yourself")
    snd.add_argument(
        "--yes", action="store_true", help="skip the question; the mail is still shown"
    )
    mk = rqs.add_parser("mark-sent", help="you sent it yourself; start the one-month clock")
    mk.add_argument("id")
    mk.add_argument("--date", type=date.fromisoformat, help="YYYY-MM-DD, default today")

    rp = sub.add_parser("replies", help="answers to your requests, read by the reply agent")
    rps = rp.add_subparsers(dest="action", required=True)

    def agent_options(p):
        p.add_argument("--llm", action="store_true", help="label with a local Ollama model")
        p.add_argument("--model", help="Ollama model, default $SPURLOS_MODEL or llama3.2:3b")
        p.add_argument("--dry-run", action="store_true", help="show, but do not change statuses")

    rimp = rps.add_parser("import", help="look for replies in an mbox file or .eml folder")
    rimp.add_argument("path")
    agent_options(rimp)
    rfet = rps.add_parser("fetch", help="look for replies over IMAP (read-only)")
    rfet.add_argument("--host", required=True)
    rfet.add_argument("--user", required=True)
    rfet.add_argument("--port", type=int, default=993)
    rfet.add_argument("--folder", action="append", help="default INBOX")
    agent_options(rfet)
    rlist = rps.add_parser("list", help="replies and what the agent made of them")
    rlist.add_argument("id", nargs="?", help="only replies to this request")
    rlab = rps.add_parser("relabel", help="correct the agent: set the label yourself")
    rlab.add_argument("key", help="reply key prefix from replies list")
    rlab.add_argument("label", choices=list(inbox.agent.labels.LABELS))

    ev = sub.add_parser("eval-replies", help="measure the reply agent on 40 example answers")
    ev.add_argument("--llm", action="store_true", help="also run a local Ollama model")
    ev.add_argument("--model", help="Ollama model, default $SPURLOS_MODEL or llama3.2:3b")
    ev.add_argument("--cases", help="another JSON file with cases")
    ev.add_argument("--out", help="write all outcomes as JSON")

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


# Known SMTP servers of common mailbox providers: host, port.
SMTP = {
    "gmail.com": ("smtp.gmail.com", 465),
    "googlemail.com": ("smtp.gmail.com", 465),
    "gmx.de": ("mail.gmx.net", 587),
    "gmx.net": ("mail.gmx.net", 587),
    "web.de": ("smtp.web.de", 587),
}


def _print_request(r) -> None:
    print(f"To: {r['recipient']} ({r['transport']})")
    print(f"Subject: {r['subject']}\n")
    print(r["body"])


def _send(vault: Vault, r, args) -> int:
    if r["status"] != "draft":
        print(f"{r['id']} was already sent on {r['sent'][:10]}", file=sys.stderr)
        return 1
    if r["transport"] != "email":
        print(f"{r['org']} takes requests by {r['transport']}: {r['recipient']}")
        print("send the text yourself, then run: spurlos request mark-sent " + r["id"])
        return 1
    profile = vault.profile()
    sender = profile["email"]
    guess = SMTP.get(sender.rsplit("@", 1)[1].lower(), (None, None))
    host = args.smtp_host or profile.get("smtp_host") or guess[0]
    port = args.smtp_port or int(profile.get("smtp_port") or 0) or guess[1] or 587
    if not host:
        print("unknown mail server; pass --smtp-host and --smtp-port", file=sys.stderr)
        return 2
    if is_reserved(r["recipient"]):
        print(f"{r['recipient']} is a reserved example address; nothing was sent", file=sys.stderr)
        return 1
    msg = mailer.build(r, sender, profile["name"], bcc_self=not args.no_bcc)
    print(f"From: {msg['From']}")
    _print_request(r)
    if not args.yes:
        answer = input(f"\nSend this to {r['recipient']} via {host}? Type yes to send: ")
        if answer.strip().lower() != "yes":
            print("not sent")
            return 1
    password = os.environ.get("SPURLOS_SMTP_PASSWORD") or getpass.getpass("SMTP password: ")
    try:
        mailer.send(msg, host, port, args.smtp_user or sender, password)
    except mailer.SendError as error:
        print(error, file=sys.stderr)
        return 1
    vault.set_profile(smtp_host=host, smtp_port=str(port))
    due = outbox.mark_sent(vault, r["id"], message_id=msg["Message-ID"])
    print(f"sent. The answer is due by {due.isoformat()} (Art. 12(3) GDPR).")
    return 0


def _request(vault: Vault, args) -> int:
    registry = registry_mod.Registry.load()
    if args.action == "new":
        fields = dict(f.split("=", 1) for f in args.field if "=" in f)
        try:
            d = outbox.create(
                vault, registry, args.domain, args.kind, slug=args.record, lang=args.lang,
                fields=fields, portability=args.portability, objection=args.objection,
            )  # fmt: skip
        except (ValueError, outbox.NoContact) as error:
            print(error, file=sys.stderr)
            return 1
        _print_request(vault.request(d.id))
        print(f"\ndraft {d.id} saved. Next: spurlos request send {d.id}")
        return 0
    if args.action == "batch":
        items = triage_mod.build(vault, registry)
        try:
            made, skipped = outbox.from_triage(vault, registry, items, args.priority)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
        for d in made:
            print(f"draft {d.id}: {d.kind} to {d.org} ({d.recipient})")
        for line in skipped:
            print(f"skipped {line}")
        return 0
    if args.action == "list":
        lines = outbox.status_lines(vault)
        print("\n".join(lines) if lines else "no requests yet")
        return 0
    r = vault.request(args.id)
    if r is None:
        print(f"no request {args.id}", file=sys.stderr)
        return 1
    if args.action == "show":
        _print_request(r)
    elif args.action == "delete":
        print("deleted" if vault.delete_draft(r["id"]) else "only drafts can be deleted")
    elif args.action == "export":
        profile = vault.profile()
        msg = mailer.build(r, profile["email"], profile["name"], bcc_self=False)
        path = mailer.export_eml(msg, args.out)
        print(f"saved {path}; open it in your mail program, send it, then run:")
        print(f"spurlos request mark-sent {r['id']}")
    elif args.action == "mark-sent":
        when = None
        if args.date:
            when = datetime.combine(args.date, datetime.min.time(), tzinfo=timezone.utc)
        due = outbox.mark_sent(vault, r["id"], when)
        print(f"{r['id']} marked as sent; the answer is due by {due.isoformat()}")
    elif args.action == "send":
        return _send(vault, r, args)
    return 0


def _replies(vault: Vault, args) -> int:
    if args.action == "list":
        rows = vault.replies(args.id)
        if not rows:
            print("no replies yet")
        for r in rows:
            print(f"{r['key'][:8]}  {r['request_id'] or '?':9} {(r['date'] or '')[:10]}  "
                  f"{r['label'] or '-':13} {r['source'] or '':14} {r['sender']}")  # fmt: skip
            if r["next_step"]:
                print(f"          next: {r['next_step']}")
        return 0
    if args.action == "relabel":
        rows = [r for r in vault.replies() if r["key"].startswith(args.key)]
        if len(rows) != 1:
            print("give a key prefix that matches exactly one reply", file=sys.stderr)
            return 1
        reply = rows[0]
        req = vault.request(reply["request_id"]) if reply["request_id"] else None
        decision = inbox.agent.decide(req["kind"], args.label, reply["asks_for"] or "",
                                      req["due"]) if req else {}  # fmt: skip
        vault.relabel(reply["key"], args.label, decision.get("next", ""))
        if req and decision.get("status"):
            vault.set_status(req["id"], decision["status"], decision.get("due"))
        print(f"{reply['key'][:8]} is now {args.label}; next: {decision.get('next', '-')}")
        return 0
    llm = OllamaLLM(args.model) if args.llm else None
    if args.action == "import":
        raws = read_raw(args.path)
    else:
        raws = _imap_replies(vault, args)
    results = inbox.process(vault, raws, llm, apply=not args.dry_run)
    print(inbox.format_results(results))
    return 0


def _imap_replies(vault: Vault, args):
    orgs, since = inbox.watched(vault)
    if not orgs:
        print("no sent requests yet, so there is nothing to look for")
        return []
    password = os.environ.get("SPURLOS_IMAP_PASSWORD") or getpass.getpass("IMAP password: ")
    source = ImapSource(args.host, args.user, password, args.port)
    raws = []
    try:
        for folder in args.folder or ["INBOX"]:
            uids: set[bytes] = set()
            for criteria in inbox.imap_criteria(orgs, since):
                uids.update(source.search(folder, *criteria))
            raws += list(source.full_messages(folder, sorted(uids, key=int)))
    finally:
        source.close()
    return raws


def _eval(args) -> int:
    cases = reply_eval.load_cases(args.cases)
    runs = {"rules": reply_eval.run(cases)}
    if args.llm:
        llm = OllamaLLM(args.model)
        print(f"running {llm.name} on {len(cases)} replies ...", file=sys.stderr)
        runs[llm.name] = reply_eval.run(
            cases, llm, on_case=lambda o: print("." if o["ok"] else "x", end="", flush=True,
                                                 file=sys.stderr),
        )  # fmt: skip
        print(file=sys.stderr)
    for name, outcomes in runs.items():
        print(reply_eval.format_summary(name, reply_eval.summarize(outcomes)))
    if args.out:
        out = {
            name: {"summary": reply_eval.summarize(o), "outcomes": o} for name, o in runs.items()
        }
        Path(args.out).write_text(json.dumps(out, indent=1), "utf-8")
        print(f"wrote {args.out}")
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
    if args.command == "eval-replies":
        return _eval(args)
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
        elif args.command == "profile":
            vault.set_profile(name=args.name, email=args.email, address=args.address)
            for key, value in vault.profile().items():
                if not key.startswith("smtp"):
                    print(f"{key}: {value}")
        elif args.command == "request":
            return _request(vault, args)
        elif args.command == "replies":
            return _replies(vault, args)
        elif args.command == "demo":
            n = build_mbox(args.path)
            vault.add_me(ME)
            vault.set_profile(name="Max Mustermann", email=ME)
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
