"""Validation for triage decisions and JSONL inputs."""

import json

ROUTES = {"billing", "access", "privacy", "safety", "general"}
ACTIONS = {"reply", "verify_identity", "escalate", "refuse"}
PRIORITIES = {"normal", "urgent"}
FIELDS = {"id", "route", "action", "priority", "escalate"}


def validate_decision(value, expected_id=None):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError(f"decision must have exactly these fields: {sorted(FIELDS)}")
    if not isinstance(value["id"], str) or not value["id"]:
        raise ValueError("id must be a nonempty string")
    if expected_id is not None and value["id"] != expected_id:
        raise ValueError(f"expected id {expected_id}, got {value['id']}")
    if value["route"] not in ROUTES:
        raise ValueError(f"invalid route: {value['route']}")
    if value["action"] not in ACTIONS:
        raise ValueError(f"invalid action: {value['action']}")
    if value["priority"] not in PRIORITIES:
        raise ValueError(f"invalid priority: {value['priority']}")
    if type(value["escalate"]) is not bool:
        raise ValueError("escalate must be a boolean")
    if value["escalate"] != (value["action"] == "escalate"):
        raise ValueError("escalate must be true exactly when action is escalate")
    return value


def read_jsonl(path):
    with open(path, encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc
