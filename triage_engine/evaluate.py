"""Evaluate triage decisions: contract, policy, baseline agreement, sensitivity."""

import argparse
import json
from pathlib import Path

from .contract import FIELDS, read_jsonl, validate_decision
from .triage import RULES, apply_policy


ROOT = Path(__file__).resolve().parent.parent
DECISION_FIELDS = ("route", "action", "priority", "escalate")
CHECKS = ("incident", "false_alarm", "injection", "identity", "third_party", "precedence", "routine")

# Every (route, action, priority) policy.md can produce, written out by hand
# rather than derived from triage.RULES so that a bad rule edit cannot hide.
ALLOWED = {
    ("safety", "escalate", "urgent"),
    ("privacy", "refuse", "normal"),
    ("privacy", "verify_identity", "normal"),
    ("privacy", "reply", "normal"),
    ("access", "verify_identity", "normal"),
    ("access", "reply", "normal"),
    ("billing", "reply", "normal"),
    ("general", "reply", "normal"),
}


def show(path):
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def label(decision):
    return f"{decision['route']}/{decision['action']}/{decision['priority']}"


def load_decisions(path, case_ids):
    rows = [validate_decision(row) for row in read_jsonl(path)]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    if set(ids) != set(case_ids):
        missing, extra = sorted(set(case_ids) - set(ids)), sorted(set(ids) - set(case_ids))
        raise ValueError(f"case IDs differ: missing={missing}, extra={extra}")
    return {row["id"]: row for row in rows}


def load_expected(path):
    expected = {}
    for row in read_jsonl(path):
        validate_decision({field: row[field] for field in FIELDS})
        if (row["route"], row["action"], row["priority"]) not in ALLOWED:
            raise ValueError(f"{path}: label for {row['id']} is not an outcome policy.md allows")
        expected[row["id"]] = row
    return expected


def load_signals(decisions_path):
    log = decisions_path.with_suffix(".run.json")
    if not log.exists():
        return {}
    trace = json.loads(log.read_text(encoding="utf-8")).get("trace", [])
    return {item["id"]: item["signals"] for item in trace if "signals" in item}


def policy_failures(decisions, expected):
    """Map case ID to a problem for every decision that policy.md does not allow."""
    failures = {}
    for case_id, decision in decisions.items():
        want = expected.get(case_id)
        if (decision["route"], decision["action"], decision["priority"]) not in ALLOWED:
            failures[case_id] = f"{label(decision)} is not an outcome policy.md allows"
        elif want and any(decision[field] != want[field] for field in DECISION_FIELDS):
            failures[case_id] = f"expected {label(want)}, got {label(decision)}. {want['why']}"
    return failures


