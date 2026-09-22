# Task A: coach repair checkpoint

> Historical checkpoint: commands and measured results below describe that release.
> Since 0.8.0, the app is JevLab and the command is `jevlab`. Use the
> [current guide](GUIDE.md) for installation, commands, and existing-data compatibility.

Date: 2026-09-20. Release: 0.4.1. Task B and Task C have not started.

> Historical checkpoint: features, commands, and verification below describe this
> milestone as recorded. See the [current README](../README.md) for current usage.

## Confirmed causes

Diagnosis reproduced failures in the installed CLI and Textual Coach screen before
implementation. Both providers stopped with `coach_dependency` (CLI exit 3).
Neither provider SDK was installed in the global uv tool, although both were in the
development environment. The global receipt had no optional extras.

The real installation check also exposed the installer command's invalid `--extra`
argument. uv 0.12.9 accepts extras on the package requirement, not that flag.
Mock-only installer tests had failed to catch this external command contract.

One reproduced configuration used the display name `Opus 5`, rather than the API ID
`claude-opus-5`. One shared model setting could carry that value into OpenAI when
switching providers. The new configuration retains both models independently and
repairs recognizable legacy cross-provider values.

Provider error mapping hid the actual reason and conflated different failures.
A mocked unexpected exception also left the TUI displaying “Requesting advice…”
after its worker had failed. These now produce safe, specific messages and restore
the Ask coach button.

Key storage was **not** mismatched: setup and coaching use the `jev-workbench`
Keychain service and provider account names. Credential lookup succeeded in the
tested native macOS Keychain configuration. Diagnosis found no relevant proxy,
custom-header, organization, project, or alternate endpoint override. Both SDK
clients use the direct official provider origin. Presence alone was not treated
as proof of key validity.

## Implemented changes

- Optional global SDK installation works and survives ordinary `make install`.
- Provider-specific model fields have editable, documented defaults. Verified
  display names normalize to their API identifiers, and each provider keeps its
  own selected model. No key was replaced or copied to a file.
- CLI/TUI errors distinguish missing credentials or SDKs, rejected keys, forbidden
  access, unavailable models, billing, rate limits, transport errors, and invalid
  or unfinished responses. Provider messages are redacted and rendered literally.
- Keychain reads and requests respect an overall timeout. Cancellation and
  unexpected TUI failures restore usable controls.
- `jev doctor --coach` checks SDKs, key source, selected models, and real advice
  independently for both providers. It explains cost and asks before spending.
  `--offline` never calls a provider; machine mode needs `--yes` for live probes.
- Secret redaction covers successful advice as well as errors, including escaped
  key echoes. Full JSON proposals retain their structure and length. Provider
  HTTP debug logging is suppressed before importing the SDKs.
- Coaching still has no tools or decision execution. It proposes designs or
  critiques existing evidence; only TypeSafe produces actual decisions.

## Try it

From any directory:

```sh
jev doctor --coach --offline
jev doctor --coach
jev coach
```

The second command asks before making paid live checks. `jev coach` opens the
Textual coach; use Ctrl+Q to leave. To change the active provider:

```sh
jev config --set coach_provider=openai --json
jev coach
```

To select Anthropic:

```sh
jev config --set coach_provider=anthropic --json
```

To inspect first, then deliberately authorize the live diagnostic in a script:

```sh
jev doctor --coach --offline --json
jev doctor --coach --yes --json
```

Future editable upgrades retain the installed extras:

```sh
cd ~/jev
make install
```

## Verification

Ruff checks, Ruff formatting, and Pyright passed (zero errors or warnings).
The final full suite passed: **390 passed, 1 skipped in 50.06 seconds**. The skipped
test is the separately opt-in TypeSafe live test. Tests use mocked provider HTTP responses and
synthetic credentials, including Keychain/environment/missing key, missing SDK,
authentication, model access, billing, rate limiting, completion reasons, secret
redaction, model migration, installer extras, and TUI recovery. Offline tests
block outbound socket connections.

The installed command was exercised from a temporary directory, outside the repository:
`--version`, ordinary doctor, offline coach doctor, history, the JSON cost gate,
and the real terminal Coach screen's launch and Ctrl+Q exit all worked. Declining
the interactive live-check prompt returned exit 2 without making a request;
machine mode without `--yes` returned the JSON `cost_confirmation` error, exit 3.
Both installed SDKs remained after a plain upgrade: Anthropic 1.7.0 and OpenAI
3.16.2. Existing Jev history remained readable. No TypeSafe inference was made for
this task. `uv pip check` verified all 40 installed packages are compatible.
Final machine-mode checks asserted valid versioned JSON, exit 0, and empty stderr
for normal doctor, offline coach doctor, and coach status.

The advisory boundary was traced and tested: production runs use
`Workbench.run → SDKClient → AsyncTypeSafeClient.system_one`, followed by response
validation and local routing. Coach proposals need explicit saving. Lesson grades
are computed from Jev answers before feedback is attached; coaching cannot change
the grade. Tests enforce the core/UI dependency direction.

### Live-call log

These live probes used the installed `jev doctor --coach --yes --json` from
a temporary directory, with credentials retrieved through Keychain, synthetic input, no
automatic retries, and a 256-token output cap. They establish connectivity at
verification time, not continuing access for any account.

The first pass made one call per provider. OpenAI returned validated advice (593
input tokens, 151 output tokens, 9,400 ms; estimated $0.00029980). Anthropic reached
the provider but returned unfinished output. The original generic completion
check discarded its stop reason, so the exact first-call cause and usage are not
available. It must not be counted as successful or assumed free.

That pass also exposed an instruction-placement issue in the diagnostic: its
brevity request was inside payload data, which the coach correctly treats as
untrusted instructions. The probe now requests compact advice at the trusted
system level. The normal coaching prompt remains advisory, and unfinished output
reports retain the provider's specific stop reason.

The second pass succeeded for **both real providers**, with exit 0:

| Provider / returned model | Input tokens | Output tokens | Latency | Estimated cost |
| --- | ---: | ---: | ---: | ---: |
| Anthropic / `claude-opus-5` | 950 | 139 | 3,631 ms | $0.00822500 |
| OpenAI / `gpt-5.6-luna` | 602 | 67 | 2,747 ms | $0.00020080 |

Anthropic returned: “Connectivity check succeeded; the synthetic template is
structurally valid but its \"other\" catch-all will absorb varied intents.” Its
suggested experiment was to try more explicit topic options. OpenAI returned:
“Synthetic template is structurally plausible for connection testing.” Its
suggested experiment was to test a clear refund example against the billing option.
These are provider suggestions, not verified facts about Jev performance.

There were four real coach requests in total: one initial request per provider,
then one per provider after correcting the probe. The successful final pair costs
an estimated $0.00842580; this excludes the first pass. Provider billing is
authoritative, and the first incomplete Anthropic response's usage was not retained.

The final diagnostic calls encountered no authentication, billing, or access
blocker for the two tested models. Other configurable models, including the
fresh-install Haiku default, were checked against documentation and mocks but
were not live-tested in this verification. Normal coach calls retain unknown
cost reporting and can still fail on network/service
issues or invalid model output. The broader spend and onboarding changes belong
to Task B, which remains unstarted.
