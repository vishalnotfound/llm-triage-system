"""Evaluate triage decisions: contract, policy, baseline agreement, sensitivity."""

import argparse
import json
from pathlib import Path

from .contract import FIELDS, read_jsonl, validate_decision
from .profile import load_profile
from .triage import apply_policy, validate_signals


ROOT = Path(__file__).resolve().parent.parent
DECISION_FIELDS = ("route", "action", "priority", "escalate")
CHECKS = ("incident", "false_alarm", "injection", "identity", "third_party", "precedence", "routine")

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


def load_expected(path, allowed):
    expected = {}
    for row in read_jsonl(path):
        validate_decision({field: row[field] for field in FIELDS})
        if (row["route"], row["action"], row["priority"]) not in allowed:
            raise ValueError(f"{path}: label for {row['id']} is not an outcome policy.md allows")
        if row["id"] in expected:
            raise ValueError(f"{path}: duplicate label for {row['id']}")
        expected[row["id"]] = row
    return expected


def load_signals(decisions_path):
    log = decisions_path.with_suffix(".run.json")
    if not log.exists():
        return {}
    trace = json.loads(log.read_text(encoding="utf-8")).get("trace", [])
    return {item["id"]: item["signals"] for item in trace if "signals" in item}


def policy_failures(decisions, expected, allowed):
    """Map case ID to a problem for every decision that policy.md does not allow."""
    failures = {}
    for case_id, decision in decisions.items():
        want = expected.get(case_id)
        if (decision["route"], decision["action"], decision["priority"]) not in allowed:
            failures[case_id] = f"{label(decision)} is not an outcome policy.md allows"
        elif want and any(decision[field] != want[field] for field in DECISION_FIELDS):
            failures[case_id] = f"expected {label(want)}, got {label(decision)}. {want.get('why', '')}"
    return failures


def weakened_rules(rules):
    """Deliberately broken copies of the built-in rules that the suite must catch."""
    yield "accept identity claims (verify_identity becomes reply)", [
        (signal, route, "reply" if action == "verify_identity" else action, priority)
        for signal, route, action, priority in rules
    ]
    yield "drop the incident rule (rule 1)", [rule for rule in rules if rule[1] != "safety"]
    yield "drop the third-party refusal (rule 2)", [rule for rule in rules if rule[2] != "refuse"]
    others = [rule for rule in rules if rule[1] != "billing"]
    billing = [rule for rule in rules if rule[1] == "billing"]
    yield "check billing before privacy and access", others[:2] + billing + others[2:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=ROOT, help="Directory containing policy.md and rules.json")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--cases", type=Path, help="Defaults to cases.jsonl in the profile")
    parser.add_argument("--extra-decisions", type=Path, help="decisions for --extra-cases")
    parser.add_argument("--extra-cases", type=Path, default=ROOT / "evals" / "extra_cases.jsonl")
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--reference", type=Path, help="optional reference decisions for the primary cases")
    parser.add_argument("--out", type=Path, help="also save this report as UTF-8 text")
    args = parser.parse_args()

    lines = []

    def say(text=""):
        print(text)
        lines.append(text)

    try:
        profile = load_profile(args.profile)
        bundled = profile.directory == ROOT
        if args.cases is None:
            args.cases = profile.directory / "cases.jsonl"
        if args.decisions is None:
            if not bundled:
                parser.error("--decisions is required for custom profiles")
            args.decisions = ROOT / "evals" / "recorded" / "decisions.jsonl"
        if args.expected is None:
            args.expected = (ROOT / "evals" if bundled else profile.directory) / "expected.jsonl"
        allowed = profile.outcomes
        expected = load_expected(args.expected, allowed)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
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
    if not bundled:
        missing = sorted(set(decisions) - set(expected))
        if missing:
            say(f"  FAIL: missing expected outcomes for {', '.join(missing)}")
            ok = False
    failures = policy_failures(decisions, expected, allowed)
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
            elif case_id not in policy_failures({case_id: old}, expected, allowed):
                verdict = "REGRESSION: the reference follows policy, these decisions do not"
            else:
                verdict = "both violate policy"
            say(f"  {case_id} {label(old)} -> {label(new)} ({changed} changed): {verdict}")
            if case_id in expected:
                say(f"      policy: {expected[case_id].get('why', '')}")

    say()
    say("Sensitivity: deliberately weakened rules replayed on the recorded signals")
    if not decisions or set(signals) != set(decisions):
        say("  SKIP: needs signals recorded by run.py --mode api or heuristic for every decision")
    else:
        try:
            for values in signals.values():
                validate_signals(values, profile)
        except ValueError as exc:
            say(f"  FAIL: {exc}")
            ok = False
        else:
            def replay(rules):
                return {
                    case_id: apply_policy(case_id, values, rules, profile.fallback)
                    for case_id, values in signals.items()
                }

            if replay(profile.rules) != decisions:
                say("  FAIL: the recorded signals do not reproduce these decisions")
                ok = False
            else:
                say("  PASS: recorded signals reproduce the decisions")
            if bundled and args.expected == ROOT / "evals" / "expected.jsonl":
                already_failing = set(policy_failures(replay(profile.rules), expected, allowed))
                for name, rules in weakened_rules(profile.rules):
                    caught = sorted(set(policy_failures(replay(rules), expected, allowed)) - already_failing)
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