def weakened_rules():
    """Deliberately broken copies of triage.RULES that the suite must catch."""
    yield "accept identity claims (verify_identity becomes reply)", [
        (signal, route, "reply" if action == "verify_identity" else action, priority)
        for signal, route, action, priority in RULES
    ]
    yield "drop the incident rule (rule 1)", [rule for rule in RULES if rule[1] != "safety"]
    yield "drop the third-party refusal (rule 2)", [rule for rule in RULES if rule[2] != "refuse"]
    others = [rule for rule in RULES if rule[1] != "billing"]
    billing = [rule for rule in RULES if rule[1] == "billing"]
    yield "check billing before privacy and access", others[:2] + billing + others[2:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--decisions", type=Path, default=ROOT / "evals" / "recorded" / "decisions.jsonl")
    parser.add_argument("--cases", type=Path, default=ROOT / "cases.jsonl")
    parser.add_argument("--extra-decisions", type=Path, help="decisions for --extra-cases")
    parser.add_argument("--extra-cases", type=Path, default=ROOT / "evals" / "extra_cases.jsonl")
    parser.add_argument("--expected", type=Path, default=ROOT / "evals" / "expected.jsonl")
    parser.add_argument("--reference", type=Path, help="optional reference decisions for the primary cases")
    parser.add_argument("--out", type=Path, help="also save this report as UTF-8 text")
    args = parser.parse_args()

    lines = []

    def say(text=""):
        print(text)
        lines.append(text)

    expected = load_expected(args.expected)
    sources = [("public", args.decisions, args.cases)]
    if args.extra_decisions:
        sources.append(("added", args.extra_decisions, args.extra_cases))
    ok = True

    say("Evaluating " + " + ".join(show(path) for _, path, _ in sources))
    say()
    say("Output contract")
    decisions, groups, signals = {}, {}, {}
    for group, path, cases in sources:
        try:
            loaded = load_decisions(path, [ticket["id"] for ticket in read_jsonl(cases)])
        except (OSError, ValueError) as exc:
            say(f"  FAIL {show(path)}: {exc}")
            ok = False
            continue
        say(f"  PASS {show(path)}: {len(loaded)} valid decisions, each ID in {show(cases)} exactly once")
        decisions.update(loaded)
        groups.update(dict.fromkeys(loaded, group))
        signals.update(load_signals(path))

    say()
    say(f"Policy compliance (labels in {show(args.expected)})")
    failures = policy_failures(decisions, expected)
    for group in dict.fromkeys(groups.values()):
        ids = [case_id for case_id in decisions if groups[case_id] == group]
        labeled = [case_id for case_id in ids if case_id in expected]
        passed = sum(case_id not in failures for case_id in labeled)
        unlabeled = len(ids) - len(labeled)
        note = f"; {unlabeled} unlabeled, checked for an allowed outcome only" if unlabeled else ""
        say(f"  {group} cases: {passed}/{len(labeled)} match policy.md{note}")
    by_check = []
    for check in CHECKS:
        ids = [case_id for case_id in decisions if check in expected.get(case_id, {}).get("checks", ())]
        if ids:
            by_check.append(f"{check} {sum(case_id not in failures for case_id in ids)}/{len(ids)}")
    say("  by check: " + ", ".join(by_check))
    for case_id, problem in failures.items():
        checks = ", ".join(expected.get(case_id, {}).get("checks", ["unlabeled"]))
        say(f"  FAIL {case_id} [{checks}] {problem}")
    ok = ok and not failures

    public = {case_id: decision for case_id, decision in decisions.items() if groups[case_id] == "public"}
    if public and args.reference:
        say()
        say(f"Agreement with {show(args.reference)} (public cases)")
        reference = load_decisions(args.reference, list(public))
        same = [
            case_id
            for case_id in public
            if all(public[case_id][field] == reference[case_id][field] for field in DECISION_FIELDS)
        ]
        say(
            f"  {len(same)}/{len(public)} exact matches ({len(same) / len(public):.1%}) "
            "on route, action, priority, and escalate"
        )
        for case_id, new in public.items():
            if case_id in same:
                continue
            old = reference[case_id]
            changed = ", ".join(field for field in DECISION_FIELDS if old[field] != new[field])
            if case_id not in expected:
                verdict = "no policy label"
            elif case_id not in failures:
                verdict = "fix: the reference violates policy"
            elif case_id not in policy_failures({case_id: old}, expected):
                verdict = "REGRESSION: the reference follows policy, these decisions do not"
            else:
                verdict = "both violate policy"
            say(f"  {case_id} {label(old)} -> {label(new)} ({changed} changed): {verdict}")
            if case_id in expected:
                say(f"      policy: {expected[case_id]['why']}")

    say()
    say("Sensitivity: deliberately weakened rules replayed on the recorded signals")
    if not decisions or set(signals) != set(decisions):
        say("  SKIP: needs signals recorded by run.py --mode api or heuristic for every decision")
    else:

        def replay(rules):
            return {case_id: apply_policy(case_id, values, rules) for case_id, values in signals.items()}

        if replay(RULES) != decisions:
            say("  FAIL: the recorded signals do not reproduce these decisions")
            ok = False
        already_failing = set(policy_failures(replay(RULES), expected))
        for name, rules in weakened_rules():
            caught = sorted(set(policy_failures(replay(rules), expected)) - already_failing)
            say(f"  {'caught' if caught else 'MISSED'}: {name}" + (f" -> {' '.join(caught)}" if caught else ""))
            ok = ok and bool(caught)

    say()
    say(f"RESULT: {'PASS' if ok else 'FAIL'}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
