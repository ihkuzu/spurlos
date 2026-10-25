"""A small web page for the vault, served on 127.0.0.1 only. It never sends mail."""

from __future__ import annotations

import json
import secrets
import webbrowser
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from . import census as census_mod
from . import escalate, mailer, outbox
from . import triage as triage_mod
from .agent import decide
from .registry import Registry
from .reply_labels import LABELS
from .vault import Vault

PAGE = "web/index.html"


def _left(due: str | None, today: date) -> int | None:
    return (date.fromisoformat(due) - today).days if due else None


class Api:
    def __init__(self, vault_path, registry: Registry):
        self.vault_path = vault_path
        self.registry = registry

    def vault(self) -> Vault:
        return Vault(self.vault_path)

    def overview(self, v: Vault) -> dict:
        c = census_mod.build(v)
        statuses: dict[str, int] = {}
        for r in v.requests():
            statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        return {
            "profile": {k: val for k, val in v.profile().items() if not k.startswith("smtp")},
            "organisations": len(c.orgs),
            "messages": c.messages,
            "dormant": len(c.dormant()),
            "statuses": statuses,
            "todo": [{"id": i, "what": w, "command": cmd} for i, w, cmd in escalate.todo(v)],
            "labels": list(LABELS),
        }

    def triage(self, v: Vault) -> list[dict]:
        items = json.loads(triage_mod.to_json(triage_mod.build(v, self.registry)))
        open_by_org: dict[str, list[dict]] = {}
        for r in v.requests():
            if r["kind"] in ("access", "erasure", "objection"):
                open_by_org.setdefault(r["org"], []).append(
                    {"id": r["id"], "kind": r["kind"], "status": r["status"]}
                )
        for item in items:
            item["requests"] = open_by_org.get(item["domain"], [])
        return items

    def requests(self, v: Vault) -> list[dict]:
        today = datetime.now(timezone.utc).date()
        rows = []
        for r in v.requests():
            row = dict(r)
            row.pop("body")
            row["days_left"] = _left(r["due"], today) if r["status"] == "sent" else None
            row["replies"] = len(v.replies(r["id"]))
            rows.append(row)
        return rows

    def request(self, v: Vault, request_id: str) -> dict | None:
        r = v.request(request_id)
        if r is None:
            return None
        out = dict(r)
        out["replies"] = [dict(x) for x in v.replies(r["id"])]
        out["children"] = [c["id"] for c in v.children(r["id"])]
        return out

    def replies(self, v: Vault) -> list[dict]:
        rows = [dict(x) for x in v.replies()]
        for row in rows:
            row.pop("body")
        return rows

    ACTIONS = ("draft", "mark-sent", "remind", "complain", "delete", "relabel")

    def post(self, v: Vault, action: str, data: dict) -> dict:
        if action not in self.ACTIONS:
            raise PermissionError(action)
        missing = [k for k in ("id", "domain", "kind", "key", "label") if k in NEEDS[action]
                   and not data.get(k)]  # fmt: skip
        if missing:
            raise ValueError("missing " + ", ".join(missing))
        if action == "draft":
            d = outbox.create(v, self.registry, data["domain"], data["kind"])
            return {"id": d.id}
        if action == "mark-sent":
            due = outbox.mark_sent(v, data["id"])
            return {"due": due.isoformat()}
        if action == "remind":
            return {"id": escalate.reminder(v, data["id"]).id}
        if action == "complain":
            return {"id": escalate.complaint(v, self.registry, data["id"], data.get("why", "")).id}
        if action == "delete":
            return {"deleted": v.delete_draft(data["id"])}
        if action == "relabel":
            if data["label"] not in LABELS:
                raise ValueError(f"unknown label {data['label']}")
            reply = next(x for x in v.replies() if x["key"] == data["key"])
            req = v.request(reply["request_id"]) if reply["request_id"] else None
            step = (
                decide(req["kind"], data["label"], reply["asks_for"] or "", req["due"])
                if req
                else {}
            )
            v.relabel(reply["key"], data["label"], step.get("next", ""))
            if req and step.get("status"):
                v.set_status(req["id"], step["status"], step.get("due"))
            return {"next": step.get("next", "")}
        raise PermissionError(action)


NEEDS = {
    "draft": ("domain", "kind"),
    "mark-sent": ("id",),
    "remind": ("id",),
    "complain": ("id",),
    "delete": ("id",),
    "relabel": ("key", "label"),
}


def make_handler(api: Api, token: str, port: int):
    page = resources.files("spurlos").joinpath(PAGE).read_text("utf-8").replace("__TOKEN__", token)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body: bytes, kind="application/json", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # the page may not load anything from outside this machine
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self' data:; form-action 'none'",
            )
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, data):
            self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

        def _allowed(self) -> bool:
            if self.headers.get("Host") not in hosts:
                return False
            path = urlparse(self.path)
            if path.path == "/":
                return parse_qs(path.query).get("t", [""])[0] == token
            return secrets.compare_digest(self.headers.get("X-Spurlos-Token", ""), token)

        def do_GET(self):
            if not self._allowed():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            path = urlparse(self.path).path
            if path == "/":
                return self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
            v = api.vault()
            try:
                if path == "/api/overview":
                    return self._json(HTTPStatus.OK, api.overview(v))
                if path == "/api/triage":
                    return self._json(HTTPStatus.OK, api.triage(v))
                if path == "/api/requests":
                    return self._json(HTTPStatus.OK, api.requests(v))
                if path == "/api/replies":
                    return self._json(HTTPStatus.OK, api.replies(v))
                if path.startswith("/api/request/"):
                    found = api.request(v, path.rsplit("/", 1)[1])
                    if found is None:
                        return self._json(HTTPStatus.NOT_FOUND, {"error": "no such request"})
                    return self._json(HTTPStatus.OK, found)
                if path.startswith("/api/export/"):
                    r = v.request(path.rsplit("/", 1)[1])
                    if r is None:
                        return self._json(HTTPStatus.NOT_FOUND, {"error": "no such request"})
                    p = v.profile()
                    msg = mailer.build(r, p["email"], p["name"], bcc_self=False)
                    msg["X-Unsent"] = "1"
                    del msg["Bcc"]
                    return self._send(HTTPStatus.OK, bytes(msg), "message/rfc822", {
                        "Content-Disposition": f'attachment; filename="{r["id"]}.eml"'})  # fmt: skip
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            finally:
                v.close()

        def do_POST(self):
            if not self._allowed():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            path = urlparse(self.path).path
            if not path.startswith("/api/"):
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(min(length, 100_000)) or b"{}")
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "body must be JSON"})
            v = api.vault()
            try:
                return self._json(HTTPStatus.OK, api.post(v, path[5:], data))
            except PermissionError:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "unknown action"})
            except (ValueError, LookupError, StopIteration) as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            finally:
                v.close()

    return Handler


def serve(vault_path, port: int = 8044, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(16)
    api = Api(vault_path, Registry.load())
    server = HTTPServer(("127.0.0.1", port), make_handler(api, token, port))
    url = f"http://127.0.0.1:{port}/?t={token}"
    print(f"spurlos is open at {url}\nonly this computer can reach it; press Ctrl+C to stop")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
