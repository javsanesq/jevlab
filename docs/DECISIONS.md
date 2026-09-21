# Decisions

Status: **All four phases approved by the user and implemented** on 2026-09-20.
The phase sections below record decisions at the time; Phase 4 supersedes deferred items.

| ID | Decision | Reason |
| --- | --- | --- |
| D01 | Source at `~/jev`; Python package `jev`, distribution `jev-workbench`, executable `jev`. | Keep source separate from runtime data and avoid changing a shared Python environment. |
| D02 | Textual + Typer + Rich; Pydantic; safe YAML; standard-library SQLite; keyring. | One core with two interfaces, a small dependency set, and native terminal rendering. |
| D03 | Official async TypeSafe SDK in the workbench; direct TypeSafe endpoint explicitly selected. | Responsive TUI and later concurrency without a gateway or an inherited endpoint override. |
| D04 | Default new templates to `jev-1.13.0`; allow aliases explicitly. | Reproducible experiments; always record the returned model version. |
| D05 | Preserve SDK-shaped questions inside a versioned YAML envelope. | Easy reading, diffing, validation, and faithful exports. No executable YAML or template expressions. |
| D06 | Require explicit instructions, 2–255 Choice options, and 2–10 Score levels. | Documented upper limits plus a useful workbench minimum; SDK validation alone is insufficient. |
| D07 | Choice/Score gates use SDK confidence; Noul uses separate no/yes probability cutoffs. | Preserve primitive semantics; never invent Noul confidence. Unconfigured gates route to review. |
| D08 | Use Keychain first, then the matching environment variable; support environment-only use when Keychain is unavailable. | Matches the brief. Do not silently accept a plaintext keyring backend. |
| D09 | Privacy config shows No-Training policy and enterprise ZDR status; no unsupported request toggles. | Live documentation takes precedence over the brief. An account annotation does not activate or verify ZDR. |
| D10 | Keep raw response JSON, immutable template revisions, and nullable usage/cost metadata in SQLite. | Reproduce old runs after editing a template and preserve unfamiliar response fields. |
| D11 | Costs are labeled estimates using a dated price snapshot; latency covers the complete SDK call including retries. | The API does not return billing totals or pure model latency. Unknown usage/price remains unknown. |
| D12 | Dark grayscale theme, cyan accent, thin borders, custom Rich/Textual probability widgets. | Meet the requested feel without adding a charting dependency for simple bars. |
| D13 | Phase 1 stores retention settings and reports footprint; automatic pruning and `jev clean` arrive in Phase 4 as requested. | Keep the MVP boundary explicit. Until then, history does not auto-prune; display this limitation. Defaults reserved: 90 days / 100,000,000 bytes. |
| D14 | Batch/eval confirmation threshold defaults to $1.00, configurable; machine mode requires explicit budget authorization when over the limit or unpriceable. | Cost awareness without blocking cheap individual playground calls. Implement execution in Phase 3. |
| D15 | Coach disabled by default, optional provider extras, user-supplied model name; all decisions remain real Jev calls. | Coaching remains separate from judging, grading, and automation policy. |
| D16 | Python export is authoritative; LangChain and Pydantic AI variants wrap that exact SDK call. | Verified native integrations can alter transport or question/result semantics. |

Phase 1 implementation details:

- Form fields handle metadata and question editing; criteria/thresholds use YAML
  text fields, with a full advanced editor for structured SDK content. Row-based
  rubric controls can follow after using the basic editor.
- Keep the MVP modules flat inside `cli` and `tui`; split them when later screens
  justify it. The dependency direction in D02 is enforced by an offline test.
- JSON commands use a versioned success/error envelope and stable exit codes.
  `--state -` selects machine output automatically.
- Native Keychain write/read/delete was verified with a temporary synthetic
  value. Inference is verified using the actual SDK with mocked HTTP responses;
  live smoke tests remain explicitly opt-in.
- D13–D16 include future-phase decisions. Retention execution, cost-gated batch
  jobs, coach calls, and exports are not present in Phase 1.

Phase 2 decisions:

