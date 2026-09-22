# Changelog

Versions describe the workbench application, not TypeSafe's model versions.

## 0.9.1 — 2026-09-22

Status: complete.

- Add an owner-triggered PyPI trusted-publishing workflow. The tagged source is
  checked, tested, and built without upload permission; a separate protected job
  can publish only after the maintainer dispatches and approves it. PyPI remains
  unpublished until the owner configures the publisher and starts the workflow.
- Put the README's install and first-result path before the large recorded demo.
- Avoid resolving development dependencies during source installation.
- Label demo performance and usage as unmeasured rather than displaying fixture zeros.
- Let first-time Simple setup ask for the TypeSafe key directly and report when
  no usable key has been found. Keep optional coach key status out of that default
  view without changing machine-readable config output.
- Give the maintainer exact first-publication and clean-install verification steps.
- Protect the GitHub `pypi` environment with owner review and a `v*` tag rule.

Verification: `make lint` passed (Ruff, formatting, Pyright); `make test` passed
with **791 offline tests** and one opt-in live test skipped. A cold isolated
editable install used only runtime dependencies, and the wheel's demo, guide,
config and version worked outside the checkout. The wheel and source archive,
complete Git history, author metadata, and local documentation links passed
privacy/inventory checks. Both GitHub workflows passed actionlint. **No live
provider call or PyPI upload was made.** The PyPI workflow has not been run;
macOS and source installation remain the supported routes.

## 0.9.0 — 2026-09-22

Status: complete.

- Run, edit, evaluate, batch and export project YAML without importing it into a
  personal catalog. Save in place and reject conflicting external edits.
- Save portable evaluation baselines; compare paired cases and enforce explicit
  accuracy/regression limits. Failed quality checks use exit 5 with a full JSON report.
- Freeze tuned thresholds, then verify on later disjoint cases with matching
  design, gates and resolved model. Preserve design/dataset/model provenance.
- Replace full-table exact-ID lookups and repeated job reads with indexed,
  chunked access. A synthetic 1,000-row summary over 50,000 runs fell from
  10.579 s to 0.019–0.026 s with identical totals; this is not an API benchmark.
- Include the locally verified 0.8.1 repairs, focused developer README, archived
  milestone notes, MIT license, community templates, security policy and a
  reproducible recorded demo GIF. Update the guide and add a project walkthrough.

Verification: `make lint` passed (Ruff, formatting, Pyright); `make test`
passed with **786 offline tests** and one opt-in live test skipped. The installed
editable command and an isolated Python 3.12 wheel were exercised outside the
checkout. The wheel passed demo, guide, doctor, project planning/export, mocked
SDK evaluations, baseline quality-failure reporting, project threshold saving,
frozen-policy holdout verification, and portable comparison without the original
source or profile. Full-history and artifact privacy scans passed; all 130 local
documentation links resolve. **No live provider API calls were made.**

Limits: outside developer demand remains unverified. Hashes detect exact reused
states, not semantic overlap or statistical independence. Baseline/policy commands
are CLI workflows. Project form saves normalize YAML comments/formatting. macOS
and source installation remain supported; no PyPI publication or Linux expansion.

## 0.8.1 — 2026-09-22

Status: complete; local checkpoint, not published.

- Open the workbench directly, with an optional tour and compact primary actions.
  New templates start with one question and no automation threshold.
- Repair question-type changes, new-template creation, and credential-mode setup.
- Upgrade to TypeSafe SDK 0.7.1 for credential-safe transport errors. Exported
  modules preserve the host application's logging configuration. Local SDK setup
  failures retain their specific reason in history, CLI, and TUI, with no request sent.
- Honor `--json` at the root, group, and command levels; describe and group CLI
  commands by purpose.
- Add the MIT license, package metadata, and contribution instructions. Focus the
  README on the developer workflow and move historical checkpoint notes into a
  documentation archive.

Verification: `make lint` passed (Ruff, formatting, Pyright); `make test` passed
with **744 offline tests** and one opt-in live test skipped. The installed editable
command and a separate Python 3.12 wheel passed checks outside the checkout,
including JSON flag placement, recorded demo, guide, diagnostics, and safe SDK
configuration errors. An 80×24 installed TUI walkthrough covered Home, demo,
one-question creation, Playground, and F2 details. Wheel/source inventories include
the MIT license and current guide, and exclude private profiles and databases;
117 local documentation links resolve. Provider regressions use official SDK
mock transports and synthetic credentials. **No live API calls were made.**

Limits: source installation remains the supported route; no PyPI or GitHub
publication occurred. Linux support, project-owned design files, evaluation
regression gates, and large-history query optimization remain later work.

