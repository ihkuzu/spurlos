"""A small client for a model served by Ollama on this machine."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Protocol


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    name: str

    def json(self, system: str, prompt: str, schema: dict) -> dict: ...


def _host(value: str | None) -> str:
    host = (value or "http://localhost:11434").strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.replace("//0.0.0.0", "//localhost")


class OllamaLLM:
    def __init__(self, model: str | None = None, host: str | None = None, timeout: float = 300):
        self.model = model or os.getenv("SPURLOS_MODEL") or "llama3.2:3b"
        self.host = _host(host or os.getenv("OLLAMA_HOST"))
        self.timeout = timeout
        self.name = f"ollama:{self.model}"

    def json(self, system: str, prompt: str, schema: dict) -> dict:
        payload = {
            "model": self.model,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0, "num_predict": 256},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            return json.loads(body["message"]["content"])
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"Ollama returned {error.code}: {detail}") from error
        except (urllib.error.URLError, OSError) as error:
            raise LLMError(f"cannot reach Ollama at {self.host}: {error}") from error
        except (KeyError, ValueError) as error:
            raise LLMError(
                f"Ollama sent something that is not the expected JSON: {error}"
            ) from error


class ScriptedLLM:
    """Returns prepared answers in order, so tests run without a model."""

    name = "scripted"

    def __init__(self, answers: list[dict]):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def json(self, system: str, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        if not self.answers:
            raise LLMError("scripted answers are used up")
        return self.answers.pop(0)
