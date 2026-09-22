# Command and configuration reference

[Documentation index](README.md) · [Quickstart](QUICKSTART.md)

Use this page for scripting contracts, saved templates, evaluation, configuration,
and optional integrations. The [project README](../README.md) introduces the core workflow.

[CLI](#cli-and-pipes) · [Templates](#templates-and-keys) · [Evaluation](#evaluate-tune-batch-and-compare) ·
[Exports](#export-a-decision-into-your-project) · [Coach](#optional-coach) · [Upgrading](#upgrading-from-jev)

## CLI and pipes

Every implemented command accepts `--json`, before or after the command/subcommand.
JSON stdout is one envelope: `{"schema_version":1,"ok":true,"data":...}` or
`{"schema_version":1,"ok":false,"error":...}`. Diagnostics go to stderr.
JSON mode does not prompt for text or open the TUI; macOS may still require
permission to access Keychain. Use environment mode for unattended execution.

```sh
jevlab --version
jevlab templates --json
jevlab_profile="$(jevlab --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["data"]["data_directory"])')"
jevlab templates new my-triage --from "$jevlab_profile/templates/support-triage.yaml" --json
jevlab templates edit my-triage
jevlab templates validate "$jevlab_profile/templates/my-triage.yaml" --json

printf '%s' '{"ticket":{"message":"I was charged twice. Please refund one charge."}}' \
  | jevlab run support-triage --state - --json

jevlab run support-triage --state ./ticket.json --json
jevlab run support-triage --text '{"ticket":{"message":"The export button is broken."}}' --json
jevlab history --template support-triage --status succeeded --json
jevlab history --search refund --json
jevlab history show RUN_ID --json
jevlab history rerun RUN_ID --json
```

`--state -` defaults to JSON output even without `--json`. The input format comes
from the template, with an explicit `--format text|json` override available.
Imports are limited to 2 MB of UTF-8 text. JSON state must be a string, object,
or array. Run IDs accept unambiguous prefixes. History JSON includes daily and
per-template totals across all retained runs, separate from the filtered list.

| Exit code | Meaning |
| --- | --- |
| 0 | Command completed; inspect doctor findings or routing separately |
| 2 | Invalid arguments, input, configuration, or local storage |
| 3 | Missing credential or unavailable Keychain |
| 4 | API, network, timeout, or response-validation failure |
| 130 | Interrupted CLI command |

Failures after run creation include a `run_id` for inspection. No API error is
silently replaced with a successful judgment. Retryable failures include a
suggested fix and a `retryable` flag.
Provider failures retain their message, HTTP status, request ID, and a
credential-redacted response body. F2 opens those details in the TUI;
`jevlab --verbose history show RUN_ID` shows them for a saved failure in the CLI.
Older failures may lack a body because earlier versions discarded it.

## Templates and keys

Templates are human-readable YAML with a versioned envelope around the official
SDK question types. See the complete [starter template](../src/jevlab/resources/support-triage.yaml)
and the [original schema design](archive/PLAN.md); the current validator is
[`core/models.py`](../src/jevlab/core/models.py).

```yaml
schema_version: 1
name: refund-check
description: Detect an explicit request for money back.
model: jev-1.13.0
state:
  description: The customer's message, without unrelated conversation history.
  format: text
  example: Please refund the duplicate charge.
questions:
  refund_requested:
    type: noul
    instructions: Does the customer explicitly request money back?
    criteria:
      "true": An explicit request to return a payment.
      "false": No explicit request to return a payment.
thresholds:
  refund_requested:
    kind: noul_probability
    no_at_or_below: 0.10
    yes_at_or_above: 0.90
notes: Illustrative cutoffs; validate on labeled examples.
```

The builder edits metadata and individual questions in guided forms, with an
advanced YAML editor. New designs contain one Choice question, text state, and
no automation thresholds: their results request review until a policy is set.
Field help and Ctrl+E explain the compact editor's controls. Labels, examples,
and edit-time errors explain instructions,
criteria, ordered Score levels, models, state format, and human-review thresholds.
It validates SDK types and local rules before an atomic save. Choice requires
2–255 options; Score requires 2–10 levels.
Duplicate keys, YAML aliases/tags, invalid thresholds, and unsafe names are rejected.
Quote Noul's `true` and `false` YAML keys. State description/example, notes, and
thresholds remain local metadata; only state, model, and questions are sent.

| Key | Action |
| --- | --- |
| Ctrl+P | Search command palette |
| Tab / Shift+Tab | Move focus |
| Enter | Open selected template/run; activate controls |
| Ctrl+N / Ctrl+D | New / fork template in the browser |
| / | Search in template browser or history |
| Ctrl+S | Validate/save in builder or settings; apply in dialogs |
| Ctrl+O / Ctrl+R | Load state file / call Jev in playground |
| Esc | Back or cancel; protect unsaved drafts |
| ? / F1 | Help; use F1 while typing in a field |
| Ctrl+Q | Quit, checking unsaved drafts |

The dark theme uses grayscale and a cyan accent. `NO_COLOR` is respected.
A terminal of at least 80 columns by 24 rows is supported. Longer forms and results scroll.

## Storage, costs, and privacy

New application profiles live in `~/.jevlab/`:

```text
config.toml          nonsecret settings
templates/*.yaml     current reusable designs
jevlab.db            runs, template revisions, lesson progress/attempts, migrations
```

`JEVLAB_HOME` overrides this directory for isolated profiles/tests. Upgraded
installations can retain `~/.jev/` and `jev.db` as described [above](#upgrading-from-jev).
Source and uv's managed Python environments live separately. Files are created with private
permissions; inputs and responses are ordinary local SQLite data, not encrypted
by the application. The app creates no persistent payload logs.

New templates default to **jev-1.13.0**. Aliases are accepted, and runs retain both
requested and returned model IDs. The verified published rate is **$0.042 per
million input tokens, output free**, dated 2026-09-20. Cost is an estimate based on
reported usage and a saved rate snapshot, not a billing receipt. Unknown usage
or unknown model pricing stays unknown. Totals count unknown-cost runs separately.
Latency measures the complete SDK call, including retries, excluding Keychain lookup.
Comparison timings also include the brief wait to register both linked history rows.

Requests use the SDK's two retries by default, a 10-second timeout per HTTP
operation, and a 45-second overall deadline including credential lookup. Keychain
lookup is limited to five seconds (or the shorter overall deadline); a lookup
timeout reports that no API request was sent. Configure request limits with
`jevlab config --set max_retries=2 --set timeout_seconds=10 --set deadline_seconds=45`.
Comparisons perform one bounded shared key lookup before the two per-call
deadlines. Doctor's preliminary credential inventory is also bounded; its online
models check then uses the configured request deadline separately.
Cancellation cannot establish whether the server completed or billed a request;
interrupted/failed calls retain that uncertainty. A hard process kill can leave
a pending record. Context counts use a character-based approximation; the server
enforces the actual 64k total / 32k state-plus-longest-question limits.

Live docs describe No Training as a policy and ZDR as an enterprise arrangement;
the SDK exposes no documented per-request toggles. Settings report these facts
without pretending to enable or verify ZDR. See [verified research](RESEARCH.md).

History maintenance is active: by default, eligible history expires after **90 days**
or when the database exceeds **100,000,000 bytes**, whichever applies first. It runs
at startup and after runs, at most once per minute in a long-lived process. It also
compacts SQLite/WAL files. To inspect or apply cleanup yourself:

```sh
jevlab clean --dry-run --json
jevlab clean --json
jevlab doctor --json
jevlab config --set retention_days=90 --set retention_bytes=100000000 --json
```

The TUI's **Cleanup** screen previews eligible records before applying the current
policy. Related reports, lesson attempts, runs, and rerun ancestors expire together,
using their newest activity. Learning achievements and dataset registrations remain;
expired attempts can no longer be inspected. Pending/active work and the current
result are protected, so large active collections can temporarily exceed the cap.
Templates, configuration, external datasets, and exported files are never pruned.
The budget covers SQLite plus WAL, not the entire source or profile directory.

## Evaluate, tune, batch, and compare

Open `jevlab eval`, `jevlab batch`, or `jevlab compare` directly, or choose them from the
home screen / Ctrl+P palette. Evals and batches share row checkpoints, cost
previews, cancellation, and explicit resume controls. Every returned answer comes
from the official Jev SDK; calculating metrics and moving sliders is local.

Try the three-case synthetic fixture shipped with the source:

```sh
cd ~/jevlab
jevlab datasets import examples/support-eval.jsonl --template support-triage --json
jevlab eval plan support-triage examples/support-eval.jsonl --json
jevlab eval run support-triage examples/support-eval.jsonl --concurrency 4 --rate 2 --json
jevlab eval --json
jevlab eval show JOB_ID --json
```

The `run` command makes three billable calls. The example tests plumbing and
illustrates the format; it is not a performance benchmark. Use representative,
independently labeled data for your own decisions. To use a library design:

```sh
jevlab library fork support-routing my-routing
jevlab library export-data support-routing ~/Downloads/routing-exercise.jsonl --json
jevlab eval run my-routing ~/Downloads/routing-exercise.jsonl --json
```

Export refuses to overwrite an existing file. It strips teaching notes from the
rows, preserving states and labels. Large input files are referenced by absolute
path and SHA-256 in SQLite, never copied into the data directory.

### Dataset format

JSONL has one object per line. `id` is optional (generated as `row-1`, etc.).
`state` is nonempty text, a JSON object, or an array. Eval rows require an expected
label for **every question**; batch rows may omit labels. Unknown fields/question
IDs, duplicate IDs/JSON keys, missing labels, nonfinite numbers, and malformed
rows fail validation before calls begin.

```json
{"id":"ticket-1","state":{"ticket":{"message":"Please refund the duplicate charge."}},"expected":{"route":"billing","impact":0,"refund_requested":true}}
```

Choice labels exactly match option keys. Score labels are zero-based integer
levels. Noul labels are JSON booleans. CSV uses `state`, optional `id`, and
`expected.<question>` columns; when the template expects JSON, the state cell
contains escaped JSON. Noul CSV labels are lowercase `true` / `false`:

```csv
id,state,expected.route,expected.impact,expected.refund_requested
ticket-1,"{""ticket"":{""message"":""Please refund the duplicate charge.""}}",billing,0,true
```

Imports stream source rows and currently accept up to **10,000 rows, 100 MiB per
file, and 1 MiB per row**. Split larger files. Evals retain compact observations
for interactive analysis; complete inputs and responses remain in ordinary run
history. The dataset registry stores metadata, not a second copy of the states.

### Read the metrics and choose thresholds

The report shows per-question accuracy, ten reliability bins, a reliability
chart, Choice confusion matrices, worst misses linked to individual runs,
tokens, estimated costs, latency mean/p50/p95, and returned model versions.

- Accuracy counts all labeled rows, including failed or unrun cases. Returned-
  answer accuracy is also available in JSON. Failure counts are explicit.
- Choice is exact match; Score uses the nearest level (halves up) and also reports
  continuous mean absolute error; Noul uses `P(yes) >= 0.50` for its baseline grade.
- Calibration compares the **predicted class probability** with observed
  correctness. For Score this is the probability of the rounded level; for Noul
  it is `max(p, 1-p)`. Empty bins have no accuracy. API confidence is a separate
  statistic and is not labeled as a calibrated probability of correctness.
- Brier scores use squared probability error: summed across classes for Choice/
  Score (range 0–2), and binary error for Noul (0–1). Calibration, Brier, and Score
  MAE exclude failed/missing answers. Worst misses are sorted by predicted-class
  probability; both probability and SDK confidence are shown.

Choose **Tune thresholds** in an eval report. Tab to a slider and use Left/Right
for 0.01 steps, Home/End for endpoints. Choice/Score use their SDK confidence;
Noul has independent no/yes probability boundaries. Live coverage divides by all
labeled cases; automated accuracy divides by automated cases only, and is unknown
when no case is automated. Saving writes only adjusted gates into the YAML;
changed question/model designs must be evaluated again.

```sh
jevlab eval tune JOB_ID route --threshold 0.90 --json
jevlab eval tune JOB_ID refund_requested --no-below 0.10 --yes-above 0.90 --json
jevlab eval tune JOB_ID route --threshold 0.90 --save --json
```

Thresholds fitted on an eval describe that dataset. Check a separate holdout
before relying on them. Pin a model version when comparing designs or tuning
thresholds; aliases can change, including between resumed calls. Reports expose
returned versions so mixed-model jobs are visible.

### Run and resume a batch

```sh
jevlab batch support-triage --input examples/support-eval.jsonl --output ~/Downloads/jevlab-results.jsonl --concurrency 4 --rate 2 --json
jevlab batch --json
jevlab batch --resume JOB_ID --json
jevlab batch --resume JOB_ID --retry-failed --json
```

Use a new output path. Every attempted row is checkpointed in SQLite; the JSONL
output is an atomic snapshot written on completion or graceful cancellation.
Each output row includes its job/template/dataset identity, case/label metadata,
and complete recorded run. A no-op resume can rebuild output without repeating
successful calls. Output changed by another program is not overwritten; choose a
new output path. A job lock prevents two processes resuming the same job.

Resume uses the saved template and checks the original dataset. Completed rows
are not repeated. Changes during job preparation stop the job before any request;
restore the original file to resume, or preview a new job for changed inputs.
Preparation failures are saved with their reason and completion time.
Cancelled or crashed requests may have finished remotely;
`--retry-unknown` explicitly permits retrying them and may incur duplicate cost.
Authentication errors stop scheduling more work; completed rows stay saved.
Evals also accept `--resume`, `--retry-failed`, and `--retry-unknown` with the
original template name and dataset arguments.

The default is four workers and two new logical calls/second. SDK retries use the
SDK's backoff and may add requests beyond that start rate. This is not a token-
rate limiter. Cost previews use character estimates, exclude unknown overhead/
retry charges, and are **not spending caps**. Interactive batch and eval each
ask before starting, including small jobs. The TUI offers a **Don't ask again**
checkbox; the interactive CLI asks whether to remember an accepted choice.
The preferences are separate and can be restored in Settings or with
`jevlab config --set confirm_batch_cost=true` and
`jevlab config --set confirm_eval_cost=true`. `--yes` skips a prompt for one command
without changing preferences. JSON/noninteractive jobs still require explicit
`--yes` above `confirm_cost_usd` (default $1.00), or for an unknown model price;
interactive preferences do not waive this scripting safeguard. Failed jobs return a saved
report with `ok: false` and exit code 4; inspecting that report later succeeds.

### Compare designs or model versions

```sh
jevlab_profile="$(jevlab --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["data"]["data_directory"])')"
jevlab templates new support-variant --from "$jevlab_profile/templates/support-triage.yaml" --json
jevlab templates edit support-variant
jevlab compare support-triage support-variant --text '{"ticket":{"message":"Please refund the duplicate charge."}}' --json
```

The TUI shows both results side by side with probability and value differences.
CLI also accepts `--state FILE`, `--state -`, `--left-model`, and `--right-model`.
Both designs receive identical state; each side is a real recorded call. Numeric
deltas are suppressed for missing answers or changed primitive types/criteria.
Matching criteria alone do not guarantee equivalent question meanings. A single
comparison does not establish which design is better across your workload.

## Export a decision into your project

Choose **Export** in the TUI to preview a standalone module, or use:

```sh
jevlab export support-triage --lang python --output ~/Downloads/decision.py
jevlab export support-triage --lang langchain --output ~/Downloads/decision_chain.py --json
jevlab export support-triage --lang pydantic-ai --output ~/Downloads/decision_tool.py --json
```

Use new output paths; existing files are never overwritten. Exports preserve the
saved model, questions, and thresholds, and contain typed sync/async result handling.
They require the official SDK, use `TYPESAFE_API_KEY` from the receiving process,
and do not depend on `jevlab` or its local profile. For example, in your own project:

```sh
uv add 'typesafe-sdk==0.7.1'
```

```python
from decision import evaluate

result = evaluate({"ticket": {"message": "Please refund the duplicate charge."}})
gate = result.routing["route"]
if gate.disposition == "automate":
    print(gate.value)
else:
    print("Send to human review")
```

LangChain exports a Runnable; Pydantic AI exports a Tool for an existing agent.
Both wrap the same SDK call and response validation. See
[tested versions, installation, and complete examples](INTEGRATIONS.md).
Framework packages are not required to export. Exported modules include the
template's examples and notes, so review those before publishing them.

## Optional coach

Anthropic Messages and OpenAI Responses are supported through their official SDKs.
The coach starts disabled. TypeSafe alone is sufficient for the workbench and
lessons. Install coach dependencies in the **global tool environment** (installing
them only in the project's development environment is insufficient):

```sh
cd ~/jevlab
make install COACH=both       # or COACH=anthropic / COACH=openai
jevlab config --provider anthropic
```

Later `make install` upgrades preserve the coach extras already installed.
Keep using your existing source folder if it is still named `~/jev`.
Setup stores keys in macOS Keychain, with `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`
as environment fallbacks. Never put a key in a command argument or configuration
file. Each provider now keeps its own configurable model: `anthropic_model`
defaults to `claude-haiku-4-5-20251001`; `openai_model` defaults to `gpt-5.6-luna`.
These API identifiers were verified on 2026-09-20; your account must have access.
Existing choices are preserved; the display name `Opus 5` becomes `claude-opus-5`.
Provider/model selection is also in Settings, or use:

```sh
jevlab config --set coach_provider=anthropic --json
jevlab doctor --coach --offline
jevlab doctor --coach
jevlab coach
jevlab coach design 'Check one claim against retrieved evidence' \
  --name grounding-proposal --output ./proposal.yaml --json
jevlab coach critique my-grounding --json
jevlab coach explain RUN_ID --json
```

`jevlab doctor --coach` reports both installed SDKs, key sources (never key values),
and model IDs. It shows an estimated price and asks before making **one small live
request per ready provider**, with no automatic retries. The request critiques a
built-in synthetic template; no personal state or history is sent or saved.
An HTTP success only passes if the returned coaching advice also validates.
Errors show the provider's redacted message and a specific next step, including
rejected keys, model access, billing, rate limits, and network failures.

For scripts, inspect readiness and estimates without spending, then explicitly
authorize the live requests:

```sh
jevlab doctor --coach --offline --json
jevlab doctor --coach --yes --json
jevlab config --set coach_provider=openai --json
```

The diagnostic JSON uses the existing versioned envelope; partial failures retain
both provider reports in `data`. Exit 3 means setup or authorization is needed;
exit 4 means a live check failed. The ordinary `jevlab doctor` stays offline.
The older `coach_model` setting remains an alias for the selected provider's model;
switching providers no longer carries the other provider's model across.

Review a proposed design with **Edit proposal** before saving. CLI `--output` writes
a validated proposal to a new file; it does not install or execute it. After editing,
import with `jevlab templates new NAME --from ./proposal.yaml`. The builder palette
also offers critique, and results have an **Explain with coach** button.

Advice is grounded in a dated TypeSafe design/jaggedness guidance snapshot and
includes one suggested experiment. Explanations are hypotheses, not a Jev reasoning
trace. When enabled, the coach can give feedback after lesson grading; interactive
use asks separately before this additional paid request. Declining feedback or a
coach failure cannot change or erase the grade.

Coach actions send selected designs/states to the separate provider. OpenAI requests
set `store=False`; this does not establish ZDR. Before an interactive request, known
models use dated standard rates for an estimate; other model prices stay unknown.
Coach token usage is shown after the call, while final account cost remains unknown.
Output is bounded by
`coach_max_output_tokens` (4096) and `coach_timeout_seconds` (60). Invalid/truncated
proposals are rejected. Regular coach panels are not retained after exit; lesson
feedback is saved with the attempt. Disable via
`jevlab config --set coach_provider=disabled --json`.

## Local HTTP API

`jevlab serve` runs in the foreground on **127.0.0.1**, using saved templates and the
same credentials, history, routing, and direct official SDK as the workbench.
Set a separate local access token in your shell; it is not your TypeSafe key:

```sh
export JEVLAB_SERVER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
jevlab serve --check --json
jevlab serve --port 8766
```

Press Ctrl+C to stop. `--check` validates configuration without opening a listener
or checking provider connectivity. `--json` on a running server emits one startup
envelope after binding; poll `/health` for readiness. The token exists only in the
environment and must also be supplied to your client (for example, start that client
from the same configured shell). Never reuse a provider key as the local token.

| Route | Purpose |
| --- | --- |
| `GET /health` | Local process health, without authentication or provider inference. |
| `GET /templates` | Authenticated list of saved template metadata. |
| `POST /templates/<name>/run` | Authenticated, recorded Jev call with JSON `state`. |

A Python client, run in an environment with the same local token:

```python
import json
import os
from urllib.request import Request, urlopen

request = Request(
    "http://127.0.0.1:8766/templates/support-triage/run",
    data=json.dumps(
        {"state": {"ticket": {"message": "Please refund the duplicate charge."}}}
    ).encode(),
    headers={
        "Authorization": "Bearer " + os.environ["JEVLAB_SERVER_TOKEN"],
        "Content-Type": "application/json",
    },
)
with urlopen(request, timeout=60) as response:
    result = json.load(response)
print(result["data"]["routing"])
```

This POST makes a billable call; health/listing do not. A request with unknown or
above-limit estimated cost returns HTTP 409 before dispatch. After reviewing that
estimate, explicitly send `"authorize_cost": true` alongside `state` to proceed.
Cost estimates remain approximate, not hard spending caps. Requests can only use
saved templates; they cannot override questions, models, or application settings.

Defaults are four concurrent calls and two new call starts/second, configurable
with `--concurrency` and `--rate`. Extra requests receive 429/503, without a queue.
Bodies are capped at 2 MB and ten seconds to upload. The API rejects browser Origin
headers and unexpected Host headers; it is intended for local backend clients,
not public hosting or direct browser integration. Access and wire logs are disabled.
App responses use the same versioned JSON envelope as the CLI; the HTTP server may
reject malformed connections before the app can supply that envelope. A disconnected
client does not guarantee upstream cancellation or eliminate possible billing.


## Upgrading from `jev`

Run the installer from your existing checkout, even if its folder is still named
`~/jev`. The new executable is `jevlab`; no `jev` compatibility command is installed.
The installer preserves coach extras and removes the old `jev-workbench` tool
only after the new tool installs successfully. Other programs named `jev` are
not removed.

If `~/.jev/` exists and `~/.jevlab/` does not, JevLab keeps using `~/.jev/` and
shows a notice. Templates, history, settings, and progress stay in place; there
is no copy or database move. If both folders exist, `~/.jevlab/` takes precedence;
use `JEVLAB_HOME=~/.jev jevlab` to explicitly open the older profile. `jevlab doctor`
shows the active folder. The old database keeps its `jev.db` filename.

New settings use `JEVLAB_HOME` and `JEVLAB_SERVER_TOKEN`. The old `JEV_HOME` and
`JEV_SERVER_TOKEN` names remain fallback inputs when the corresponding new name
is unset. Provider key variables keep their official names. Existing Keychain
entries under `jev-workbench` remain readable; newly saved keys use `jevlab`.
No key needs to be entered again merely because the application was renamed.


## Project files and regression checks

Named templates and existing exit codes remain supported. Run, edit, export, eval,
and batch also accept explicit project YAML paths. See the [project workflow](PROJECTS.md)
for portable baselines, paired comparisons, and frozen-threshold verification.
The new `eval compare` and `eval verify` commands use exit 5 for failed quality
checks; their version-1 JSON envelopes retain the report under `data`.