## 0.8.0 — 2026-09-22

Status: complete.

- Rename the distribution, Python package and installed command to `jevlab`.
  Keep TypeSafe's Jev model names and exported decision interfaces unchanged.
- New profiles use `~/.jevlab/jevlab.db`. Existing `~/.jev` profiles remain in
  place with a notice; legacy databases and their WAL files are never moved or
  merged. `JEVLAB_HOME` selects a profile, with `JEV_HOME` retained as a fallback.
- Read existing `jev-workbench` Keychain keys when no new `jevlab` entry exists.
  Save new keys only under `jevlab`; preserve official provider environment names.
- Preserve installed coach extras and remove the old uv-managed command only
  after the new tool installs successfully. No `jev` command shim is installed.
- Update the guide, current commands, CI and captured terminal screens. Keep
  historical reports labeled as records of the earlier command name.
- Use `JEVLAB_SERVER_TOKEN` for the local API, retaining the former name as a
  fallback. Preserve JSON envelopes, exit codes, HTTP routes and saved records.

Verification: Ruff, formatting and Pyright passed; **714 offline tests passed**
and one explicitly opt-in live test was skipped. The editable tool and a separate
Python 3.12 wheel passed installed CLI checks outside the checkout; the wheel's
TUI recorded demo also passed. The real legacy profile retained its history
counts, template/configuration contents and healthy database; all three provider
keys were found through legacy Keychain entries without displaying values.
Synthetic regressions cover an active SQLite WAL, ambiguous databases, profile
selection, credentials and installer failure recovery. All Git objects and
current files passed the privacy scan; wheel/source inventories contain no
private profile, database, real credentials or personal paths. The bundled demo
and datasets are deliberately synthetic. **No live provider API calls were made.**

## 0.7.1 — 2026-09-21

Status: complete.

- Reject dataset changes during batch/eval preparation before dispatch; save
  initialization failures and preserve checkpoints for a safe resume.
- Bound Jev, comparison and diagnostic credential lookup and event-loop shutdown.
  Keychain timeouts explain
  that no API request was sent; online doctor respects configured request limits.
- Preserve unsaved editor drafts when JSON/YAML nesting is excessive. Local file
  failures, coach HTTP errors, and local server validation now retain useful reasons.
- Reject Score values that contradict their distribution, in the workbench and
  exported modules, with a conservative local rounding allowance.
- Add behavioral regressions and an import-boundary check; update the guide's
  troubleshooting and installation examples. Keep the command and data paths
  unchanged until the separately reviewed rename phase.

Verification: `make lint` passed (Ruff, formatting, Pyright); `make test` passed
with **682 offline tests** and one explicitly skipped live test. The installed
editable command and a separate Python 3.12 wheel installation passed from
outside the checkout, including the key-free demo, guide, diagnostics, JSON
contracts, and specific local-file failures. The guide is included exactly in
both distribution formats. Locked runtime/coach and development dependency
advisory scans reported no known vulnerabilities; installed dependencies passed
their compatibility check. Current source, all reachable/reflog history, and
distribution contents passed the privacy check; credential-pattern matches were
synthetic fixtures. **No live TypeSafe or coach calls were made.**

## 0.7.0 — 2026-09-21

Status: complete.

- Single Jev runs and history reruns start without spending confirmation; results
  show latency, returned token usage and usage-priced cost.
- Separate remembered confirmation preferences for interactive batch and eval,
  cancel-safe TUI checkboxes, and one-off `--yes`. Scripted budget rules stay intact.
- Guided fields across the app: labels, purpose, examples, immediate validation,
  Simple/Expert help and field-specific Ctrl+E explanations.
- Updated English guide, quickstart and publication policy. The scheduled
  publisher was deleted; checkpoints are published during development.
- Includes the preceding 0.6.1 diagnostic fixes. Starts a clean `jevlab` repository
  authored as Javi, without inheriting the old repository's personal author metadata.
  The command remains `jev` until the reviewed rename phase.

Verification: Ruff, formatting and Pyright passed; **623 offline tests passed**
and one opt-in live test was skipped. See [Phase 2](docs/archive/FOLLOWUP_PHASE2.md).
No live API calls were made in this phase; provider behavior is covered with
mocks. Packaged and editable installations were exercised from outside the
project with keys unset.

## 0.6.1 — 2026-09-21

Status: complete. Local Phase 1; publication paused.

- Reject malformed model identifiers with a specific correction before sending
  a request, and show validation feedback in the model fields while editing.
- Preserve provider messages, HTTP status, request IDs, and credential-redacted
  response bodies through failed-run storage, history, CLI output, and TUI F2.
