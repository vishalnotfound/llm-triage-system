"""OpenAI-compatible chat-completions adapter for the model-backed triage."""

import functools
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contract import ACTIONS, PRIORITIES, ROUTES
from .triage import SIGNALS, apply_policy


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4.1-mini-2025-04-14"
PROMPTS = {"api": "candidate_prompt.md", "api-direct": "direct_prompt.md"}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
ATTEMPTS = 4
MAX_RESPONSE_BYTES = 64 * 1024


class RejectRedirects(HTTPRedirectHandler):
    """Keep provider credentials and ticket content on the configured endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

SIGNALS_SCHEMA = {
    "type": "object",
    "properties": {name: {"type": "boolean"} for name in SIGNALS},
    "required": list(SIGNALS),
    "additionalProperties": False,
}
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": sorted(ROUTES)},
        "action": {"type": "string", "enum": sorted(ACTIONS)},
        "priority": {"type": "string", "enum": sorted(PRIORITIES)},
    },
    "required": ["route", "action", "priority"],
    "additionalProperties": False,
}


@functools.cache
def settings():
    """Read the untracked .env (shell variables win), then the run settings."""
    env_file = ROOT / ".env"
    if env_file.exists():
        raw = env_file.read_bytes()
        # PowerShell redirection writes UTF-16 and Notepad may add a UTF-8 BOM.
        text = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    return {
        "base_url": os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL,
        "model": os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL,
        # Empty omits the parameter, for models that only accept their default.
        "temperature": os.environ.get("OPENAI_TEMPERATURE", "0"),
    }


@functools.cache
def system_prompt(mode):
    prompt = (ROOT / "triage_engine" / PROMPTS[mode]).read_text(encoding="utf-8")
    policy = (ROOT / "policy.md").read_text(encoding="utf-8")
    return prompt.strip() + "\n\n" + policy.strip()


def describe(mode):
    """Settings recorded next to the decisions. Never includes the API key."""
    config = settings()
    return {
        "base_url": config["base_url"],
        "model_requested": config["model"],
        "temperature": config["temperature"] or "provider default",
        "response_format": "json_schema, strict",
        "system_prompt": f"triage_engine/{PROMPTS[mode]} + policy.md",
        "system_prompt_sha256": hashlib.sha256(system_prompt(mode).encode("utf-8")).hexdigest(),
    }


def post(payload):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY in the shell or in .env")
    request = Request(
        settings()["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    opener = build_opener(RejectRedirects())
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with opener.open(request, timeout=60) as response:
                content = response.read(MAX_RESPONSE_BYTES + 1)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise ValueError("Model response exceeded 64 KiB")
                return json.loads(content)
        except HTTPError as exc:
            with exc:
                if exc.code not in RETRYABLE_STATUS or attempt == ATTEMPTS:
                    raise RuntimeError(f"HTTP {exc.code} from model API") from exc
        except OSError:
            if attempt == ATTEMPTS:
                raise
        time.sleep(2**attempt)


def complete(mode, ticket, schema_name, schema):
    config = settings()
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt(mode)},
            {
                "role": "user",
                "content": json.dumps(
                    {"subject": ticket["subject"], "body": ticket["body"]}, ensure_ascii=False
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    }
    if config["temperature"]:
        payload["temperature"] = float(config["temperature"])

    started = time.perf_counter()
    result = post(payload)
    choice = result["choices"][0]
    message = choice["message"]
    if choice.get("finish_reason") != "stop" or not message.get("content"):
        raise ValueError("Model refused or did not return a complete response")
    usage = result.get("usage") or {}
    return json.loads(message["content"]), {
        "model": result.get("model"),
        "system_fingerprint": result.get("system_fingerprint"),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "seconds": round(time.perf_counter() - started, 3),
    }


def decide(ticket):
    """Candidate: the model reports policy signals; triage.apply_policy decides."""
    signals, meta = complete("api", ticket, "ticket_signals", SIGNALS_SCHEMA)
    if not isinstance(signals, dict) or set(signals) != set(SIGNALS):
        raise ValueError("Model response did not contain the required policy signals")
    if any(type(value) is not bool for value in signals.values()):
        raise ValueError("Model policy signals must be booleans")
    return apply_policy(ticket["id"], signals), {"signals": signals, **meta}


def decide_direct(ticket):
    """Ablation: the model applies the policy and returns the decision itself."""
    answer, meta = complete("api-direct", ticket, "ticket_decision", DECISION_SCHEMA)
    decision = {"id": ticket["id"], **answer, "escalate": answer.get("action") == "escalate"}
    return decision, meta
