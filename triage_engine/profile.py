"""Load a policy profile from operator-editable files."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .contract import OUTCOME_FIELDS

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


@dataclass(frozen=True)
class Profile:
    directory: Path
    policy: str
    descriptions: dict[str, str]
    rules: tuple
    fallback: tuple
    fingerprint: str

    @property
    def outcomes(self):
        return frozenset(rule[1:] for rule in self.rules) | {self.fallback}


def outcome(value, label):
    if not isinstance(value, dict) or set(value) != set(OUTCOME_FIELDS):
        raise ValueError(f"{label} must contain route, action, and priority")
    for field, name in value.items():
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError(f"{label}.{field} must be a nonempty string without surrounding spaces")
    return tuple(value[field] for field in OUTCOME_FIELDS)


def load_profile(directory=ROOT):
    directory = Path(directory).resolve()
    policy_file = directory / "policy.md"
    rules_file = directory / "rules.json"
    policy = re.sub(r"<!--.*?-->", "", policy_file.read_text(encoding="utf-8"), flags=re.DOTALL).strip()
    if not policy:
        raise ValueError(f"{policy_file} is empty")
    raw = rules_file.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("rules.json must contain an object")
        data.pop("_comment", None)
        if set(data) != {"rules", "fallback"}:
            raise ValueError("include rules and fallback")
        if not isinstance(data["rules"], list) or not data["rules"]:
            raise ValueError("rules must be a nonempty ordered list")
        descriptions, rules = {}, []
        for index, rule in enumerate(data["rules"], 1):
            label = f"rule {index}"
            if not isinstance(rule, dict) or set(rule) != {"signal", "description", "outcome"}:
                raise ValueError(f"{label} must contain signal, description, and outcome")
            name, description = rule["signal"], rule["description"]
            if not isinstance(name, str) or not SIGNAL_NAME.fullmatch(name):
                raise ValueError(f"{label}.signal must start with a lowercase letter and use only lowercase letters, digits, and underscores (up to 64 characters)")
            if name in descriptions:
                raise ValueError(f"duplicate signal {name!r}")
            if not isinstance(description, str) or not description.strip():
                raise ValueError(f"{label}.description must be a nonempty string")
            descriptions[name] = description.strip()
            rules.append((name, *outcome(rule["outcome"], label)))
        fallback = outcome(data["fallback"], "fallback")
    except ValueError as exc:
        raise ValueError(f"{rules_file}: {exc}") from exc
    fingerprint = hashlib.sha256(json.dumps([policy, data]).encode("utf-8")).hexdigest()
    return Profile(directory, policy, descriptions, tuple(rules), fallback, fingerprint)