- Keep old invalid-model snapshots readable without permitting unvalidated reruns.

Verification: Ruff, formatting, and Pyright passed; 571 tests passed and one
opt-in live test was skipped. The installed command was checked from a fresh shell
outside the project. Two live requests reproduced the rejection (CLI and TUI),
and two live requests verified the corrected model (CLI and TUI). All regression
failure classes used mocks. See [Phase 1](docs/archive/FOLLOWUP_PHASE1.md).

## 0.6.0 — 2026-09-21

Status: complete.

- English beginner's guide, short quickstart, and a README that starts with the
  purpose of the tool and links to those instructions.
- `jev guide` to read the bundled guide in the terminal, with an optional browser
  view and the existing machine-readable output convention.
- Public release documentation, source-only publication rules, and an honest
  record of the earlier development milestones.

Verification: Ruff, formatting, and Pyright passed. 481 offline tests passed and
one opt-in live test was skipped, on the development interpreter and independently
on Python 3.12.1. The installed command, terminal/browser guide, source archive,
wheel, and documented keyboard flows were verified outside the checkout. One
real TypeSafe request tested the synthetic worked example; no coach request was
made. See [Task C](docs/archive/TASK_C.md) for exact evidence and unverified account/OS steps.

## 0.5.0 — 2026-09-21

Status: complete. Source baseline preserved before work on 0.6.0 began.

- Skippable welcome tour, repeatable tour command, and a free, clearly labeled
  illustrative demo stored with the application.
- Simple and Expert views, focus explanations with Ctrl+E, glossary with Ctrl+G,
  easier question editing, and plain-language result explanations.
- Interactive cost confirmations, cancellation before dispatch, confirmations
  bound to the approved dataset, and consistent safe error messages.
- Existing JSON output, exit codes, piped input, and exported code preserved.

Verification: Ruff and Pyright passed; 470 offline tests passed and one opt-in
live test was skipped. The installed command was tested from a temporary directory, including
TUI navigation and cancellation. No live API calls were made for this milestone.
See [Task B](docs/archive/TASK_B.md).

The first public source baseline is based on the preserved 0.5.0 source snapshot.
Publication preparation removes personal paths and local run identifiers from
documentation; it does not reconstruct or backdate earlier source history.

## Earlier milestones — documentation only

The checkpoints below were recorded during development. Their original source
snapshots were not retained in Git. Cached editable-install metadata confirms
their version labels, but those artifacts contain no application source. They
cannot support faithful source releases or tags. The checkpoint documents are
historical records; their commands may differ from the current interface.

### 0.4.1 — 2026-09-20

- Repaired coach installation, provider-specific configuration, SDK error
  messages, deadlines, and recovery from failed TUI requests.
- Added `jev doctor --coach` diagnostics and tested both provider connections
  with real requests using synthetic input.
- Verification recorded 390 offline tests passing, one live test skipped, clean
  Ruff/Pyright checks, and installed-command checks outside the source directory.
  See [Task A](docs/archive/TASK_A.md) for the separate live-call evidence and limitations.

### 0.4.0 — 2026-09-20

- Added Python, LangChain, and Pydantic AI exports; an authenticated local HTTP
  server; lessons 6–10; retention and cleanup; and synthetic portfolio images.
- Verification recorded 250 offline tests passing, one live test skipped, clean
  Ruff/Pyright checks, and installed-command checks from a temporary directory.
  See [Phase 4](docs/archive/PHASE4.md).

### 0.3.0 — 2026-09-20

- Added dataset import, evaluation reports, calibration, threshold tuning,
  resumable batch processing, and side-by-side comparison.
- Verification recorded 148 offline tests passing, one live test skipped, clean
  Ruff/Pyright checks, and installed-command checks from a temporary directory.
  See [Phase 3](docs/archive/PHASE3.md).

### 0.2.0 — 2026-09-20

- Added the first five lessons, seven example patterns, synthetic practice
  datasets, and optional advisory coach adapters.
- Verification recorded 65 offline tests passing, one live test skipped, clean
  Ruff/Pyright checks, and installed-command checks from a temporary directory. Separate real
  TypeSafe calls used synthetic examples; coach authentication was not tested in
  this milestone. See [Phase 2](docs/archive/PHASE2.md).

### 0.1.0 — 2026-09-20

- Added the global command, configuration and diagnostics, YAML templates,
  template editor, playground, visual results, and SQLite history.
- Verification recorded 42 offline tests passing, one live test skipped, clean
  Ruff/Pyright checks, and installed-command checks from a temporary directory. No real TypeSafe
  inference was made for this milestone. See [Phase 1](docs/archive/PHASE1.md).
