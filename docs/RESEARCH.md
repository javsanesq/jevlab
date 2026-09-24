# Jev research

Verified on 2026-09-20 against live documentation, the public OpenAPI schema,
published package metadata, and official Python SDK source. The initial research
preceded implementation; the Phase 2 appendix below records subsequent integration
and authenticated checks.

The model is still named **Jev**. Since 0.8.0, this separate workbench is named
**JevLab** and launches with `jevlab`. Historical installation checks below retain
the command and source-folder names used when they were performed.

## SDK maintenance update — 2026-09-22

The current dependency pin is **0.7.1**. The official
[SDK changelog](https://docs.typesafe.ai/sdk/python/changelog) records early API-key
validation and exclusion of key values from logged exceptions in the September 21
patch. Exported modules use that patch and leave the host application's logger
configuration unchanged. SDK debug logs can still contain request and response
payloads; credential redaction does not make sensitive-state logging private.
The sections below preserve the dated API investigation that informed the design.

## API and SDK

The [quickstart](https://docs.typesafe.ai/introduction/quickstart) and
[Python SDK](https://docs.typesafe.ai/sdk/python) confirm `typesafe-sdk`,
`TYPESAFE_API_KEY`, and `TypeSafeClient().system_one(state=..., questions=...)`.
`AsyncTypeSafeClient` supplies the same operation asynchronously and fits the TUI.
The direct endpoint is `https://api.typesafe.ai/v1/systemone`.

The initially inspected SDK was **0.7.0**, supporting Python >=3.10. Source inspected at
[commit 2ce5c65](https://github.com/typesafe-ai/typesafe-sdk-python/tree/2ce5c65f13646cab6e6f782328194c9d85f3300a).
It depends on **httpx2**, Pydantic, and Tenacity. Transport mocks must match that
client, rather than assuming the older `httpx` package. Use an isolated uv project.

The [HTTP contract](https://docs.typesafe.ai/api) returns `model`, `answers`, and
`usage`. Answers are keyed by the question IDs:

| Type | Returned fields besides `type` |
| --- | --- |
| Choice | `choice`, `probabilities`, `confidence` |
| Score | continuous `score`, `legend`, `probabilities`, `confidence` |
| Noul | `noul`, the probability of yes |

SDK conveniences include `.choices`, `.scores`, and `.nouls`. Score maps have
integer keys in Python and string keys in JSON. Legends can contain structured
descriptions. Usage includes input/output tokens; SDK values can be `None` when
not reported. Preserve missing values rather than replacing them with zero.
`raw_http_response` exposes the complete body; a missing `request_id` property
raises an exception, so read its optional header defensively. The SDK can ignore
unknown answer kinds: our adapter must verify question coverage and types.

## Models, limits, and price

The [model page](https://docs.typesafe.ai/models) currently lists:

- Pinned model: **`jev-1.13.0`**. Both `jev-latest` and `jev-preview` resolve to it.
- Text/JSON inputs; no direct image, audio, video, or binary inputs.
- **64k tokens** across state and all questions; **32k tokens** across state and
  the longest individual question.
- Published rate limits: **250,000 tokens/second and 1,200 requests/minute**.
  The page explicitly says these can change dynamically.
- **$0.042 per million input tokens; output tokens free**. Thus 1,000 input
  tokens cost $0.000042 at the published rate, before any account-specific terms.

`models.list()` lists account-visible aliases; absence of a pinned version from
that list does not mean it is invalid. Store both requested and returned model
names. Price is not returned by inference: calculate an estimate from usage and
a dated price snapshot. A preflight token estimate is approximate; no tokenizer
or token-count endpoint was found in the reviewed public SDK/OpenAPI.

The API reference documents at most **255 Choice options** and **10 Score
levels**, recommending at least **two Score levels**. No separate maximum question
count was found; the context budgets still apply. SDK constructors alone do not
enforce every documented constraint: Score accepts one level and question
instructions are optional in SDK/schema, despite the HTTP reference marking
instructions required. The workbench should require meaningful instructions and
2–10 Score levels, and additionally check documented limits before submission.

## Uncertainty, design, and known weaknesses

[Confidence](https://docs.typesafe.ai/confidence) summarizes a distribution; it
is not interchangeable with the winning option's probability. **Noul has no
separate confidence field.** Use yes/no probability boundaries with an abstention
interval. Score's value is an expectation over positions starting at zero, not
a measured physical quantity. Use outcome probabilities for probability
calibration, and show confidence-versus-accuracy as a separately labeled view.

The [building guide](https://docs.typesafe.ai/concepts/how-to-build-with-system-one)
recommends narrow judgments, relevant named state fields, explicit criteria,
independent questions in one request, and deterministic composition in code.
Question IDs are application identifiers, not model instructions. Score levels
need standalone descriptions; Choice definitions should distinguish alternatives.

The [Jev 1.13 jaggedness page](https://docs.typesafe.ai/model-jaggedness/jev-1.13),
reviewed by TypeSafe on September 17, identifies literal interpretation,
arithmetic/counting/date weaknesses, indirection, irrelevant context, adversarial
content, contradictory rubrics, and lack of guaranteed identities between
related questions. Compute exact quantities in code. Avoid double negatives,
reference the relevant evidence, and test adversarial and missing-evidence cases.
Do not transfer thresholds between Noul and Choice or assume independently asked
negations sum to one. Jev is not a text generator.

For later evals: Choice gets label accuracy and a confusion matrix; Noul gets
binary accuracy at an explicit decision threshold; Score gets level accuracy
under a declared rule plus continuous MAE. Tune thresholds on calibration data
and report held-out results. A coach's explanation must be labeled a hypothesis:
Jev provides no natural-language reasoning trace.

## Retries and errors

[RetryPolicy](https://docs.typesafe.ai/sdk/python/api/retries) defaults to two
retries after the first attempt, exponential backoff starting at 0.5 seconds,
a 5-second backoff cap, and 25% downward jitter. It honors `Retry-After` and
`retry-after-ms`, retries connection/timeouts plus HTTP 408, 429, and 5xx
(including 529), and has a 30-second retry scheduling budget. The default HTTP
operation timeout is 10 seconds. Use a separate overall deadline where needed;
the retry budget is not an application cancellation guarantee.

[Exceptions](https://docs.typesafe.ai/sdk/python/api/exceptions) include
`TypeSafeError`, `TypeSafeAPIError`, `TypeSafeBadRequestError`,
`TypeSafeAuthenticationError`, `TypeSafePermissionDeniedError`,
`TypeSafeNotFoundError`, `TypeSafeUnprocessableEntityError`,
`TypeSafeRateLimitError`, `TypeSafeInternalServerError`,
`TypeSafeAPIConnectionError`, `TypeSafeAPITimeoutError`, and
`TypeSafeAPIResponseValidationError`. Map them to actionable messages, preserve
status/request ID when available, and avoid a second automatic retry loop.
Timeouts can leave server completion and billing unknown; no public idempotency
contract was found. Do not promise exactly-once inference.

## Privacy: a material difference from the brief

The [privacy policy](https://typesafe.ai/legal/privacy-policy) says customer
Input is not used to train or fine-tune models. The
[legal overview](https://docs.typesafe.ai/legal) describes **ZDR as an enterprise
arrangement**, not a public request switch.

No documented ZDR/No-Training request headers or body fields were found in the
[live OpenAPI](https://api.typesafe.ai/openapi.json), API reference, or SDK.
`extra_headers`/`extra_body` are generic extension hooks and do not establish
support for invented privacy options. Proposed config: display the published
no-training policy and ZDR availability/status, without operational toggles
until an official contract is documented. Local history retention is separate
from provider retention. Coach providers have separate policies.

SDK DEBUG logging includes request/response bodies even though secret headers
are redacted. Disable body logging in the application. Store credentials only
in Keychain or read environment variables; never persist SDK request headers.

## Patterns and integrations

Skimmed both requested community collections:
[awesome-typesafe](https://github.com/AbdelStark/awesome-typesafe) and
[awesome-jev-by-typesafe](https://github.com/Anil-matcha/awesome-jev-by-typesafe).
Useful recurring shapes include bounded routing, tool selection, evidence
verification, candidate reranking, and calibration-driven human review.
Treat them as discovery aids, not API authorities or verified performance claims.
The official [citation cookbook](https://docs.typesafe.ai/cookbooks/citation_check)
checks exact quote presence in code before a semantic support judgment;
[guardrails](https://docs.typesafe.ai/cookbooks/llm_guardrails) combine independent
hazard questions with code-owned policy. Their cached examples use Jev 1.12 and
are not measurements of the current model.

| Integration | Verification and export implication |
| --- | --- |
| LangChain | Official [source/README](https://github.com/langchain-ai/langchain/tree/master/libs/partners/typesafe) and [LangChain guide](https://www.langchain.com/blog/building-a-harness-with-jev) document `TypeSafeClassifier`, a Runnable. Published package `langchain-typesafe` is 0.0.1a2. Its dependency list does not include the official SDK; export a Runnable wrapper around our SDK function to preserve the required SDK path. |
| Pydantic AI | Official [provider guide](https://pydantic.dev/docs/ai/models/typesafe/) and published `pydantic-ai-slim` 2.46.0 verify the `typesafe` extra and `TypeSafeModel`/`TypeSafeProvider`. It maps output schemas to questions and can round Score values or derive a boolean margin. For exact template reproduction, export a tool wrapper around the SDK function; do not silently translate the rubric or replace raw Noul probabilities with derived confidence. |

Integrations are verified by documentation/source, not executed. Recheck and
contract-test at Phase 4. No coach model IDs have been selected or hard-coded;
provider APIs will be verified when Phase 2 is approved.

## Local setup observation

`uv` 0.12.9 is available. No `jev` command was found on PATH. The proposed source
directory `~/jev` did not previously exist. Only the planning documents had
been created there at that checkpoint. Repeat the command collision check
at installation time. This paragraph records the initial environment, not the
installed application's current status.

## Phase 2 verification, 2026-09-20

The tested configuration retrieved its TypeSafe credential from macOS Keychain.
An authenticated `models.list()` succeeded. A real support-triage inference on
synthetic input returned all three answer types from `jev-1.13.0`, reporting
512 input / 69 output tokens and a published-rate estimate of $0.000021504.
No key was printed or copied to application files. These are historical
verification results, not a claim about current account access.

Rechecked the building guide and version-specific jaggedness page for the lesson
and coach guidance snapshot. The bundled cases and explanations are original
synthetic teaching examples, not copied benchmark results. Source links accompany
each pattern. Generic evaluation, calibration, and holdout support remain Phase 3.

For the optional coach, verified [OpenAI Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)
and [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create),
then inspected the installed official SDKs: **openai 3.16.2** and **anthropic 1.7.0**.
Both currently use `httpx2`, so transport-level mocks use that package. OpenAI uses
`AsyncOpenAI.responses.create` with `instructions`, `input`, configured `model`,
`max_output_tokens`, and `store=False`; consume `output_text` only from a completed
response. Anthropic uses `AsyncAnthropic.messages.create` with a system prompt,
user message, configured model, and `max_tokens`; consume text blocks only after
normal completion. Reject truncated/refused/malformed advice. Neither adapter
offers tools or executes provider-generated content.

No coach model ID is hard-coded, and provider availability is not inferred from a
ChatGPT/Codex or Claude subscription. Model names and separate keys are configured
by the user. The coach remains disabled here; its SDK integrations were tested with
mock HTTP, not authenticated provider requests. `store=False` controls Responses
storage and is not a blanket provider ZDR guarantee. Coach costs remain unknown
unless a later feature introduces verified, model-specific pricing.


## Phase 3 verification — 2026-09-20

Re-read live [API reference](https://docs.typesafe.ai/api),
[Python SDK](https://docs.typesafe.ai/sdk/python),
[models](https://docs.typesafe.ai/models),
[confidence](https://docs.typesafe.ai/confidence), and the current
[Jev 1.13 jaggedness page](https://docs.typesafe.ai/model-jaggedness/jev-1.13).
The web reader failed to retrieve Markdown; direct HTTPS Markdown reads succeeded.
No wire-contract change was needed; installed `typesafe-sdk==0.7.0` remains pinned.

- Live model docs still list `jev-1.13.0`, both aliases pointing to it, $0.042 per
  million input tokens, free output, 64k combined / 32k state-plus-longest context.
- Published current limits are 1,200 requests/minute and 250,000 tokens/second,
  explicitly described as changeable. The workbench defaults conservatively to
  two logical call starts/second and four workers; this is not a promise about
  the account's quota and does not limit SDK retries or token throughput.
  *(0.11.0 update: defaults are now ten starts/second and eight workers, half the
  published request limit, and a job halves its start rate after a 429; see D107.
  These limits were not re-verified live for 0.11.0.)*
- SDK retries/backoff remain authoritative for 429/529. No application retry
  loop is stacked around a single inference; retrying failed/uncertain dataset
  rows is an explicit resume action.
- Choice returns the highest-probability option; Score is the probability-weighted
  level value. Choice/Score `confidence` derives from distribution shape. Noul
  has no separate confidence field. Therefore our reliability bins use predicted-
  class probabilities, and automation gates keep the existing primitive-specific
  semantics. Accuracy/rounding/binning/Brier definitions are workbench evaluation
  choices, not additional claims about the TypeSafe API.
- Aliases move. The docs recommend pinning after threshold tuning; eval reports
  retain returned version counts and per-call versions. The jaggedness guidance
  still calls for representative/edge-case testing and arithmetic in code.

Phase 3 verification uses mocked official SDK transports and local fixtures. No
additional paid requests were made as part of this phase's automated checks.

## Phase 4 verification — 2026-09-20

Rechecked the live [API](https://docs.typesafe.ai/api) and
[Python SDK](https://docs.typesafe.ai/sdk/python), plus the installed SDK 0.7.0
source. Exported sync/async functions call `system_one(state=..., questions=...,
model=...)` on the official clients with the direct TypeSafe origin. Response
objects expose typed answer mappings and usage; the local service retains raw JSON.
No new model names, prices, privacy flags, or wire-format assumptions were added.

The framework variants are verified adapters around that exact SDK call:

- **langchain-core 1.6.3:** [RunnableLambda reference](https://reference.langchain.com/python/langchain-core/runnables/base/RunnableLambda), sync `invoke` and async `ainvoke`, tested against the installed package. The separate [native TypeSafe integration](https://github.com/langchain-ai/langchain/tree/master/libs/partners/typesafe) exists, but its classifier interface is not used to transform saved templates.
- **pydantic-ai-slim 2.46.0:** [Tool reference](https://pydantic.dev/docs/ai/api/pydantic-ai/tools/) and [native TypeSafe adapter](https://pydantic.dev/docs/ai/models/typesafe/) were inspected, including [source](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/models/typesafe.py). The export supplies a Tool to an already configured agent, preserving continuous Score and Noul probabilities. A real Pydantic AI Agent with local FunctionModel exercises the exported tool offline.
- The full receiving-project setup, error/timeout semantics, and differences from native integrations are documented in [INTEGRATIONS.md](INTEGRATIONS.md). No live framework-agent/provider call was made.

The local server uses **Starlette 1.6.0** and **Uvicorn 0.53.0**, verified against
installed signatures and official [request documentation source](https://github.com/Kludex/starlette/blob/main/docs/requests.md)
and [Uvicorn settings](https://github.com/encode/uvicorn/blob/master/docs/settings.md).
ASGI tests exercise request streaming, auth, host/origin checks, limits, and actual
SDK HTTP mocks. The server adds local orchestration only; TypeSafe inference still
goes directly through its SDK. HTTP-operation timeouts and an overall workbench
deadline remain distinct; a disconnected caller does not prove cancellation upstream.

For lessons 6–10, re-read [confidence](https://docs.typesafe.ai/confidence),
[Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13),
[function calling](https://docs.typesafe.ai/cookbooks/function_calling),
[guardrails](https://docs.typesafe.ai/cookbooks/llm_guardrails),
[citation checking](https://docs.typesafe.ai/cookbooks/citation_check), and
[reranking](https://docs.typesafe.ai/cookbooks/rerank_typesafe).
The design guidance remains: pass direct relevant evidence, isolate judgments,
compute arithmetic and enforce policy in code, and evaluate on representative data.
Original visible practice datasets teach these workflows; no benchmark claim is made.

## Task A — coach verification (2026-09-20)

Checked the installed official SDKs (`anthropic` 1.7.0, `openai` 3.16.2) and current
provider documentation before choosing identifiers or request parameters:

- [Anthropic models](https://platform.claude.com/docs/en/models/overview): `Opus 5`
  is a display name; its API identifier is `claude-opus-5`. Configuration migration
  preserves recognized choices rather than silently selecting a different model.
- [Haiku 4.5](https://platform.claude.com/docs/en/models/haiku-4-5/overview):
  `claude-haiku-4-5-20251001` is the pinned, economical default for new Anthropic
  configuration. The standard rate is $1 input / $5 output per million tokens.
- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna):
  `gpt-5.6-luna` supports Responses and is the new OpenAI default, at $0.20 input /
  $1.20 output per million tokens. Its documented default reasoning uses additional
  budget; coaching sets `reasoning.effort=none` for this exact known ID only.
- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) and
  [OpenAI pricing](https://developers.openai.com/api/docs/pricing/): doctor estimates
  use standard uncached rates, with Opus 5 at $5 input / $25 output per million.
  Unsupported custom model prices remain unknown. These estimates do not promise
  account-specific pricing or model access.
- [Anthropic errors](https://platform.claude.com/docs/en/api/errors) and
  [OpenAI errors](https://developers.openai.com/api/docs/guides/error-codes):
  distinguish authentication, permission/model access, quota/billing, rate limits,
  timeout/network problems, bad requests, and provider failures. A 429 quota error
  needs a billing fix; retrying it as rate limiting is misleading.

Requests still use official async Messages/Responses clients and direct provider
origins. OpenAI requests retain `store=False`; this is not a ZDR guarantee. Coach
output remains validated advisory JSON; no provider is allowed to call tools or
execute a Jev decision. Mocked HTTP tests inspect both actual SDK request paths.
Live account results and installed-command verification are recorded in
[TASK_A.md](archive/TASK_A.md).

The local `uv 0.12.9 tool install --help` and official
[uv command reference](https://docs.astral.sh/uv/reference/cli/#uv-tool-install)
confirm that `--extra` is not a tool-install option. The installer now puts extras
on the editable package requirement itself. Actual `make install COACH=both`
installed both provider SDKs, and a subsequent plain `make install` retained them.

## Task B — explanation semantics (2026-09-20)

Re-read the official [confidence guidance](https://docs.typesafe.ai/confidence.md),
[System One concepts](https://docs.typesafe.ai/concepts/system-one.md), and
[Noul reference](https://docs.typesafe.ai/primitives/noul.md). The web reader could
not retrieve these pages; the official Markdown pages were read directly over HTTPS.
No API detail was inferred from a coach answer.

- Choice/Score confidence summarizes concentration of the returned distribution;
  it is distinct from an option probability and does not itself mean the answer
  has that chance of being correct. Teaching copy uses a 0.00–1.00 confidence
  value and explains this distinction explicitly.
- Noul supplies a probability of yes and no separate confidence. Local saved
  thresholds decide yes/no/review routing; the UI does not fabricate confidence.
- Calibration compares predicted probabilities with observed labeled results.
  Explanations preserve the existing calculations and never claim a small practice
  dataset proves real-world reliability.

No provider transport, exported decision implementation, or model version changed
for Task B. Task B tests and terminal checks used mocks or recorded examples;
the previous Task A live provider verification is documented separately.

## Task C — installation and beginner account setup (2026-09-21)

The current official [TypeSafe index](https://docs.typesafe.ai/llms.txt),
[quickstart](https://docs.typesafe.ai/introduction/quickstart.md), and
[confidence guidance](https://docs.typesafe.ai/confidence.md) were read directly
over HTTPS. The quickstart links API-key setup to
`https://console.typesafe.ai/keys`. Account creation and dashboard control labels
were not inferred from the SDK, and the guide acknowledges account-specific steps.

The [official uv installation page](https://docs.astral.sh/uv/getting-started/installation/)
confirms the macOS shell installer. A fresh-shell check used the installed uv,
preserved the existing coach extras, installed the new editable command, and
tested it outside the project. Package verification additionally used a normal
wheel, rebuilt from the source distribution, with the complete guide included.
Task C's one real TypeSafe example and its limits are recorded in [TASK_C.md](archive/TASK_C.md).
