# Contributing to JevLab

JevLab focuses on designing, inspecting, evaluating, and exporting Jev decisions.
For substantial changes, open an issue describing the developer problem and a
small reproducible example before adding another command or integration.

## Development setup

Use macOS, Python 3.12+, and [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
git clone https://github.com/javsanesq/jevlab.git
cd jevlab
uv sync --locked --all-extras
make dev
```

`make dev` uses the normal application profile. For development without touching
saved work, set `JEVLAB_HOME` to a temporary directory before launching:

```sh
export JEVLAB_HOME="$(mktemp -d)"
make dev
```

The recorded demo, template editing, and tests need no API credentials.

## Architecture

- `src/jevlab/core`: typed domain models, official SDK calls, validation, and persistence. No UI imports.
- `src/jevlab/cli` and `src/jevlab/tui`: interfaces over the same workbench service.
- `src/jevlab/coach`: optional suggestions and critique; never a replacement for Jev decisions.
- `tests`: isolated profiles, mocked SDK transports, and CLI/TUI interaction checks.
- `docs`: user reference and design evidence; historical reports are under `docs/archive`.

Preserve the versioned JSON envelope, exit codes, stdout/stderr separation, and
existing saved data. Keep policy and side effects in the receiving application.
Record meaningful architectural tradeoffs in [DECISIONS.md](docs/DECISIONS.md).

## Before opening a pull request

Run the shared checks:

```sh
make lint
make test
```

Add regression tests that exercise the failing behavior through its caller.
Prefer a complete interaction over an assertion that repeats the implementation.
Tests must remain offline: do not depend on your Keychain, accounts, or private data.

For installation changes, install the tool and check it from outside the checkout:

```sh
make install
cd "$(mktemp -d)"
jevlab --version
jevlab demo --json
```

Describe the problem, resulting behavior, and verification in the pull request.
Call out any live API requests separately from mocks. Do not add real keys,
local profiles, private datasets, or screenshots of personal input. Report a
suspected credential exposure without including the credential itself.

Update the relevant user documentation when behavior changes. Keep edits focused;
avoid unrelated formatting or new dependencies without a demonstrated need.
Maintainers follow the [release checklist](docs/RELEASING.md) for packaging and
publication. Contributors do not need to make paid API calls to run the suite.

Follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report vulnerabilities through
the [security policy](SECURITY.md), without posting credentials or private inputs.
