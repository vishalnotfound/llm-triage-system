"""Operator commands and JSON interface for LLM Triage System."""

import argparse
import getpass
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from triage_engine import model
from triage_engine.contract import validate_decision
from triage_engine.profile import ROOT, load_profile
from triage_engine.run import add_batch_arguments, run_batch

from .store import STATES, Queue, QueueError

MAX_INPUT = 128 * 1024


def required_text(value, field, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise QueueError(f"{field} must contain between 1 and {maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise QueueError(f"{field} must be valid Unicode") from exc
    if "\0" in value:
        raise QueueError(f"{field} cannot contain null characters")
    return value


def intake():
    payload = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(payload) > MAX_INPUT:
        raise QueueError("Ticket JSON exceeds 128 KiB")
    try:
        ticket = json.loads(payload)
    except (ValueError, RecursionError) as exc:
        raise QueueError("Expected a JSON ticket on standard input") from exc
    if not isinstance(ticket, dict) or set(ticket) != {"id", "subject", "body"}:
        raise QueueError("Ticket must contain exactly id, subject, and body")
    return {
        "id": required_text(ticket["id"], "id", 128),
        "subject": required_text(ticket["subject"], "subject", 300),
        "body": required_text(ticket["body"], "body", 16000),
    }


def classify(queue, ticket_id, actor, profile):
    ticket, token = queue.claim(ticket_id, actor)
    try:
        config = model.describe(profile)
        decision, details = model.decide(ticket, profile)
        validate_decision(decision, ticket_id)
        evidence = {
            "model": config["model_requested"],
            "policy_sha256": profile.fingerprint,
            "policy_prompt_sha256": config["system_prompt_sha256"],
            "signals": details["signals"],
            "metrics": {key: details[key] for key in ("prompt_tokens", "completion_tokens", "seconds")},
        }
    except Exception:
        # Provider responses and exceptions can contain ticket text or credentials.
        queue.finish(ticket_id, token, None, {"reason": "classification_failed"}, actor)
    else:
        queue.finish(ticket_id, token, decision, evidence, actor)
    return queue.get(ticket_id)


def parser():
    cli = argparse.ArgumentParser(prog="python -m llm_triage_system", description="LLM Triage System")
    cli.add_argument(
        "--db", type=Path, default=os.environ.get("LLM_TRIAGE_DB", "var/triage.sqlite3"),
        help="SQLite path (default: LLM_TRIAGE_DB or var/triage.sqlite3)",
    )
    cli.add_argument(
        "--profile", type=Path, default=os.environ.get("LLM_TRIAGE_PROFILE", ROOT),
        help="Directory containing policy.md and rules.json (or set LLM_TRIAGE_PROFILE)",
    )
    cli.add_argument("--actor", help="Audit label (defaults to the OS username)")
    commands = cli.add_subparsers(dest="command", required=True)
    add_batch_arguments(commands.add_parser("batch", help="Classify cases and save one JSON report"))
    commands.add_parser("submit", help="Queue a JSON ticket read from stdin")
    listing = commands.add_parser("list", help="List tickets, urgent and review first")
    listing.add_argument("--status", choices=STATES)
    listing.add_argument("--route", metavar="ROUTE")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)
    for command, help_text in (
        ("show", "Read a ticket and its decision"),
        ("classify", "Run or retry policy classification"),
        ("review", "Record a manual decision"),
        ("resolve", "Close a triaged ticket"),
        ("reopen", "Return a resolved ticket to the triaged queue"),
        ("history", "Read the ticket's audit history"),
    ):
        operation = commands.add_parser(command, help=help_text)
        operation.add_argument("id")
        if command in {"review", "resolve", "reopen"}:
            operation.add_argument("--reason", required=True)
        if command == "review":
            operation.add_argument("--route", required=True, metavar="ROUTE")
            operation.add_argument("--action", required=True, metavar="ACTION")
            operation.add_argument("--priority", required=True, metavar="PRIORITY")
    return cli


def execute(queue, args, actor, profile):
    if args.command == "submit":
        ticket = intake()
        return {"id": ticket["id"], "created": queue.submit(ticket, actor)}
    if args.command == "list":
        if not 1 <= args.limit <= 200 or not 0 <= args.offset <= 2**63 - 1:
            raise QueueError("Limit must be 1–200 and offset must be a nonnegative SQLite integer")
        return queue.list(args.status, args.route, args.limit, args.offset)
    if args.command == "history":
        return queue.history(args.id)
    if args.command == "classify":
        result = classify(queue, args.id, actor, profile)
        return {key: value for key, value in result.items() if key != "body"}
    if args.command == "review":
        if (args.route, args.action, args.priority) not in profile.outcomes:
            raise QueueError("Route, action, and priority must form an outcome allowed by policy")
        decision = validate_decision({
            "id": args.id, "route": args.route, "action": args.action,
            "priority": args.priority, "escalate": args.action == "escalate",
        })
        queue.review(args.id, decision, required_text(args.reason, "reason", 1000), actor, profile.fingerprint)
    if args.command in {"resolve", "reopen"}:
        expected, status = ("triaged", "resolved") if args.command == "resolve" else ("resolved", "triaged")
        queue.transition(args.id, expected, status, required_text(args.reason, "reason", 1000), actor)
    ticket = queue.get(args.id)
    if args.command != "show":
        del ticket["body"]
    return ticket


def main():
    args = parser().parse_args()
    try:
        if args.command == "batch":
            print(json.dumps(run_batch(args.profile, args.cases, args.output)))
            return 0
        actor = required_text(args.actor if args.actor is not None else getpass.getuser(), "actor", 128)
        profile = load_profile(args.profile) if args.command in {"classify", "review"} else None
        queue = Queue(args.db)
        with closing(queue.db):
            result = execute(queue, args, actor, profile)
        print(json.dumps(result, indent=2))
        if args.command == "classify" and result["status"] == "needs_review":
            return 2
        return 0
    except (QueueError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
