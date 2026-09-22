# Implementation plan

Historical implementation plan approved on 2026-09-20. The original four build
phases are complete. This document preserves the original names, structure, and
schema plan; since 0.8.0 the app and command are `jevlab`. For current usage,
see [README.md](../../README.md) for the delivered commands and current limitations,
[RESEARCH.md](../RESEARCH.md) for API evidence, and [DECISIONS.md](../DECISIONS.md) for choices.

## Architecture

```text
Textual TUI ──┐
Typer CLI ────┼──> core services ──> official TypeSafe SDK ──> TypeSafe API
Coach* ───────┤          ├────────> YAML templates
HTTP server* ┘          └────────> SQLite history

* Later phases. Coach also calls its selected provider for advice only.
```

Core owns domain models, question validation, template persistence, credentials,
SDK adaptation, execution, uncertainty gates, pricing, and history. It imports
no UI or coach code. CLI and TUI consume the same result and error models.
Use `AsyncTypeSafeClient` with an explicit direct endpoint and explicit model.
CLI executes the async service; Textual runs it in cancellable workers.
No fallback model generates a decision when Jev fails.

Each real run validates the template and state, snapshots the design, records a
pending run, calls Jev, checks the response against the requested questions, and
stores the full JSON response plus measured metadata. Errors have a code,
actionable message, optional request ID, and retriable status. An interrupted
request stays marked interrupted/unknown, never falsely successful or free.

Use `Decimal` for pricing and integer nanodollars for stored cost estimates.
At the verified price an input token is 42 nanodollars. Store the price basis and
SDK version with each run. Missing usage or an unknown model rate means unknown
cost. Numeric columns are right-aligned; probability/confidence use two decimal
places, latency integer ms, and cost enough decimals to show small nonzero values.

## Directory structure

```text
~/jev/
  pyproject.toml             # Python >=3.12; uv; console entry point
  uv.lock
  Makefile                  # install, dev, test, lint
  README.md
  docs/{RESEARCH,DECISIONS,PLAN}.md
  src/jev/
    __main__.py
    core/
      models.py             # versioned template and run contracts
      templates.py          # safe loading, SDK validation, atomic saving
      config.py             # nonsecret config and preferences
      credentials.py        # Keychain + environment fallback
      client.py             # only workbench TypeSafe transport adapter
      service.py            # execute, snapshot, persist
      storage.py            # SQLite, migrations, history queries
      pricing.py
      thresholds.py
      errors.py
    cli/{app,commands,output}.py
    tui/
      app.py
      screens/
      widgets/
      theme.tcss
    resources/              # one starter template in Phase 1
    coach/                  # Phase 2
  tests/{unit,integration,tui,live}/

~/.jev/
  config.toml               # no secrets
  templates/*.yaml
  jev.db                    # runs; later evals and learn progress
```

SQLite may create transient journal files beside the database; there is one
logical database and no permanent per-run files. Large datasets remain at their
original paths. Source, uv-managed environments, and explicitly exported files
are not application data inside `~/.jev`. No persistent payload logs.

## Template YAML schema v1

Questions mirror the SDK, with the envelope adding documentation and local policy.
This example shows all three types; thresholds are illustrative and uncalibrated.

```yaml
schema_version: 1
name: support-triage
description: Route one support request and inspect its urgency.
state:
  description: An object containing ticket.message as customer-written text.
  format: json
  example:
    ticket:
      message: I was charged twice for the same order. Please refund one charge.
model: jev-1.13.0
questions:
  route:
    type: choice
    instructions: Which team should handle the primary request in `ticket.message`?
    criteria:
      billing: Charges, invoices, subscriptions, or refunds.
      technical: A malfunctioning product feature or integration.
      other: A request outside billing and technical support, or no clear request.
  urgency:
    type: score
    instructions: How time-sensitive is the problem described in `ticket.message`?
    criteria:
      - No deadline or disruption is described.
      - Work is disrupted, but a usable workaround is described.
      - Essential work is blocked and no workaround is described.
  refund_requested:
    type: noul
    instructions: Does `ticket.message` explicitly ask to return a payment?
    criteria:
      "true": The customer explicitly requests money back.
      "false": The customer does not explicitly request money back.
thresholds:
  route:
    kind: confidence
    automate_at_or_above: 0.85
  urgency:
    kind: confidence
    automate_at_or_above: 0.85
  refund_requested:
    kind: noul_probability
    no_at_or_below: 0.10
    yes_at_or_above: 0.90
notes: Example thresholds; validate on labeled cases before relying on them.
```

