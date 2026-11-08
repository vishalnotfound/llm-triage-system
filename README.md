# UnderAI Triage

UnderAI Triage queues support tickets, recommends the next action, and records
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
python3 -m underai submit <<'JSON'
{"id":"SUP-100","subject":"Duplicate charge","body":"My October invoice shows the same charge twice."}
JSON
python3 -m underai classify SUP-100
python3 -m underai list --status triaged --route billing
python3 -m underai show SUP-100
python3 -m underai resolve SUP-100 --reason "Checked the invoice and replied in the support system"
python3 -m underai history SUP-100
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
python3 -m underai review SUP-100 --route billing --action reply --priority normal \
  --reason "Confirmed the billing request"
python3 -m underai reopen SUP-100 --reason "Customer reported a second charge"
```

All successful commands emit JSON. Exit code `1` indicates a rejected
operation and `2` indicates a recorded classification failure requiring
review. Classification stores the requested model, prompt and policy hash,
and detected signals. Manual changes record their reason and actor.
Interrupted classifications can be retried after a five-minute lease expires.
A late classification result cannot overwrite a newer attempt or manual decision.

## Operate on a single host

Run as a restricted OS account and set `UNDERAI_DB` to an absolute path on
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
The keyword and direct-model modes are available for comparisons in the batch
runner. The live queue uses the policy-backed classifier.

The synthetic cases and initial policy are adapted from Deployment.inc's
[Open Problem 02 — The Deprecation Notice](https://github.com/Deployment-inc/Deployment.inc-Hiring-Problems/blob/main/problems/OP-02-the-deprecation-notice.md), under the
[CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/).
