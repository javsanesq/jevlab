# Phase 4 checkpoint

> Historical checkpoint: commands and measured results below describe that release.
> Since 0.8.0, the app is JevLab and the command is `jevlab`. Use the
> [current guide](../GUIDE.md) for installation, commands, and existing-data compatibility.

Completed 2026-09-20. This completes all four requested phases.

> Historical checkpoint: features, commands, and verification below describe this
> milestone as recorded. See the [current README](../../README.md) for current usage.

## Delivered

- Standalone typed Python exports, plus verified LangChain Runnable and Pydantic AI
  Tool variants. Saved templates, models, answer validation, and thresholds survive
  export. Both synchronous and asynchronous use are supported.
- Authenticated loopback HTTP API using saved templates, the existing direct SDK
  call path, cost confirmation, bounded concurrency, local history, and routing.
- Ten lessons, including confidence/calibration, human-review routing, agent loops,
  guardrails, and RAG. Lessons 6/7 include local calibration and threshold analyses.
- Active history retention, auditable `jev clean` previews/application, a TUI cleanup
  screen, schema-4 migration, preserved learning achievements, and disk diagnostics.
- Export preview/save screen, command-palette entries, updated setup documentation,
  framework integration guide, and six reproducible portfolio screenshots.

## Try it

```sh
jev --version
jev                       # Export and Cleanup on Home or Ctrl+P
jev learn                 # Ten lessons; grading makes real Jev calls
jev export support-triage --lang python --output ~/Downloads/decision.py
jev export support-triage --lang langchain --json
jev export support-triage --lang pydantic-ai --json
jev clean --dry-run --json
jev doctor --json
```

Use a new `.py` output path; export refuses to overwrite files. Generated code
requires `typesafe-sdk==0.7.0` and a `TYPESAFE_API_KEY` in the receiving process.
Framework installation examples are in [INTEGRATIONS.md](../INTEGRATIONS.md).

Start the local API:

```sh
export JEV_SERVER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
jev serve --check --json
jev serve
```

The process binds only to `127.0.0.1:8766`; Ctrl+C stops it. Set the same separate
local token in your client. Health checks and authenticated template listings are
free local operations. `POST /templates/<name>/run` makes a real billable call;
see the [README client example](../REFERENCE.md#local-http-api).

Reproduce the synthetic portfolio images without keys or calls:

```sh
cd ~/jev
uv run python scripts/capture_portfolio.py
```

## Verification

- **250 offline tests pass; one opt-in live test is skipped.** Ruff checks,
  formatting verification, and Pyright pass. Offline tests block outbound network
  connections and isolate profiles. No paid verification requests were made.
- Editable global installation is `jev 0.4.0`. From a temporary directory, the installed command
  exports standalone code, lists ten lessons, previews cleanup, and checks server
  configuration. The full-screen TUI launches and exits cleanly with Ctrl+Q.
- An actual loopback listener passed health/authenticated listing checks, rejected
  unauthenticated and browser-Origin requests, and stopped with Ctrl+C. This smoke
  check made no inference requests and used an isolated temporary profile/token.
- Migration to schema 4 preserved an existing run. Doctor reported a healthy
  database, no PATH collision, and successful credential lookup in the tested
  configuration. Cleanup preview correctly found no eligible records.
- Exports execute through the real sync/async SDK with mocked HTTP, including actual
  LangChain Runnable and Pydantic AI Agent/Tool contract tests. Server route tests
  mock only inference transport; no upstream API call is made.
- Retention tests cover dependency groups, dry-run immutability, WAL compaction,
  active locks, migration, preserved achievements, and maintenance failure isolation.
  A comparison regression verifies that cleanup cannot separate an unfinished pair.
- Portfolio images, Export, and Cleanup were visually inspected. The capture script
  blocks network and Keychain access and removes its temporary profile on exit.

## Decisions and limits

- Exports contain template examples/notes, so review before publishing. They do not
  contain credentials or historical states, read the workbench Keychain, or enforce
  external business actions. Framework variants preserve the exact SDK call.
- The server is for local backend clients. It rejects browser Origins, uses a
  separate token, has no durable request queue, and is not a public deployment.
  Unknown/above-limit cost estimates require explicit request authorization;
  estimates are not spending caps. Disconnected requests may still finish upstream.
- Retention groups related history by newest activity. Active/pending work and the
  current result are protected. Protected data or irreducible metadata can exceed
  the configured byte cap. Filesystem cleanup is limited to SQLite/WAL history;
  templates, datasets, exported files, and configuration remain.
- Learning achievements survive expired detailed attempts. Practice datasets are
  small, visible, and synthetic; neither their grades nor the portfolio screenshots
  establish real model performance or deployment readiness.
- No new paid API or coach requests were made during Phase 4 verification.

Decisions D34–D41 are in [DECISIONS.md](../DECISIONS.md). No further decisions require
confirmation; the original four-phase build is complete.
