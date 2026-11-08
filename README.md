# LLM Triage System

Classify a file of tickets with your own policy or manage tickets in a local
queue. The model identifies signals. Your rules turn those signals into a
route, action, and priority.

## Run a batch

From the repository root, use Python 3.10 or later. No packages need to be
installed. Set `OPENAI_API_KEY` in your environment or copy
[.env.example](.env.example) to `.env` and configure your model provider.
Classification sends ticket subjects and bodies to that provider.

```sh
python3 -m llm_triage_system batch
```

Open `results/report.json`. It contains a run summary with ticket count,
model, total token usage, and elapsed time, followed by each ticket's decision,
detected signals, token usage, and classification time. Token counts are
`null` if the provider does not report them. Each successful run replaces the
report. Use `--output path/to/report.json` to save it elsewhere.

## Use your own policy and cases

The repository root contains the default support profile. Edit its files
directly or copy the [facilities example](examples/facilities/) into a new folder.

| File | What to edit |
| --- | --- |
| [policy.md](policy.md) | Write your instructions, exceptions, and examples. |
| [rules.json](rules.json) | Set the model's boolean signals, their descriptions, and their routing outcomes. |
| [cases.jsonl](cases.jsonl) | Enter one ticket per line with a unique `id`, `subject`, and `body`. |

Each file includes editing notes. In `rules.json`, order rules by precedence.
The first true signal wins, and `fallback` applies if none match. You can
change routes, actions, and priority labels. The action `escalate` sets the
decision's escalation flag, and `urgent` puts open tickets first in the queue.

```sh
python3 -m llm_triage_system --profile examples/facilities batch
```

## Work a live queue

The optional queue uses SQLite 3.38+ with JSON functions. Intake, listing,
manual review, and history work without a model key.

```sh
python3 -m llm_triage_system submit <<'JSON'
{"id":"SUP-100","subject":"Duplicate charge","body":"My invoice shows the same charge twice."}
JSON
python3 -m llm_triage_system classify SUP-100
python3 -m llm_triage_system list
python3 -m llm_triage_system history SUP-100
```

If classification fails, the ticket goes to `needs_review`. Retry
`classify` or record a manual decision with `review`. Use `resolve` when
the work is complete and `reopen` if it needs further attention. Review,
resolution, and reopening require a reason.

```sh
python3 -m llm_triage_system review SUP-100 --route billing --action reply \
  --priority normal --reason "Confirmed the duplicate charge"
python3 -m llm_triage_system resolve SUP-100 --reason "Replied to the customer"
```

Set `LLM_TRIAGE_PROFILE` to use a profile across commands. Set `LLM_TRIAGE_DB`
to place the queue database on persistent storage. It defaults to
`var/triage.sqlite3`. Use a separate database per workflow and restrict
access to databases, backups, and reports because they can contain customer
information. Run `python3 -m llm_triage_system --help` for all commands.

The original synthetic cases and support policy are adapted from Deployment.inc's
[Open Problem 02 — The Deprecation Notice](https://github.com/Deployment-inc/Deployment.inc-Hiring-Problems/blob/main/problems/OP-02-the-deprecation-notice.md)
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
