"""Generate one decision per input ticket."""

import argparse
import json
import platform
import time
from pathlib import Path

from .contract import read_jsonl, validate_decision
from .profile import load_profile


ROOT = Path(__file__).resolve().parent.parent


def show(path):
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("api", "api-direct", "heuristic"), default="api")
    parser.add_argument("--profile", type=Path, default=ROOT, help="Directory containing policy.md and rules.json")
    parser.add_argument("--cases", type=Path, help="Defaults to cases.jsonl in the profile")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "decisions.jsonl")
    args = parser.parse_args()

    try:
        profile = load_profile(args.profile)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.cases is None:
        args.cases = profile.directory / "cases.jsonl"

    if args.mode == "heuristic":
        from .triage import decide

        config = {"policy_sha256": profile.fingerprint}
    else:
        from . import model

        decide = model.decide if args.mode == "api" else model.decide_direct
        config = model.describe(args.mode, profile)

    tickets = list(read_jsonl(args.cases))
    ids = [ticket["id"] for ticket in tickets]
    if len(ids) != len(set(ids)):
        raise ValueError("input contains duplicate ticket IDs")

    # Collect and validate before writing, so a failed run does not leave a
    # partially generated decision file.
    decisions, trace = [], []
    started = time.perf_counter()
    for ticket in tickets:
        try:
            decision, details = decide(ticket, profile)
            decisions.append(validate_decision(decision, ticket["id"]))
        except Exception as exc:
            raise RuntimeError(f"ticket {ticket['id']} failed: {exc}") from exc
        trace.append({"id": ticket["id"], "decision": decision, **details})
    elapsed = time.perf_counter() - started

    usage = {key: sum(item.get(key, 0) for item in trace) for key in ("prompt_tokens", "completion_tokens")}
    log = {
        "mode": args.mode,
        "cases": show(args.cases),
        "tickets": len(decisions),
        "elapsed_seconds": round(elapsed, 2),
        **config,
        "models_returned": sorted({item["model"] for item in trace if item.get("model")}),
        "system_fingerprints": sorted({item["system_fingerprint"] for item in trace if item.get("system_fingerprint")}),
        "usage": usage,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "trace": trace,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in decisions),
        encoding="utf-8",
    )
    log_path = args.output.with_suffix(".run.json")
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(decisions)} decisions to {show(args.output)} ({elapsed:.1f}s)")
    if args.mode != "heuristic":
        print(
            f"Model {', '.join(log['models_returned'])}: {usage['prompt_tokens']} prompt + "
            f"{usage['completion_tokens']} completion tokens; details in {show(log_path)}"
        )


if __name__ == "__main__":
    main()