`state.format` is `text` or `json`; JSON state must be a string, object, or array.
State description/example are design metadata and are not silently added to
requests. Instructions and criteria accept documented JSON structure, preserved
by the editor. `thresholds` and `notes` may be empty. Missing gates mean review.
For Noul, values between the no/yes boundaries go to review; validate
`0 <= no < yes <= 1`. Confidence gates must target Choice or Score questions.
Automation is a reported routing outcome, not permission to execute side effects.

Reject duplicate YAML keys, unsupported tags, path traversal in names, nonfinite
numbers, unknown envelope fields, mismatched threshold IDs, and invalid SDK
question objects. Quote Noul `true`/`false` keys when saving YAML. Use atomic
replacement for saves, and preserve existing content when validation fails.
Require 2–255 Choice options and 2–10 Score levels. Show approximate context
usage for both documented budgets; never call a character estimate exact.
Question IDs remain stable when reordering questions.

## SQLite schema

Phase 1 creates these tables with explicit migrations and foreign keys:

| Table | Columns |
| --- | --- |
| `schema_migrations` | `version INTEGER PRIMARY KEY`, `applied_at TEXT` |
| `template_revisions` | `hash TEXT PRIMARY KEY`, `name TEXT`, `yaml_text TEXT`, `created_at TEXT` |
| `runs` | `id TEXT PRIMARY KEY`, `template_hash TEXT REFERENCES template_revisions(hash)`, `parent_run_id TEXT REFERENCES runs(id)`, `started_at TEXT`, `finished_at TEXT NULL`, `status TEXT`, `requested_model TEXT`, `resolved_model TEXT NULL`, `request_json TEXT`, `response_json TEXT NULL`, `routing_json TEXT NULL`, `latency_ms INTEGER NULL`, `input_tokens INTEGER NULL`, `output_tokens INTEGER NULL`, `cost_nanousd INTEGER NULL`, `price_snapshot_json TEXT`, `sdk_version TEXT`, `request_id TEXT NULL`, `error_json TEXT NULL` |

Use UUID run IDs and UTC ISO timestamps. `request_json` contains the exact state,
questions, and requested model, without credentials or HTTP headers.
`response_json` stores the complete response body even if semantic validation
fails. `routing_json` stores the gate result used for that run. Index run time,
template hash, status, and resolved model; join revision name for template filters.
History search covers name, notes, input, and response text; defer FTS unless the
size cap demonstrates a need. Daily/template totals cover retained history and
separately count runs whose costs are unknown.

Later migrations add:

| Phase | Table | Purpose |
| --- | --- | --- |
| 2 | `learn_progress` | Lesson ID, content version, attempts, completion, last eval/run reference. |
| 3 | `datasets` | Path, file SHA-256, format, row count, state/label mapping. No file copy. |
| 3 | `jobs` | Batch/eval identity, immutable template/dataset/config snapshots, progress, status. |
| 3 | `job_items` | Unique job + row identity, attempt status, run reference, expected labels, row hash. |
| 3 | `evals` | Job reference, scoring/calibration definitions, metrics JSON, threshold snapshot. |

Eval rows reference run inputs and store their labels, enabling inspection after
the source changes. Resume verifies the dataset fingerprint and template revision
and skips successful rows. Unknown interrupted requests are surfaced before a
retry; resumability is not a provider exactly-once guarantee.
Phase 4 pruning removes old runs/dependent case details and unused revisions,
reclaims SQLite space, and preserves small aggregate/progress records with an
explicit indication that individual cases were pruned.

## Phase 1 screens and keys

| Screen | Behavior | Context keys |
| --- | --- | --- |
| Workbench / templates | List and search templates; show recent activity; select a design. | `Ctrl+N` new, `Enter` open, `Ctrl+D` fork, `/` search |
| Template builder | Metadata fields, question list, type picker, instructions, criteria rows, thresholds, validation; advanced YAML view preserves structured values. | `Ctrl+S` validate/save; palette actions add/remove/reorder questions |
| Playground | Template selector and text/JSON state editor; load a file; run in background. | `Ctrl+O` load state, `Ctrl+R` run |
| Result inspector | Choice bars, Score distribution/value, Noul gauge, raw JSON, confidence where applicable, review gates, usage/cost/latency. | `Tab` move focus; palette opens raw response or reruns |
| History | Search/filter runs, inspect immutable inputs, daily/template totals, load an old run into Playground. | `/` search, `Enter` inspect; rerun is a new recorded call |
| Settings / doctor | Masked credential setup, nonsecret config, key presence/source, model, privacy status, disk usage, dependency/backend diagnostics. | `Ctrl+S` save settings |
| Help / command palette | Contextual help and searchable actions. | `Esc` dismiss |

