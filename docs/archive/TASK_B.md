# Task B: approachable interface checkpoint

> Historical checkpoint: commands and measured results below describe that release.
> Since 0.8.0, the app is JevLab and the command is `jevlab`. Use the
> [current guide](../GUIDE.md) for installation, commands, and existing-data compatibility.

Completed: 2026-09-21. Release: 0.5.0. Task C has not started.

> Historical checkpoint: features, commands, and verification below describe this
> milestone as recorded. See the [current README](../../README.md) for current usage.

## What changed

- A first home launch opens a short welcome tour, with optional secure key setup,
  a free recorded demo, and an explained result. Skip, finish, or Esc remembers
  completion; `jev tour` can always repeat it.
- Simple mode is the default. Screens explain their purpose and use plain labels.
  More options reveals advanced controls without changing the saved preference;
  Expert mode reveals them from the start. Hidden values are retained.
- Ctrl+E explains the focused control, including password inputs, without reading
  its value. Ctrl+G opens the searchable glossary. Both work over dialogs and
  restore the previous focus when closed. Help remains available with ? or F1.
- Each result explains the returned answer, probabilities/confidence, and saved
  automate/review rule in ordinary words. The explanation is local and free, and
  cannot change the actual answer or claim to know the model's reasoning.
- The template editor has individual option and description fields in Simple
  mode. Full YAML and structured criteria remain available. Switching question
  types preserves each unfinished draft.
- Interactive paid actions show a cost estimate and require explicit consent,
  with Cancel selected first. This covers runs/reruns, coach requests, lessons,
  comparisons, jobs and resumed jobs, and starting the paid-call local server.
  Optional lesson feedback asks separately after saving the grade.
- Errors share the same what/why/next shape. F2 opens safe TUI details; global
  `--verbose` adds safe CLI metadata. Unexpected TUI failures no longer render
  Textual tracebacks containing local variables. Failed workers restore controls.
- JSON envelopes, exit codes, stdout/stderr behavior, piped-input behavior,
  existing automated budget gates, and generated project code are preserved.

## Try it

The global editable installation works from any directory:

```sh
jev tour
jev demo
jev glossary
jev
```

The first three commands require no paid request. Inside the app, use Tab to
focus a control, Ctrl+E to understand it, Ctrl+G for definitions, Esc to return,
and Ctrl+Q to leave. Ctrl+P opens the command palette. The home screen includes
Recorded demo and Welcome tour buttons.

Switch the default view without touching keys or provider selection:

```sh
jev config --set ui_mode=expert --json
jev config --set ui_mode=simple --json
```

To test the spending gate, open a template, choose Get answers (Ctrl+R), read the
estimate, and choose Cancel. No request is sent. Choosing the affirmative button
instead makes a real paid request using your configured account.

The new commands also support scripts:

```sh
jev demo --json
jev tour --json
jev glossary confidence --json
jev doctor --json
```

## Limits and deliberate choices

- The demo is an authored illustrative recording bundled on disk, prominently
  labeled as such. It is not a captured live response, a benchmark, or an actual
  decision about your data. It does not write to run history.
- Estimates use dated prices and approximate input sizes. They are not caps;
  provider retries and account-specific rates can change the final bill. Unknown
  models show unknown pricing and still require interactive consent.
- JSON/piped-input/unattended commands keep their previous behavior because
  scripts must not acquire prompts or different output contracts. Use the
  existing budget controls and explicit authorization flags for automation.
- Starting `jev serve` confirms that future client requests may spend money.
  It cannot know their sizes in advance. Existing server authentication and
  request budget gates still apply; there is no terminal prompt per HTTP call.
- Simple mode reduces the initial controls, not the concepts needed for advanced
  evals, exports, or server use. Full beginner instructions belong to Task C.
- Help and result explanations are rule-based descriptions of available facts,
  not a coach response or a model reasoning trace. The coach remains optional.
- Existing profiles receive Simple mode and the welcome tour if those new settings
  are absent. Task B verification used isolated profiles and did not rewrite
  existing profile settings or credentials.

## Verification

`make lint` passed Ruff checks and formatting for all 100 Python files, and
Pyright reported zero errors or warnings. `make test` passed **470 tests**, with
**1 skipped** opt-in live test, in 78.38 seconds. Offline tests block outgoing
socket connections. Coverage includes help/focus recovery, first-run persistence,
demo isolation, Simple/Expert preservation, friendly criteria editing, every
interactive cancellation path, consent bound to dataset plans, safe errors, and
unchanged machine output and exit codes. Existing core/UI dependency and export
tests continue to pass.

An independent 80×24 Textual review exercised the complete tour, key settings
without submitting a key, password-field explanations, glossary, recorded demo,
question editing, cancellation, and result scrolling. Captured screens were
rendered and inspected. Cancel remained visible and selected first; result actions
stayed visible while the answer content scrolled. These flows made zero inference
calls and created zero history entries in an isolated temporary profile.

`make install` upgraded the global editable tool from 0.4.1 to 0.5.0, warned about
the existing executable, and preserved both coach SDKs: Anthropic 1.7.0 and OpenAI
3.16.2. `uv pip check` confirmed all 40 installed packages are compatible.

The installed `~/.local/bin/jev` was then tested from
a temporary directory, using temporary environment-only profiles with all provider key variables
removed:

- `--version` returned `jev 0.5.0`.
- `demo --json`, redirected plain `demo`, `tour --json`, and
  `glossary confidence --json` returned their expected explanations and recording
  labels without credentials.
- Config changes to Expert and back to Simple succeeded. Ordinary `doctor --json`
  and history succeeded. Offline coach doctor returned the expected exit 3 and
  per-provider missing-key reports for this deliberately empty profile.
- Piped `run support-triage --state - --format text` returned one versioned JSON
  `missing_key` error, exit 3, with no prompt or stderr output.
- `jev --verbose glossary unknown-term` returned exit 2, empty stdout, and the
  what/why/next error plus safe error code on stderr, without a traceback.
- A real terminal launch of `jev` showed the first-run tour. Ctrl+E and Ctrl+G
  opened help and returned correctly; Esc skipped and persisted completion.
  `jev tour` repeated the tour, `jev demo` displayed the recorded example, and
  Ctrl+Q exited each with code 0.
- A terminal `jev run support-triage --text 'Please refund a duplicate charge.'
  --format text` showed its estimate and asked before dispatch. Answering `n`
  returned exit 2 and stated that no request was sent. This temporary profile's
  history remained empty after all terminal checks.

No unresolved blocker was found in these checks. They do not establish live
provider availability or guarantee an estimate will match an account's bill.

No real TypeSafe, Anthropic, or OpenAI calls were made for Task B. Provider HTTP
responses were mocked in tests; tour/demo verification used bundled synthetic
data. The earlier real calls that repaired both coaches belong to
[Task A](TASK_A.md), not to this verification.
