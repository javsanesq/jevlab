# Phase 3 checkpoint

Completed 2026-09-20. Phase 4 has not started.

> Historical checkpoint: features, commands, and verification below describe this
> milestone as recorded. See the [current README](../README.md) for current usage.

## Delivered

- CSV/JSONL dataset validation and import, with source-path and fingerprint
  registration in the existing SQLite database. Library designs can export
  canonical labeled datasets directly from CLI or TUI.
- Eval runs with per-question accuracy, ten-bin reliability charts, Choice
  confusion matrices, inspectable worst misses, Brier scores, Score MAE,
  returned model counts, token/cost totals, and latency statistics.
- Keyboard threshold sliders with live coverage and automated accuracy, including
  separate Noul no/yes gates. No API call is needed to tune saved results. Saving
  changes only adjusted gates and refuses a changed template design.
- Bounded batch/eval concurrency, rate-limited call starts, progress, cost
  preflight, cancellation, SQLite checkpoints, atomic JSONL output, safe resume,
  and explicit retries for failed or uncertain requests.
- Side-by-side comparison of two templates or model versions over identical
  state, with both complete results and probability/value differences.
- TUI entry points, command-palette actions, CLI commands with JSON output,
  documentation, and a three-case synthetic fixture at
  `examples/support-eval.jsonl`.

## Try it

From any directory:

```sh
jev eval
jev batch
jev compare
```

In `jev eval`, select `support-triage` and enter this dataset path:

```text
~/jev/examples/support-eval.jsonl
```

Choose **Import / preview**, then **Run Jev** to make three real calls. Open the
report's **Tune thresholds** view; Tab to a slider and use Left/Right to change
it. The sliders work from stored answers and do not call the API.

CLI equivalent:

```sh
cd ~/jev
jev eval plan support-triage examples/support-eval.jsonl --json
jev eval run support-triage examples/support-eval.jsonl --json
jev eval --json
jev eval show JOB_ID --json
jev eval tune JOB_ID route --threshold 0.90 --json
```

The planning/inspection/tuning commands above are local. `eval run`, `batch`,
`compare`, and retry actions make paid Jev calls. Use `--save` on `eval tune` only
when you want to persist the previewed gate. The bundled fixture illustrates the
workflow; it is not evidence of model performance.

See the [README](../README.md#evaluate-tune-batch-and-compare) for dataset schemas,
export, cost controls, concurrency, resume, and comparison commands.

## Verification

- Editable global installation upgraded to `jev 0.3.0`. From a temporary directory, the installed
  CLI returned valid JSON for the new commands, validated the example preflight,
  and launched `jev eval` in a real terminal, exiting cleanly with Ctrl+Q.
- `jev doctor` reported a healthy schema-3 database, no PATH collision, and
  successful credential lookup in the tested configuration. Migration preserved
  an existing run without creating evals or lesson progress.
- 148 offline tests pass; one opt-in live pytest test is skipped.
- Ruff checks/format verification and Pyright pass.
- Tests use the actual official SDK through mocked HTTP transports. Network
  access is blocked throughout the offline suite. No additional paid API calls
  were made for Phase 3.
- Coverage includes calibration denominators and boundary probabilities, Score
  grading, asymmetric Noul routing, cost gates before calls, bounded concurrency,
  rate limiting, cancellation, cross-process locking, output ownership, safe
  retries, edited-then-restored datasets, and migration preserving runs and lessons.
- TUI workflows cover import/preview/run, worst-miss inspection, threshold saving
  without reverting other gates, missing-template resume, budget dialogs,
  comparison, and library dataset export.
- Eval, batch setup, threshold, and comparison screens were visually inspected;
  the comparison was checked to occupy two actual terminal columns.

## Decisions and limits

- Inputs are limited to 10,000 rows, 100 MiB/file, 1 MiB/row; split larger datasets.
  Sources remain in place. Run inputs/responses are still saved as history.
- Four workers and two logical call starts/second are defaults. SDK retries may
  add traffic, and there is no token-throughput limiter.
- Cost previews are approximate, not hard spending caps. Unknown or above-limit
  estimates require explicit authorization; machine mode uses `--yes`.
- SQLite checkpoints every row. Output is an atomic snapshot at completion or
  graceful cancellation; resume can rebuild it without repeating successes.
- Uncertain remote outcomes require explicit retry and may be billed twice.
- Accuracy includes all labeled rows, while calibration excludes missing answers.
  Tuning is measured on the same dataset; use independent validation/holdout data
  and pinned model versions before relying on an automation threshold.
- Comparisons persist their two runs in history; they are not resumable jobs.
- Automatic retention, export modules, the local server, remaining lessons, and
  portfolio media remain Phase 4.

Decisions D25–D33 are recorded in [DECISIONS.md](DECISIONS.md). The defaults above
are adjustable choices; no additional approval is needed to use Phase 3. Stop at
this checkpoint and wait for the user's Phase 4 go-ahead.