Global keys: `Ctrl+P` command palette, `F1` help, `Esc` back/cancel,
`Ctrl+Q` quit, `Tab`/`Shift+Tab` focus. `?` opens help outside text input on
every screen; in an editor it inserts a question mark, with `F1` available
without leaving the field. Footer labels reflect the focused context.
Unsaved edits are preserved or require a discard decision. Cancellation cannot
guarantee that the remote request stopped or incurred no cost.

## CLI and config contract

Phase 1 commands: `jev`, `jev config`, `jev doctor`, `jev templates`,
`jev templates new`, `jev templates edit`, `jev templates validate`,
`jev run`, `jev history`, and `jev history show`/`rerun`.
Editor commands can open the TUI normally; with `--json` they consume explicit
files/options and never open an editor. Every implemented command supports
`--json`, including error paths. The no-argument root opens the TUI;
`jev --json` emits machine-readable application status instead.

`jev run NAME --state -` reads stdin and emits JSON by default for piping;
`--json` also explicitly selects machine mode. `--state PATH` reads a file;
`--text TEXT` supplies inline text. JSON mode writes one versioned envelope to
stdout, diagnostics/progress to stderr, uses stable exit codes, and never prompts.
File format comes from the template or an explicit `--format` override, not a
guess that changes the meaning of a string. Rerun defaults to the stored design
and state; loading current template content is an explicit choice.

First-time `jev config` asks for a key using masked input and stores it using
the macOS Keychain backend. It also offers environment-only configuration.
`config --json` reports only redacted presence/source and nonsecret settings.
No key values in command arguments, config files, exceptions, or logs.
Coach settings can exist before the optional provider extras are installed.

Doctor is offline by default and checks command collisions, config/schema,
template validity, database health, SDK version, credential backend/presence,
and disk footprint. `doctor --online` can call the model-list endpoint explicitly;
it does not spend inference tokens. Missing keys still allow editing/history.

## Phases and verification

1. **MVP:** implement the above with one support-triage starter template, docs,
   editable global installation, Keychain onboarding, and offline tests. Retention
   configuration exists, but automatic cleanup is explicitly deferred to Phase 4.
   Stop and report exact commands, checks, limitations, and decisions.
2. **Learn + Coach:** first five lessons, bundled labeled datasets, all seven
   requested use-case patterns, optional Anthropic/OpenAI providers. Proposals
   validate like manual templates; explanations are testable hypotheses. Small
   lesson grading uses the core runner; full eval UI follows in Phase 3. Stop.
3. **Evals:** CSV/JSONL mappings, per-primitive metrics, probability calibration,
   confusion matrices, worst misses, threshold curves, two-model/template compare,
   and resumable batch jobs with concurrency/rate budgets. Threshold tuning uses
   calibration versus held-out splits. Show the total cost estimate before work;
   default confirmation limit $1.00, explicit authorization in machine mode. Stop.
4. **Harness + polish:** SDK-preserving Python/LangChain/Pydantic AI exports,
   loopback-only local server, remaining lessons, 90-day/100-MB retention and
   cleanup, portfolio screenshots/GIFs. Exports have contract tests; server calls
   use the same validation, credentials, costs, and storage. Stop.

Phase 1 tests cover invalid templates, all three result shapes, raw-response
preservation, missing usage, revision-based reruns, credential precedence and
redaction, SDK failures/retries through a mocked transport, JSON stdout purity,
and Textual builder/playground/history interactions. Live tests require an
explicit `--live` flag plus a key. No network or billable calls in ordinary tests.
Acceptance requires `ruff`, `pyright`, pytest, a real terminal visual check, and
testing the installed command from another directory. Real inference and native
Keychain checks are reported separately from mocks; do not claim they passed
without executing them.

After Phase 1, intended commands are:

```sh
cd ~/jev
make install
jev config
jev doctor
jev
printf '%s' '{"ticket":{"message":"I was charged twice. Please refund one charge."}}' | jev run support-triage --state - --json
make test
make lint
```

`make install` will warn about an existing unrelated `jev` command before invoking
`uv tool install --editable .`. `make dev` runs the project TUI under uv.
These Phase 1 commands are now implemented.
