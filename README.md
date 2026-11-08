# LLM Triage System

LLM Triage System queues support tickets, recommends the next action, and records
the operator's decision. It covers security incidents, privacy requests,
account access, billing, and general support. The classifier detects policy
signals and a deterministic ruleset applies [the routing policy](policy.md).
Operators handle customer replies, identity verification, and incident
escalation in their support system.

## Run locally

Use Python 3.10+ with SQLite 3.38+ and JSON functions enabled. The application
has no Python package dependencies. Run commands from the repository root.
Set `OPENAI_API_KEY` in the environment or copy `.env.example` to `.env` and
configure your approved provider. Intake, queue management, and manual review
work without a model key.

```sh
python3 -m llm_triage_system submit <<'JSON'
{"id":"SUP-100","subject":"Duplicate charge","body":"My October invoice shows the same charge twice."}
JSON
python3 -m llm_triage_system classify SUP-100
python3 -m llm_triage_system list --status triaged --route billing
python3 -m llm_triage_system show SUP-100
python3 -m llm_triage_system resolve SUP-100 --reason "Checked the invoice and replied in the support system"
python3 -m llm_triage_system history SUP-100
```

- **Intake.** Pass one JSON object per invocation. Reusing the source ticket ID
  with identical content is safe. A content conflict returns an error.
- **Work the queue.** Open urgent tickets appear first, followed by tickets
  needing review, pending tickets, other triaged tickets, and resolved
  tickets. Use `--status`, `--route`, `--limit`, and `--offset` to filter and page.
- **Review.** Provider failures leave the ticket in `needs_review`. Retry
  `classify` or record a policy outcome with `review`. A manual decision can
  also correct an existing recommendation.
- **Resolve and reopen.** Resolve a triaged ticket after handling it, or use
  `reopen` to put it back in the triaged queue with its last decision.

```sh
python3 -m llm_triage_system review SUP-100 --route billing --action reply --priority normal \
  --reason "Confirmed the billing request"
python3 -m llm_triage_system reopen SUP-100 --reason "Customer reported a second charge"
```

All successful commands emit JSON. Exit code `1` indicates a rejected
operation and `2` indicates a recorded classification failure requiring
review. Classification stores the requested model, prompt and policy hashes,
and detected signals. Manual changes record their reason and actor.
Interrupted classifications can be retried after a five-minute lease expires.
A late classification result cannot overwrite a newer attempt or manual decision.

## Use your own policy and cases

A profile is a folder of editable files. The files in the repository root are
the default support profile. Copy the
[facilities example](examples/facilities/) to start a new one.

| File | What to edit |
| --- | --- |
| `policy.md` | Write the domain policy, exceptions, and examples in plain language. The model reads this during classification. |
| `rules.json` | Define the boolean signals the model can report. Give each rule a unique lowercase `signal`, a `description` explaining when it applies, and an `outcome` with `route`, `action`, and `priority`. List rules in precedence order and include a `fallback` outcome. |
| `cases.jsonl` | Add one ticket per line with a unique `id`, `subject`, and `body`. The batch runner reads this file by default. |
| `expected.jsonl` | To test a custom profile, add one expected decision per case ID with `id`, `route`, `action`, `priority`, and a boolean `escalate`. |

The app builds the model's JSON schema from `rules.json` and requires a
boolean for every configured signal. When multiple signals match, the first
rule wins. When none match, the fallback wins. Change rule order and outcomes
in JSON. Keep your policy prose consistent with it.

Routes, actions, and priority labels are editable. The decision always has
the same five fields for integrations. Use `action: "escalate"` to set
`escalate` to true and `priority: "urgent"` to put open tickets first in the
queue. Outcomes are recommendations for your workflow.

Run the example against your configured model, then check the results
against its expected decisions:

```sh
python3 -m triage_engine.run --profile examples/facilities \
  --output results/facilities.jsonl
python3 -m triage_engine.evaluate --profile examples/facilities \
  --decisions results/facilities.jsonl
```

To use the same profile with the live queue, select it with `--profile` or
set `LLM_TRIAGE_PROFILE`. Use a separate database for each workflow. A profile
change applies to future classifications and manual reviews.

```sh
export LLM_TRIAGE_PROFILE=examples/facilities
export LLM_TRIAGE_DB=var/facilities.sqlite3
head -n 1 examples/facilities/cases.jsonl | python3 -m llm_triage_system submit
python3 -m llm_triage_system classify FAC-1
```

## Operate on a single host

Run as a restricted OS account and set `LLM_TRIAGE_DB` to an absolute path on
persistent local storage. The default is `var/triage.sqlite3`. An operator or
integration can supply `--db` and `--actor` before the subcommand. The actor
value is an audit label. Access control comes from the host account and
filesystem permissions.

Restrict access to the database and backups because they contain customer
messages. Use SQLite's online backup mechanism for live backups and test
restores before relying on them. Configure your retention and deletion
process for the database, backups, and model provider before sending customer
data. Model classification sends the ticket subject and body to the
configured provider.

For integration, invoke `submit` and `classify` from the intake job and monitor
`pending` and `needs_review` for stalled or failed classifications. Before
rollout, test routing in shadow mode with representative, appropriately
handled tickets and review the urgent and privacy outcomes with operators.

## Evaluate routing

The existing synthetic cases cover routing precedence, privacy, account
changes, security reports, and prompt injection. Validate recorded runs offline:

```sh
python3 -m triage_engine.evaluate --extra-decisions evals/recorded/extra_decisions.jsonl
```

Use `python3 -m triage_engine.run --help` to generate fresh batch decisions.
Pass their paths to the evaluator with `--decisions` and `--extra-decisions`.
The direct-model comparison is available in the batch runner. The keyword
smoke check requires the original support signals. The live queue uses the policy-backed classifier.

The synthetic cases and initial policy are adapted from Deployment.inc's
[Open Problem 02 — The Deprecation Notice](https://github.com/Deployment-inc/Deployment.inc-Hiring-Problems/blob/main/problems/OP-02-the-deprecation-notice.md), under the
[CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/).
