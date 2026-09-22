# Phase 1 checkpoint

> Historical checkpoint: commands and measured results below describe that release.
> Since 0.8.0, the app is JevLab and the command is `jevlab`. Use the
> [current guide](GUIDE.md) for installation, commands, and existing-data compatibility.

Completed 2026-09-20. This is the historical Phase 1 checkpoint; see
[Phase 2](PHASE2.md) for the later key verification and subsequent feature set.

> Historical checkpoint: features, commands, and verification below describe this
> milestone as recorded. See the [current README](../README.md) for current usage.

## Delivered

- Editable global `jev` command, version 0.1.0, backed by an isolated uv tool environment.
- Dark Textual workbench, template forms and advanced YAML, playground, probability
  visualizations, raw result inspection, searchable history, settings, and help/palette.
- Direct official SDK calls with retries, timeouts, response checks, safe errors,
  cancellation status, usage/cost metadata, and immutable design snapshots.
- Typer commands for setup, diagnostics, template import/edit/validation, runs,
  history, and exact historical reruns; JSON success/error envelopes.
- macOS Keychain credentials with environment fallback; private local files in
  `~/.jev/`; one SQLite database; support-triage starter with all three primitives.
- README, Makefile, research, decisions, locked dependencies, offline tests, and
  a separate opt-in live smoke test.

## Verification performed

| Check | Result |
| --- | --- |
| `make test` | 42 passed, 1 opt-in live test skipped |
| `make lint` | Ruff checks and formatting passed; Pyright: 0 errors/warnings |
| `make install` | Installed editable `jev-workbench==0.1.0` and the `jev` executable |
| Installed CLI from a temporary directory | Version, doctor JSON, and history JSON succeeded |
| Installed TUI from a temporary directory | Opened in a real PTY; Ctrl+Q exited cleanly |
| Visual inspection | Reviewed rendered home, builder, playground, and mock-result screens |
| Native Keychain | Synthetic test item written/read/deleted successfully |
| Doctor | Database healthy, starter valid, no PATH collision; approximately 43 kB app data |
| Live TypeSafe inference | Not performed; no API key configured |

Tests ran without outbound network access. Synthetic runs used temporary profiles.
Keychain verification used a separate temporary service and did not store a real
API credential.

## Try it

```sh
jev config
jev doctor
jev
```

Choose `support-triage`, press Enter, edit the example, and press Ctrl+R. This makes
a real API call after a key has been configured. F1 shows help; Ctrl+P opens actions.

```sh
printf '%s' '{"ticket":{"message":"Please refund the duplicate charge."}}' \
  | jev run support-triage --state - --json
jev history --json
```

## Limits and decisions

Rubrics and thresholds use YAML fields in the basic form editor. Default models
are pinned to `jev-1.13.0`; costs are dated published-rate estimates. Noul displays
only its returned yes-probability. Privacy settings describe the documented
No-Training policy and enterprise ZDR arrangement; they cannot activate ZDR.

Retention settings are recorded but not enforced until Phase 4. Context counts
are approximate. No coach, lessons, evals, batch jobs, compare, export, or server
is implemented yet. There are no additional decisions requiring confirmation
before trying Phase 1; Phase 2 needs the user's next go-ahead.
