"""The reply agent: a small LangGraph graph that matches an answer to its request, labels it
and decides the next step. The model only labels; every action is a fixed rule."""

from __future__ import annotations

from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import reply_labels as labels
from .llm import LLM, LLMError
from .replies import Reply, match
from .requests import add_month

MAX_TRIES = 2


class State(TypedDict, total=False):
    reply: Reply
    request: dict | None
    matched_by: str
    label: str
    summary: str
    asks_for: str
    source: str
    tries: int
    note: str
    answer: dict
    decision: dict


def decide(kind: str, label: str, asks_for: str, due: str | None) -> dict:
    """Same input, same step: no model involved here."""
    if label in ("confirmed", "no_data"):
        return {"status": "done", "next": "nothing to do; keep the reply as proof"}
    if label == "data_provided":
        return {"status": "answered", "next": "look through the data they sent"}
    if label == "id_required":
        wanted = asks_for or "more information"
        return {"status": "waiting_on_you", "next": f"they ask for {wanted}; send only that"}
    if label == "extension":
        # Art. 12(3): up to two further months
        new_due = add_month(add_month(date.fromisoformat(due))) if due else None
        return {
            "status": "sent",
            "due": new_due.isoformat() if new_due else None,
            "next": f"deadline extended to {new_due}" if new_due else "deadline extended",
        }
    if label == "refused":
        return {
            "status": "refused",
            "next": "read their reason; if it does not hold, complain to the authority",
        }
    return {"status": None, "next": "wait for the real answer"}


def build(llm: LLM | None, requests: list[dict]):
    def match_node(state: State) -> State:
        request, how = match(state["reply"], requests)
        return {"request": dict(request) if request else None, "matched_by": how, "tries": 0}

    def classify_node(state: State) -> State:
        reply = state["reply"]
        if reply.auto:
            return {"label": "auto_reply", "summary": "automatic message", "asks_for": "",
                    "source": "header", "answer": {}}  # fmt: skip
        rules = labels.by_rules(reply.subject, reply.body)
        if llm is None:
            return {"label": rules, "summary": "", "asks_for": "", "source": "rules", "answer": {}}
        try:
            answer = labels.by_model(
                llm, state["request"]["kind"], reply.subject, reply.body, reply.attachments,
                state.get("note", ""),
            )  # fmt: skip
        except LLMError as error:
            return {"label": rules, "summary": "", "asks_for": "", "answer": {},
                    "source": f"rules (model failed: {str(error)[:80]})"}  # fmt: skip
        return {"answer": answer, "tries": state.get("tries", 0) + 1, "source": llm.name}

    def check_node(state: State) -> State:
        if not state.get("answer"):
            return {"note": ""}
        problem = labels.check(state["answer"])
        if not problem:
            a = state["answer"]
            return {"label": a["label"].strip().lower(), "summary": str(a.get("summary", "")),
                    "asks_for": str(a.get("asks_for", "")), "note": ""}  # fmt: skip
        if state["tries"] >= MAX_TRIES:
            reply = state["reply"]
            return {"label": labels.by_rules(reply.subject, reply.body), "note": "",
                    "source": "rules (model answer invalid)", "answer": {}}  # fmt: skip
        return {"note": problem}

    def decide_node(state: State) -> State:
        req = state["request"]
        return {"decision": decide(req["kind"], state["label"], state.get("asks_for", ""),
                                   req.get("due"))}  # fmt: skip

    graph = StateGraph(State)
    graph.add_node("match", match_node)
    graph.add_node("classify", classify_node)
    graph.add_node("check", check_node)
    graph.add_node("decide", decide_node)
    graph.add_edge(START, "match")
    graph.add_conditional_edges("match", lambda s: "classify" if s["request"] else END)
    graph.add_edge("classify", "check")
    graph.add_conditional_edges("check", lambda s: "classify" if s.get("note") else "decide")
    graph.add_edge("decide", END)
    return graph.compile()


def run(llm: LLM | None, requests: list[dict], reply: Reply) -> dict[str, Any]:
    return build(llm, requests).invoke({"reply": reply})