| ID | Decision | Reason |
| --- | --- | --- |
| D17 | Ship five lessons, seven patterns, and 29 original synthetic labeled cases. | Start with practical state/question design; do not present tiny visible exercises as benchmarks. |
| D18 | Lesson fields are a top-level allowlist applied to actual request state. | Make context filtering testable without adding executable transformations. |
| D19 | Grade Choice exactly, Noul at 0.50, Score by nearest level (halves up), plus observed Score MAE. Completion requires 80%, required evidence, and every call completed. | Deterministic, disclosed teaching rules independent of the coach and automation thresholds. |
| D20 | Persist lesson progress and versioned attempts in additive SQLite migration 2; preserve existing runs. | Track practice and partial failures without a second database or per-run files. |
| D21 | Apply existing cost-confirmation settings to lesson grading now. | A small lesson is already multiple billable calls; unknown estimates require explicit authorization. |
| D22 | Coach adapters use optional official SDKs, configured model IDs, bounded output/time, validated JSON, no tools, and no automatic template save/execution. | Coaching remains advisory and works without changing the Jev decision path. |
| D23 | OpenAI uses Responses with store=False; Anthropic uses Messages. Coach costs stay unknown, usage is reported. | Verified current interfaces; no invented privacy or pricing guarantees. |
| D24 | An enabled coach gives feedback after a lesson; regular coach panels remain in memory, lesson feedback is stored with its attempt. | Keep the footprint small and make the separate data-sharing action visible. Feedback failure never changes a Jev grade. |

No new confirmation is needed to use Phase 2. Selecting a coach provider/model
is optional setup; the workbench and lessons use the already verified TypeSafe key.


Phase 3 decisions:

| ID | Decision | Reason |
| --- | --- | --- |
| D25 | Accept explicit CSV/JSONL row schemas; validate all rows before calls; reference the original file by path and SHA-256. | Catch labeling mistakes without copying large datasets. Initial limits: 10,000 rows, 100 MiB/file, 1 MiB/row. Library dataset export produces the same format. |
| D26 | Add dataset/job/row checkpoints to SQLite migration 3; freeze template revisions and hash individual source rows. | Resume without repeating successes, reject changed input, and preserve previous history and learning progress. |
| D27 | Use bounded asynchronous workers (default four) and evenly spaced logical call starts (default two/second), with the official SDK owning retries. | Keep concurrency bounded without duplicating SDK backoff. Explicitly disclose that retries add traffic and token-rate limiting is not implemented. |
| D28 | Treat uncertain remote completion separately from failed calls; require an explicit retry flag. Use process locks and output ownership checks. | Cancellation/crashes do not imply that a billable request did not complete. Avoid accidental double execution and overwriting unrelated output. |
| D29 | Accuracy and routing coverage include all labeled rows; calibration/Brier/Score MAE use returned answers only. | Do not improve apparent accuracy by dropping failed calls or fabricate probabilities for missing results. |
| D30 | Reliability uses predicted-class probability in ten fixed bins; keep SDK confidence separate. Choice exact match, Score nearest level (halves up) plus MAE, Noul baseline at 0.50. | Match each primitive's actual semantics. Disclose multiclass vs binary Brier scaling and small-sample limitations. |
| D31 | Threshold sliders perform no inference; save only adjusted gates if the saved design still matches the eval. | Support fast routing experiments without silently reverting unrelated gates. Recommend a separate holdout and pinned versions; show returned model counts. |
| D32 | Compare two real calls with identical state and per-side persisted runs. Suppress numeric deltas when types/criteria change. | Make wording and model experiments inspectable without pretending one example measures general quality. |
| D33 | Native Rich/Textual charts and keyboard sliders; no charting dependency. | Keep installation small and the full experience in the terminal. |

Phase 3 was completed before the user's Phase 4 go-ahead.

Phase 4 decisions:

