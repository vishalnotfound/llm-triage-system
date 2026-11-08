"""OpenAI-compatible chat-completions adapter for the model-backed triage."""

import functools
import hashlib
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .profile import ROOT, load_profile
from .triage import apply_policy, validate_signals


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-6-luna"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
ATTEMPTS = 4
MAX_RESPONSE_BYTES = 64 * 1024


class RejectRedirects(HTTPRedirectHandler):
    """Keep provider credentials and ticket content on the configured endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
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


def system_prompt(profile):
    prompt = (ROOT / "triage_engine" / "candidate_prompt.md").read_text(encoding="utf-8")
    guidance = "\n".join(f"- {name}: {text}" for name, text in profile.descriptions.items())
    return f"{prompt.strip()}\n\n{guidance}\n\n{profile.policy}"


def describe(profile):
    """Settings recorded next to the decisions. Never includes the API key."""
    config = settings()
    return {
        "base_url": config["base_url"],
        "model_requested": config["model"],
        "temperature": config["temperature"] or "provider default",
        "response_format": "json_schema, strict",
        "policy_sha256": profile.fingerprint,
        "system_prompt_sha256": hashlib.sha256(system_prompt(profile).encode("utf-8")).hexdigest(),
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


def complete(ticket, schema, profile):
    config = settings()
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt(profile)},
            {
                "role": "user",
                "content": json.dumps(
                    {"subject": ticket["subject"], "body": ticket["body"]}, ensure_ascii=False
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "ticket_signals", "strict": True, "schema": schema},
        },
    }
    if config["model"] == "gpt-6-luna":
        payload["reasoning_effort"] = "none"
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
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "seconds": round(time.perf_counter() - started, 3),
    }


def decide(ticket, profile=None):
    """Read policy signals and apply the profile's first matching outcome."""
    profile = profile or load_profile()
    schema = object_schema({
        name: {"type": "boolean", "description": description}
        for name, description in profile.descriptions.items()
    })
    signals, meta = complete(ticket, schema, profile)
    validate_signals(signals, profile)
    decision = apply_policy(ticket["id"], signals, profile.rules, profile.fallback)
    return decision, {"signals": signals, **meta}
