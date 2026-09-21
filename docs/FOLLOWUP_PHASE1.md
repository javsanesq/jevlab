# Jevlab follow-up: Phase 1 — run failure and diagnostics

Completed: 2026-09-21. Local application version: 0.6.1. Not published.

## Diagnosis before changes

The saved failure contained the requested model `jev`, the valid starter
questions, and a small JSON state. Its error retained a request ID, but the
top-level run request ID and response body were empty. Both the installed CLI
and the actual Textual Playground reproduced the rejection with live requests.
An observer at the SDK boundary captured HTTP 400 and this provider body:

```json
{"detail":{"error_type":"api_usage_error","message":"Unknown model: jev"}}
```

The current [official models page](https://docs.typesafe.ai/models.md) documents
`jev-latest`, `jev-preview`, and pinned versions such as `jev-1.13.0`; `jev` is
not an alias. The [API reference](https://docs.typesafe.ai/api.md) confirms that
the supplied JSON object and question shapes are supported. A successful live
request with the same questions and state, changing only the model to
`jev-latest`, confirmed the cause. Credentials, quota, criteria, and context size
were not responsible for this rejection.

The application discarded the evidence in several places: status-only exception
translation replaced provider messages; unsuccessful response bodies were
deliberately omitted from storage; failed request IDs were not copied to the run;
history reconstructed only three error fields; and F2 replayed the same brief
notification rather than retaining technical details.

## Changes

- Correct the affected current template and default model to `jev-latest`, leaving
  other settings, questions, thresholds, keys, and historical revisions intact.
- Validate model identifiers and show model-field corrections while editing.
  Permit future well-formed IDs instead of maintaining a fixed model allowlist.
- Preserve provider messages and credential-redacted diagnostic bodies at the SDK
  boundary. Failed runs retain status, request ID, and available technical detail.
- Carry typed errors through CLI, TUI, history, and shared job paths. F2 and verbose
  CLI output expose the saved detail without making another provider request.
- Keep old invalid-model snapshots readable for inspection, but revalidate before
  sending any rerun. Preserve specific local validation failures rather than
  replacing them with a generic form error.
- Pause the recurring publisher. No repository push, release, or package upload
  belongs to this phase; publication requires the later privacy checkpoint.

## Try it

Run `jev`, open the repaired template, and choose **Get answers**. The existing
spend prompt remains until the separately reviewed Phase 2. The result should
show answers, tokens, elapsed time, and the usage-based cost estimate.

In the template editor, enter `jev` in Model. The inline message explains the
valid identifier and prevents saving it. Change it to `jev-latest` to clear the
error. No API request is needed for this check.

For a failed request, press F2. History can reopen a saved failure and its details.
The command form is `jev --verbose history show RUN_ID`, replacing `RUN_ID` with
the ID of that saved failure. Reading history is free.

## Limits

Old response bodies discarded by earlier versions cannot be reconstructed. The
original saved failure remains unchanged. New validation prevents obvious model
entry mistakes, but only the provider can establish current access to an otherwise
well-formed model ID. Provider diagnostics may echo submitted state; credentials
are redacted, but the user should review other private content before sharing it.

## Verification

- `make lint`: Ruff checks and formatting passed; Pyright reported zero errors
  and warnings. `make test`: **571 passed, 1 skipped** in 93.47 seconds. The skipped
  test is opt-in and billable; ordinary tests block outbound sockets.
- The regression suite exercises actual SDK HTTP mocks for malformed questions,
  unknown models, authentication, access, quota, context limits, rate limiting,
  service failures, malformed successful responses, network errors, timeouts, and
  unknown exceptions. Assertions check the explanation itself, diagnostic storage,
  normal/JSON/verbose CLI output, and representative TUI/F2/history flows.
- Redaction tests cover known credentials, encoded variants, nested credential
  fields, JSON and text bodies, and large bodies without losing the explanation.
  Semantic response validation identifies the exact answer or usage field.
- `make install` installed editable 0.6.1 and preserved the optional coach
  packages. A fresh `zsh -f` from a temporary directory passed version, demo/guide/doctor JSON,
  rejected config updates without changing the file, and rejected a malformed
  model before credential lookup or run creation. These checks used a temporary
  environment-only profile with no real keys.
- A failed request created through an installed SDK with mocked HTTP was reopened
  by the installed verbose history command. Its reason, HTTP 422, request ID, and
  full redacted body survived. No key value reached stdout, stderr, or storage.
- **Four live TypeSafe requests** were made in this phase: two pre-fix failures
  reproduced through CLI and Textual Playground, followed by successful CLI and
  Playground calls using the corrected model and the same supplied example. The
  TUI checks exercised the installed application's real controls and workers with
  Textual's headless driver; the requests themselves were live. Existing Keychain
  credentials were used without being printed, copied, or changed.
- Both successful calls resolved to `jev-1.13.0`, selected billing, and returned
  refund probability 0.97. Impact confidence was below the saved 0.85 cutoff, so
  that answer required review. CLI latency was 819 ms and TUI latency was 857 ms;
  each used 517 input and 69 output tokens. The published-rate estimate was
  $0.000021714 per successful call. Failure costs were unknown because no usage
  was returned. These examples are verification, not a performance benchmark.
- No coach calls, Git pushes, GitHub releases, or package uploads were performed.
  The recurring publisher was verified paused. The original failed run still
  contains its exact original model and missing-body fields; it was not rewritten.