| ID | Decision | Reason |
| --- | --- | --- |
| D34 | Export self-contained typed sync/async Python using SDK 0.7.0; preserve validation, model, questions, and routing gates. | A receiving application needs no workbench installation or database. It supplies its own environment credential; exports contain no saved keys or run history. |
| D35 | LangChain Runnable and Pydantic AI Tool wrap that same function; verify actual framework calls with mock HTTP. | Preserve exact question and result semantics instead of adopting framework-native schema-to-question translations. Tested versions live in INTEGRATIONS.md; framework dependencies remain development-only here. |
| D36 | Local server uses Starlette/Uvicorn, binds only 127.0.0.1, and requires a separate environment bearer token for templates/inference. | Serve local backend projects without exposing a public endpoint, storing another secret, or passing through a model gateway. Reject Origin and unexpected Host headers; suppress request logs. |
| D37 | Server reuses the workbench lifecycle, saved templates, cost gates, and history. Bound uploads, concurrency, and request starts; return busy errors without a queue. | Preserve controls across interfaces and keep resource use predictable. A disconnect cannot establish upstream completion/billing; there is no automatic replay. |
| D38 | Enforce eligible history retention at startup and after runs, throttled to once per minute; expose preview/apply in CLI/TUI. | Implement 90-day/100-MB defaults while keeping inference success independent of maintenance failures. Doctor reports the latest maintenance error. |
| D39 | Prune related jobs/lessons/runs/rerun ancestors atomically by newest activity; protect active records. Preserve aggregate lesson achievements with nullable last-attempt references in schema 4. | Avoid dangling reports or lost learning progress. The size budget covers SQLite/WAL, and can temporarily be exceeded by protected records or irreducible metadata. Source datasets, outputs, templates, and configuration are outside cleanup. |
| D40 | Complete ten lessons using the existing synthetic datasets; lessons 6/7 reuse local calibration and routing calculations. | Teach distinct probability/confidence semantics and real coverage tradeoffs without inventing benchmarks or changing baseline labels based on a threshold. |
| D41 | README embeds reproducible actual Textual SVG captures using synthetic responses, temp profiles, and blocked network/Keychain access. | Show a recruiter the working experience without presenting demonstration values as live API benchmarks or exposing personal data. |

Comparison pairs are linked in history before inference begins, so retention cannot
prune the finished side while its partner is pending. Their brief registration
barrier is included in per-side latency; credential lookup remains excluded.

No additional decisions require confirmation. Phase 4 completes the requested build;
the server's separate token and optional framework packages are documented setup choices.

## Task A — coach repair (2026-09-20)

The user approved implementation after reviewing a read-only diagnosis. Task B
(approachability) and Task C (beginner guide) remain separate approval checkpoints.

| ID | Decision | Reason |
| --- | --- | --- |
| D42 | Keep `anthropic_model` and `openai_model` separately; retain `coach_model` as the selected-provider compatibility mirror. | Switching providers must not reuse an incompatible model. Preserve existing choices, normalize verified display names, and recover recognizable cross-provider values only in legacy configurations. |
| D43 | New editable model defaults are `claude-haiku-4-5-20251001` and `gpt-5.6-luna`; coaching remains disabled by default. | Provide documented economical starting points without assuming account access or restricting future model IDs. This supersedes the user-supplied-only model setup in D15. |
| D44 | `make install` preserves coach extras from the global tool's uv receipt; `COACH=both` installs both explicitly using the editable requirement's `[anthropic,openai]` suffix. | Development dependencies and global tool dependencies are separate. An ordinary upgrade must not remove a working coach. Real installation revealed that `uv tool install` rejects the old `--extra` argument. |
| D45 | `jev doctor --coach` checks both providers independently and requires explicit spend authorization before one synthetic critique per ready provider, with 256 output tokens and zero retries. | Establish usable coaching through the real SDK without sending local designs or creating history. `--offline` is read-only; `--yes` authorizes machine-mode live checks. |
| D46 | Map provider failures to distinct actionable errors; redact known credentials and credential-shaped tokens from errors and successful responses; disable provider HTTP debug logging. | Authentication, permissions, model access, billing, throttling, and transport errors need different fixes. Neither successful advice nor diagnostics may expose a key. |
| D47 | Cover credential lookup and SDK work with the coach deadline; use a bounded daemon-thread bridge for native Keychain access and restore TUI controls on errors or cancellation. | A blocked Keychain prompt must not keep command shutdown waiting indefinitely; unexpected exceptions must not leave the UI stuck requesting advice. |
| D48 | Keep all coaching advisory, without tools, automatic template saves, or access to decision execution; validate every proposal against the existing template schema. | Only the TypeSafe SDK produces decisions. Coach failure cannot replace or alter a Jev result or lesson grade. |
| D49 | Put diagnostic formatting instructions in the trusted system prompt; keep only the synthetic template in probe data. Report provider stop reasons and reject partial advice. | The first live probe showed that brevity instructions embedded in untrusted data conflicted with the coach's own instruction boundary. The corrected probe returned valid live advice from both providers within the same small token cap. |

Doctor cost estimates use a dated standard-rate snapshot and reported usage after
the call; they are not spending caps or account invoices. Normal coach calls still
report usage with unknown cost, as recorded in D23. Existing machine output and
exit codes are preserved; the new doctor mode adds per-provider reports on failure.

