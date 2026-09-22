# Follow-up Phase 2: spending and explained forms

> Historical checkpoint: commands and measured results below describe that release.
> Since 0.8.0, the app is JevLab and the command is `jevlab`. Use the
> [current guide](GUIDE.md) for installation, commands, and existing-data compatibility.

Completed: 2026-09-21. Application version: 0.7.0.

## Behavior

- A single Jev request, including a history rerun, starts immediately in the
  CLI and Playground. Results retain token usage, latency and usage-priced cost.
  TypeSafe returns usage rather than an invoiced dollar amount; this cost still
  uses published rates and cannot account for unknown retry charges.
- Interactive batch and evaluation jobs still show an estimate and ask before
  starting, even for one row. Each has its own remembered opt-out. In the TUI,
  tick **Don't ask again** and accept; cancelling never saves that preference.
  The CLI offers a remember-choice question after acceptance. `--yes` applies
  only to that command. Settings can restore either prompt.
- JSON, piped and unattended calls keep their original envelope, exit codes,
  stdout/stderr separation and budget rules. Interactive preferences do not
  waive the scripting budget gate. Comparisons, coaches, lesson grading and
  the server keep their separate spending confirmations.
- Editable fields now have plain labels, descriptions and examples. Inline
  errors explain local problems before submission. Simple mode shows help;
  Expert mode offers expandable help. Ctrl+E keeps longer explanations and
  uses the actual field description in generic name/file dialogs.
- Coverage includes the template/question editors, Playground, Settings,
  batch/eval setup, comparisons, thresholds, lessons, coach, export, YAML/file
  dialogs and search filters. Free-text searches need no artificial restrictions;
  checkboxes and fixed choices need no separate invalid-value messages.
- The guide, quickstart and README describe the new behavior. The shared Field
  component lives in the TUI; core has no UI imports. Generated harness code is
  unchanged.

## Try it

```sh
jev --version
jev
jev guide
jev batch
jev eval
```

In the app, open a saved design and choose **Get answers** to make one paid
request immediately. In the builder, enter `jev` as the model to see the local
correction to `jev-latest`; no request is sent. Ctrl+E explains the focused field.

Restore either remembered prompt with:

```sh
jev config --set confirm_batch_cost=true
jev config --set confirm_eval_cost=true
```

## Verification

`make lint` passed: Ruff checks and formatting, and zero Pyright errors.
`make test` passed: **623 tests passed, one opt-in live test skipped**.
Regression tests use mocked providers and block outbound network calls.
They check the specific visible
feedback, cancellation without dispatch or preference changes, independent
spending scopes, one-off authorization, unchanged machine budget checks,
saved usage/cost, edit-time validation and keyboard navigation.

The globally installed editable command was tested from a fresh `zsh -f` outside
the checkout, with a temporary environment-only profile and all provider keys
unset. Version, demo, guide, doctor and history JSON passed, as did both preference
toggles. A real terminal session opened the builder, Ctrl+E help, returned with
Escape, and exited cleanly with Ctrl+Q.

Builder, Playground and Settings were checked in both modes at 80×24 and 120×42.
Every active control was reachable with Tab and scrolled into view. Ctrl+E
returned to the same draft. The guide's template-building exercise was followed
with keyboard navigation; the intended edits were saved and other questions,
state and thresholds were preserved.

A clean wheel installed on Python 3.12 passed version, keyless demo, doctor,
guide and history checks outside the source tree. Wheel and source archive
inventories contain the application, documentation and synthetic teaching data;
they contain no private profile, database, key material, personal paths or
editable-install pointer. The archive uses anonymous owner metadata. The
packaged guide matches the Markdown source. Historical and current-source
privacy checks found synthetic credential fixtures only; their values are not
live secrets. GitHub CI must pass for the exact commit before its release tag.

No live TypeSafe, Anthropic or OpenAI call was made for this phase. The preceding
[Phase 1](FOLLOWUP_PHASE1.md) separately records live error reproduction and
successful verification; these are not new Phase 2 calls.

## Publication and remaining phases

The scheduled publisher was deleted. The owner approved publishing verified
checkpoints as work proceeds and starting a clean `jevlab` repository with one
initial commit authored as Javi. The old public repository remains unchanged,
including its existing author metadata. The new history does not inherit it.

The command is still `jev`, the distribution is still `jev-workbench`, and local
state stays in `~/.jev/`. The audit, command/data rename, MIT/release-readiness
work and possible Linux support remain in the later requested phases. No PyPI
publication was performed. Local validation cannot prove that an arbitrary
provider model exists or that an account has billing access; those failures
continue through the detailed error pipeline implemented in Phase 1.
