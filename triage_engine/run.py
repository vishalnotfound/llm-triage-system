"""Classify a profile's tickets and save a single JSON report."""

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

from . import model
from .contract import read_jsonl, validate_decision
from .profile import ROOT, load_profile

DEFAULT_REPORT = ROOT / "results" / "report.json"


def add_batch_arguments(parser):
    parser.add_argument("--cases", type=Path, help="Defaults to cases.jsonl in the profile")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT, help="Defaults to results/report.json")


def read_cases(path):
    tickets = list(read_jsonl(path))
    if not tickets:
        raise ValueError(f"{path}: add at least one ticket")
    ids = set()
    for index, ticket in enumerate(tickets, 1):
        if not isinstance(ticket, dict) or any(
            not isinstance(ticket.get(field), str) or not ticket[field].strip()
            for field in ("id", "subject", "body")
        ):
            raise ValueError(f"{path}: ticket {index} needs nonempty id, subject, and body strings")
        if ticket["id"] in ids:
            raise ValueError(f"{path}: duplicate ticket ID {ticket['id']!r}")
        ids.add(ticket["id"])
    return tickets


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the destination so a failed or interrupted run leaves the prior report intact.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def run_batch(directory, cases, output):
    profile = load_profile(directory)
    cases = cases or profile.directory / "cases.jsonl"
    sources = [cases, profile.directory / "rules.json", profile.directory / "policy.md"]
    if output.suffix.lower() != ".json" or any(output.resolve() == source.resolve() for source in sources):
        raise ValueError("Choose a separate .json report path")
    tickets = read_cases(cases)
    config = model.describe(profile)
    results = []
    started = time.perf_counter()
    for ticket in tickets:
        try:
            decision, metrics = model.decide(ticket, profile)
            validate_decision(decision, ticket["id"])
        except Exception as exc:
            raise ValueError(f"Ticket {ticket['id']!r} could not be classified: {exc}") from exc
        results.append({
            **decision,
            "signals": metrics["signals"],
            "metrics": {key: metrics[key] for key in ("prompt_tokens", "completion_tokens", "seconds")},
            "input": {"subject": ticket["subject"], "body": ticket["body"]},
        })
    elapsed = time.perf_counter() - started

    def token_sum(key):
        counts = [result["metrics"][key] for result in results]
        return sum(counts) if all(type(count) is int for count in counts) else None

    prompt_tokens = token_sum("prompt_tokens")
    completion_tokens = token_sum("completion_tokens")
    report = {
        "summary": {
            "tickets": len(results),
            "model": config["model_requested"],
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": (
                prompt_tokens + completion_tokens
                if prompt_tokens is not None and completion_tokens is not None else None
            ),
            "elapsed_seconds": round(elapsed, 3),
            "policy_sha256": profile.fingerprint,
        },
        "results": results,
    }
    write_report(output, report)
    return {"report": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", type=Path, default=os.environ.get("LLM_TRIAGE_PROFILE", ROOT),
        help="Directory containing policy.md and rules.json",
    )
    add_batch_arguments(parser)
    args = parser.parse_args()
    try:
        result = run_batch(args.profile, args.cases, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