## Task B — approachable terminal workflows (2026-09-21)

The user approved Task B separately. Task C remains a later checkpoint.

| ID | Decision | Reason |
| --- | --- | --- |
| D50 | Add `ui_mode` (Simple by default) and `tour_completed` to local settings. Show the skippable tour once on the home launch and allow `jev tour` at any time. | A first launch needs an explanation and a free path before asking someone to spend money. Older configurations receive the same defaults without replacing credentials or provider choices. |
| D51 | Simple mode hides advanced controls until More options; Expert reveals them by default. The choice changes presentation only. | Preserve every capability, all saved values, and professional exports. Temporary More options does not change the saved mode. |
| D52 | Ctrl+E resolves explanations from control identifiers, never field contents; Ctrl+G opens the glossary. Explanations and result summaries are deterministic and free. | Help must work without a key or coach, including on password fields. It must not invent a reasoning trace or change an answer. |
| D53 | Bundle an explicitly authored illustrative recording for `jev demo`; label its provenance on screen and in machine output. | Show all three primitives without a key, billable request, personal data, or history entry. Do not pass synthetic values off as a real inference capture or measured performance. |
| D54 | Confirm every interactive paid action, with Cancel selected first. Keep existing JSON, stdin, unattended CLI, HTTP, and explicit `--yes` authorization behavior. | Protect a person exploring the interface while honoring the non-negotiable scripting contracts. This supersedes D14/D21's threshold-only behavior for interactive use. Estimates remain approximate and unknown prices are disclosed. |
| D55 | Bind interactive dataset approval to the planned input, template, retry policy, eligible rows, and cost; reject changed plans before dispatch. Freeze coach configuration and compared inputs across confirmation. | The request actually sent must match what the person approved. Source files and resumable jobs can change while a dialog is open. The private approval fingerprint does not alter JSON schemas. |
| D56 | Use one human error shape (what happened, why, next step), with F2 or global `--verbose` for safe metadata. Replace Textual's unexpected-error renderer, which includes local variables, with a safe message. | Keep secret values, entered data, and raw tracebacks out of normal screens. Preserve typed machine errors, regression-test unexpected failures, and keep test-time exception propagation. |
| D57 | Provide individual option/description fields for ordinary Choice, Score, and Noul criteria in Simple mode. Retain advanced YAML for structured criteria and preserve unsaved drafts when switching primitive types. | A beginner can build a valid design without learning YAML; switching types must not silently erase unfinished work or flatten richer SDK content. |
| D58 | Explain confidence as distribution concentration, separately from option probability; report Noul's probability of yes without inventing confidence. Use saved routing when explaining automate/review. | Match the current TypeSafe documentation and the response actually returned. A high confidence value is not a promise of correctness, and a routing recommendation does not execute an external action. |

Regular coach cost remains unknown after a call as in D23; preflight estimates
use the same dated standard-rate snapshot as Task A where available. Optional
lesson feedback gets a separate interactive confirmation after the grade is saved.
Declining it keeps the completed grade. Starting the local server requires one
interactive acknowledgment that later client requests can incur charges; its
existing authenticated HTTP and per-request budget contracts remain unchanged.

Verification and the no-live-calls boundary for this task are recorded in
[TASK_B.md](TASK_B.md).

## Task C and public versions (2026-09-21)

| ID | Decision | Reason |
| --- | --- | --- |
| D59 | Write the beginner guide in English, as selected by the user, and keep a shorter quickstart beside it. Explain the installed app before its developer interfaces. | The reader may never have used Terminal; the guide must follow the actual post-Task-B screens. |
| D60 | Keep `docs/GUIDE.md` authoritative; editable installs read it directly and wheel builds include its exact contents. Render it with the existing Rich library for the pager and local HTML. | The guide works offline without another model, dependency, checkout requirement, or duplicate source document. Browser output is static; links are shown as text addresses. |
| D61 | Verify actual commands, keyboard flows, and packaging; make one small live TypeSafe call with synthetic data, separate from network-blocked regression tests. Document account/setup steps that were not repeated. | A guide must describe observed behavior without pretending account creation or a fresh-machine installation was reproduced. |
| D62 | Publish genuine source tags starting with preserved 0.5.0, then 0.6.0. Preserve 0.1.0–0.4.1 as historical milestone notes only. | No Git history existed, and cached editable wheels contain metadata rather than recoverable source. Do not fabricate old releases by attaching earlier labels to current code. |
| D63 | The user authorized a public GitHub repository and recurring publication of completed, verified versions. Publish only committed releases with matching versions, a completed changelog entry, passing checks, and reviewed source; never stage unfinished edits or move public tags. | Keep future versions available while distinguishing completed work from a passing but unfinished working tree. The recurring schedule and repository outcome are recorded in the publication report. |
| D64 | Use the account's GitHub noreply identity for commits, remove personal paths and internal run identifiers from historical reports, and exclude local state, secrets, caches, and build outputs. Do not choose a license grant on the owner's behalf. | Public project authorization covers the source project, not private profiles or an unsolicited change to licensing rights. |

