# Project workflow

Keep your decision design beside your code. Reproduce a case, edit one question,
compare against a baseline, then export. Nothing needs importing into the personal
template catalog. [Documentation](README.md) · [Command reference](REFERENCE.md)

## Work with YAML directly

After installing from the repository, start in its included example:

```sh
cd examples/ticket-routing
jevlab templates edit ./decision.yaml
```

The editor saves back to that file. Ctrl+S saves; Ctrl+Q exits. External edits
or deletion block saving so another editor's changes are not silently overwritten.
Form saves normalize YAML formatting and comments. Use a text editor if preserving
comments matters.

```sh
jevlab run ./decision.yaml --text 'I cannot upload my document.' --json
jevlab export ./decision.yaml --output decision.py
```

**`run` makes one billable Jev call immediately.** Export makes no API call.
The generated module uses the official SDK and keeps decisions separate from your
application's actions. It preserves the saved thresholds.

Bare names such as `support-triage` still mean the personal catalog. A `.yaml` or
`.yml` suffix, or an explicit path, means a project file. Paths for templates,
input states, datasets and outputs all resolve from your current directory.
Renaming a design inside the form does not rename its project file. History and
run snapshots still live in your application profile. Server routes continue to
accept catalog names only.

## Save a baseline

A baseline is a portable JSON snapshot of an evaluation. Inspect cost first:

```sh
jevlab eval plan ./decision.yaml ./cases.jsonl
```

The following command makes three live calls. `--yes` authorizes their estimated
cost. The bundled cases are synthetic examples, not an accuracy benchmark.

```sh
jevlab eval run ./decision.yaml ./cases.jsonl --yes --json > baseline-run.json
```

Capture the saved job ID and freeze its evidence:

```sh
baseline_job=$(python3 -c 'import json; print(json.load(open("baseline-run.json"))["data"]["id"])')
jevlab eval baseline "$baseline_job" --output baseline.json
```

The baseline contains the complete design, fingerprints, labels, predictions,
confidence, metrics, resolved model IDs, and run IDs. It excludes evaluated input
states and local source paths. Template examples and free text are still included;
review them and labels before sharing. Hashes are not anonymization or signatures.
The original dataset must still exist unchanged when you create a snapshot.
The saved snapshot then survives history cleanup and source-file removal.
Existing output files are never overwritten implicitly.

## Compare a changed design

Edit the wording, preserving question IDs and label meanings. Then evaluate the
same cases again. This starts another three billable calls:

```sh
jevlab eval run ./decision.yaml ./cases.jsonl --yes --json > candidate-run.json
candidate_job=$(python3 -c 'import json; print(json.load(open("candidate-run.json"))["data"]["id"])')
jevlab eval compare baseline.json "$candidate_job" --min-accuracy 0.90
```

The comparison itself is offline. It lists improved, regressed, and changed cases.
A regression is a previously correct case/question pair that is now wrong or
unanswered. An improvement elsewhere does not cancel it. By default **zero
regressions are allowed**; `--max-regressions 1` changes that explicit limit.
`--min-accuracy` applies to every question, including failed cases in the denominator.
Incomplete evaluations fail the check even if relaxed quality limits would pass.

Case IDs, input states, and labels must match. File location and row order may
change. Question wording and requested models may change; Choice label sets and
Score level counts must stay compatible. Matching label names cannot prove that
their meaning stayed the same: review that yourself.

Save the candidate to compare on another machine without keys or a local database:

```sh
jevlab eval baseline "$candidate_job" --output candidate.json
jevlab eval compare baseline.json candidate.json --json --output comparison.json
```

`--json` keeps the version-1 envelope. Exit **0** means the check passed; **5** means
quality limits failed, with the full report under `data` and a specific `error`.
Input errors use exit 2. Existing commands retain their previous exit codes.
Use this offline comparison in CI after explicitly producing the evaluation
artifacts; running a live eval in CI is a separate, billable choice.

## Tune first, verify on separate cases

The threshold tuner reports performance on data already seen. It does not prove
that the threshold will work on new cases. Choose a gate using the tuning job:

```sh
jevlab eval tune "$candidate_job" route --threshold 0.85 --save --template ./decision.yaml
jevlab eval freeze "$candidate_job" --template ./decision.yaml --output policy.json
```

These commands make no API call. `freeze` records the chosen thresholds and tuning
evidence. It permits only threshold changes from that evaluated design. Run the
unchanged template on a separate holdout **after** freezing. This makes three calls:

```sh
jevlab eval run ./decision.yaml ./holdout.jsonl --yes --json > holdout-run.json
holdout_job=$(python3 -c 'import json; print(json.load(open("holdout-run.json"))["data"]["id"])')
jevlab eval verify policy.json "$holdout_job" --min-accuracy 0.95 --min-coverage 0.20 --output verification.json
```

Verification is offline. It checks the frozen design/thresholds, chronological
order, the actual resolved model, and exact state overlap. Renaming a tuning case
or changing its label does not make it a holdout. Model aliases can drift; use a
pinned version for tuning and verification.

The report distinguishes held-out verification from tuning, retains provenance,
and checks accuracy among automated cases plus coverage. Each configured gate
must pass; questions without a gate remain review-only. No automated cases means
insufficient evidence and a failed accuracy check, even if minimum coverage is 0.
Default automated accuracy is 0.95; choose requirements appropriate to your task.

Do not keep tuning against the holdout after seeing its results. Exact hashes
cannot detect semantic duplicates or prove independence, and a handful of examples
cannot establish production reliability. The tool makes no statistical guarantee.

## Scope

The new baseline and policy commands are CLI workflows; the TUI retains its
single-case comparison, evaluation viewer and threshold slider. TUI evaluation
setup and threshold saving use the personal catalog; for project files, use the
explicit CLI paths above. No extra service,
tracing backend, optimizer, or project configuration file is required.

The workflow has automated integration tests. Demand from outside developers is
still unverified. Before expanding it, try it with real project cases and record
where it helps or gets in the way.
