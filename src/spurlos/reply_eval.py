"""Measure how well the reply agent labels answers: keyword rules against local models."""

from __future__ import annotations

import json
import time
from collections import Counter
from importlib import resources
from pathlib import Path

from . import agent
from .llm import LLM
from .replies import Reply
from .reply_labels import LABELS


def load_cases(path: str | Path | None = None) -> list[dict]:
    if path:
        data = json.loads(Path(path).read_text("utf-8"))
    else:
        raw = resources.files("spurlos").joinpath("data/replies_eval.json").read_text("utf-8")
        data = json.loads(raw)
    return data["cases"]


def _setup(cases: list[dict]) -> tuple[list[dict], list[Reply]]:
    requests, replies = [], []
    for i, case in enumerate(cases):
        mid = f"<eval{i}@spurlos>"
        requests.append({"id": f"EVAL{i}", "org": f"org{i}.example", "kind": case["kind"],
                         "recipient": f"dpo@org{i}.example", "status": "sent",
                         "sent": "2026-10-01", "due": "2026-11-01", "message_id": mid})  # fmt: skip
        replies.append(Reply(key=case["id"], sender=f"privacy@org{i}.example",
                             org=f"org{i}.example", date=None, subject=case["subject"],
                             body=case["body"], in_reply_to=[mid],
                             attachments=case.get("attachments", []),
                             auto=case.get("auto", False)))  # fmt: skip
    return requests, replies


def run(cases: list[dict], llm: LLM | None = None, on_case=None) -> list[dict]:
    requests, replies = _setup(cases)
    graph = agent.build(llm, requests)
    outcomes = []
    for case, reply in zip(cases, replies):
        start = time.perf_counter()
        state = graph.invoke({"reply": reply})
        outcome = {
            "id": case["id"],
            "lang": case["lang"],
            "gold": case["label"],
            "label": state.get("label"),
            "source": state.get("source"),
            "tries": state.get("tries", 0),
            "seconds": round(time.perf_counter() - start, 3),
        }
        outcome["ok"] = outcome["label"] == outcome["gold"]
        outcomes.append(outcome)
        if on_case:
            on_case(outcome)
    return outcomes


def summarize(outcomes: list[dict]) -> dict:
    n = len(outcomes)
    by_label = {
        label: f"{sum(o['ok'] for o in outcomes if o['gold'] == label)}/"
        f"{sum(1 for o in outcomes if o['gold'] == label)}"
        for label in LABELS
    }
    by_lang = {
        lang: f"{sum(o['ok'] for o in outcomes if o['lang'] == lang)}/"
        f"{sum(1 for o in outcomes if o['lang'] == lang)}"
        for lang in sorted({o["lang"] for o in outcomes})
    }
    mistakes = Counter((o["gold"], o["label"]) for o in outcomes if not o["ok"])
    fallbacks = sum(1 for o in outcomes if str(o["source"]).startswith("rules ("))
    return {
        "correct": sum(o["ok"] for o in outcomes),
        "total": n,
        "by_label": by_label,
        "by_lang": by_lang,
        "mistakes": [f"{gold} -> {got}: {k}" for (gold, got), k in mistakes.most_common(6)],
        "fallbacks": fallbacks,
        "retries": sum(1 for o in outcomes if o["tries"] > 1),
        "seconds_per_reply": round(sum(o["seconds"] for o in outcomes) / max(n, 1), 2),
    }


def format_summary(name: str, s: dict) -> str:
    lines = [
        (
            f"{name}: {s['correct']}/{s['total']} correct, {s['seconds_per_reply']} s per reply, "
            f"{s['fallbacks']} fallbacks to rules, {s['retries']} retries"
        ),
        "  by label: " + ", ".join(f"{k} {v}" for k, v in s["by_label"].items()),
        "  by language: " + ", ".join(f"{k} {v}" for k, v in s["by_lang"].items()),
    ]
    if s["mistakes"]:
        lines.append("  most common mistakes: " + "; ".join(s["mistakes"]))
    return "\n".join(lines)