See [TASK_C.md](TASK_C.md) for the guide's verification matrix and live-call
evidence, and [RELEASING.md](RELEASING.md) for future publication criteria.

## Jevlab follow-up, Phase 1 — run errors (2026-09-21)

| ID | Decision | Reason |
| --- | --- | --- |
| D65 | Pause recurring publication and supersede D63's automatic publication authorization. Keep the current command and data location until the separately reviewed rename phase. | The owner now requires phased review, a full-history privacy scrub before publishing, and owner-run PyPI publication. |
| D66 | Correct the affected current template and default model from `jev` to the documented alias `jev-latest`; preserve historical revisions. Validate malformed model identifiers before saving or sending, without a fixed version allowlist. | Live CLI/TUI reproduction confirmed HTTP 400 with `Unknown model: jev`; the questions and JSON state were valid. Future valid versions must remain configurable. |
| D67 | Capture provider diagnostics at the SDK boundary while the exact credential is available for redaction. Preserve HTTP status, provider message, request ID, and the complete credential-redacted response body in the existing JSON storage. | Status-only replacement and discarded bodies made failed runs impossible to diagnose. Never log headers, credentials, or raw exceptions. |
| D68 | Keep typed errors through presentation, history, and worker boundaries. Show a concise specific reason immediately and retain detailed diagnostics separately for F2 and verbose CLI output. | A formatted notification cannot reconstruct information that was discarded earlier. Unknown failures must identify the unknown cause honestly. |
| D69 | Keep legacy invalid-model snapshots readable for history only; revalidate before any rerun. Do not invent missing historical response bodies. | New input validation must not make old evidence inaccessible or rewrite what was originally sent. |

## Jevlab follow-up, Phase 2 — spending and field guidance (2026-09-21)

| ID | Decision | Reason |
| --- | --- | --- |
| D70 | Single Jev calls and history reruns start without a spending prompt. Show usage, latency and usage-priced cost afterward. | The owner explicitly removed single-run friction. The provider returns tokens, not an invoiced dollar amount; published-rate cost remains an estimate. |
| D71 | Keep separate interactive `confirm_batch_cost` and `confirm_eval_cost` preferences, both initially true. Save an opt-out only after acceptance; `--yes` is one-off. | Small batches still deserve a first-use prompt. Cancelling must neither send calls nor silently change future behavior. JSON/piped budget gates remain unchanged. |
| D72 | Use a shared TUI Field component for labels, descriptions, examples and inline validation. Show help in Simple mode and make it collapsible in Expert mode; preserve focused Ctrl+E explanations. | Consistent guidance belongs beside the control, without placing UI dependencies in core. Cross-field rules still use domain validation before saving or running. |
| D73 | Delete the scheduled publisher and publish verified checkpoints during development, then stop for review. | The owner's latest instruction supersedes the old recurring-publication authorization. PyPI publication remains owner-run. |
| D74 | Bring forward only clean repository creation: start `javsanesq/jevlab` with one audited initial commit authored as Javi, including 0.6.1 and Phase 2 changes in 0.7.0. Keep the old public repository unchanged. | The old history contains the owner's full name in an author field. The owner approved fresh history; no real compromised key was identified. Command and data-path migration remain in Phase 4. |
| D75 | Keep coach, comparison, lesson grading and server spending confirmations. | Those are separate paid workflows; the requested exemption is for a single Jev run. Saved batch/eval preferences do not authorize them. |

The owner has selected MIT for the release-readiness phase, superseding D64's
earlier lack of a license choice. See [FOLLOWUP_PHASE2.md](FOLLOWUP_PHASE2.md)
for this checkpoint's verification and remaining scope.
