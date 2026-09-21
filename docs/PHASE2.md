# Phase 2 checkpoint

Completed 2026-09-20. Phase 3 has not started.

> Historical checkpoint: features, commands, and verification below describe this
> milestone as recorded. See the [current README](../README.md) for current usage.

## Delivered

- Five interactive lessons: System One, primitive choice, state design, questions
  and criteria, and multiple independent questions. Drafts open in the existing
  builder; top-level field selection changes the actual state sent to Jev.
- Real-API lesson grading with disclosed per-primitive scoring, cost preflight,
  per-case run inspection, partial-failure handling, and saved progress/attempts.
- Seven reusable patterns and 29 original synthetic labeled cases. Library designs
  can be opened, run, or forked, with when-to-use notes and official source links.
- Optional Anthropic/OpenAI coach: intent-to-template proposals, critique, result
  hypotheses, and lesson feedback. Proposals validate before review and cannot
  save or execute themselves. Provider/model selection is explicit in config.
- TUI panels and CLI commands (`learn`, `library`, `coach`) with JSON modes;
  shared CLI output/error handling; additive SQLite schema migration 2.

## TypeSafe key and live verification

A test configuration retrieved a TypeSafe credential from **macOS Keychain**.
Authenticated model discovery and a real three-primitive inference succeeded.
The synthetic support example reported 512 input tokens and an estimated
$0.000021504 at the recorded rate. These results describe that verification,
not the availability of any current account.

A separate five-case lesson smoke test completed through the real SDK in a temporary
profile: 1,899 input tokens and estimated cost $0.000079758. All five reference-design
labels matched. This only verifies the integration on those visible synthetic cases;
it is not evidence of general model accuracy. The preflight estimate was lower than
reported usage, illustrating that character estimates omit server overhead and are
not spending caps. No user lesson progress was credited by this smoke test.

The six inference checks together had a published-rate estimate of $0.000101262.
No actual invoice/billing total was retrieved. Key values were never displayed or
copied to app files.

## Automated verification

- 65 offline tests pass; one separately opt-in live pytest test is skipped.
- Ruff checks/format verification and Pyright pass.
- Tests exercise actual TypeSafe, Anthropic, and OpenAI SDKs through mocked HTTP
  transports, grading/projection, cost gates, safe failures, interrupted attempts,
  migration preserving an existing run, JSON commands, and TUI workflows.
- Rendered lesson, curriculum, library, and coach screens were visually inspected.
- Editable global installation upgraded to `jev 0.2.0`. From a temporary directory, the installed
  command reported five lessons, seven patterns, and successful credential lookup;
  `jev learn` launched in a real terminal and exited cleanly with Ctrl+Q.
  Existing run history was preserved without creating lesson attempts.
- Coach authentication was not tested in this milestone's configuration.

## Try it

```sh
jev learn
jev library
jev doctor --json
```

Start lesson 1, select **Edit draft**, improve the question, save with Ctrl+S, return
with Esc, then choose **Grade with Jev**. Inspect individual cases from the report.

CLI equivalent:

```sh
jev learn start 1 --name routing-practice --json
jev templates edit routing-practice
jev learn plan 1 --template routing-practice --json
jev learn grade 1 --template routing-practice --json
jev learn progress --json
```

Optional coach setup, choosing a provider and an API model available to your account:

```sh
cd ~/jev
make install COACH=anthropic  # or openai / both
jev config --provider anthropic
jev coach
```

## Limits and choices

The coach is disabled by default. Its adapters pass mocked contract tests, but live
provider access and advice quality remain unverified until configured. Advice is
not a reasoning trace, calibration result, or replacement for Jev. Coach costs are
unknown; token usage is reported. General coach panels are not persisted, while
lesson feedback is stored with its attempt.

Lessons use 4–5 visible synthetic cases each, fixed grading labels, a disclosed 80%
exercise threshold, and top-level field filtering. They are teaching exercises.
Full dataset import, evals, calibration, threshold tuning, batch, and compare remain
Phase 3. Export/server, the remaining lessons, and retention/cleanup remain Phase 4.

See decisions D17–D24 in [DECISIONS.md](DECISIONS.md). No additional confirmation is
needed to use Phase 2; choosing a coach is optional setup. Stop here for the user's
Phase 3 go-ahead.
