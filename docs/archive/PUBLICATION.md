# Public repository and release verification

The current public repository is [javsanesq/jevlab](https://github.com/javsanesq/jevlab).
Its [releases](https://github.com/javsanesq/jevlab/releases) contain immutable version
tags, release notes, Python wheels, and source archives. Application versions are
separate from the TypeSafe model chosen in a template.

## Clean publication checkpoint

The owner approved starting a fresh repository on 2026-09-21 after the privacy
check found the owner's full name in one old commit's author metadata. The clean
repository starts with one initial commit authored as **Javi**, at version 0.7.0.
It includes the verified 0.6.1 run-error fixes and the Phase 2 spending/form changes.
No earlier tags are recreated here. The old public repository is unchanged;
creating this repository does not remove information already published there.

The scan covered all four old commits, 152 historical blobs, tags, reflogs and
current source. No real key, private profile, database, personal home path, or
employer reference was found. Key-pattern matches were synthetic test fixtures;
no compromised real key was identified. Example absolute paths were generalized.
The packaged artifacts are separately checked before publication.

Since 0.8.0, the command and distribution are `jevlab`, with no installed `jev`
shim. New profiles use `~/.jevlab/`; existing `~/.jev/` profiles remain in place
and are used with a notice when no new profile exists. Existing Keychain entries
remain readable. See the [upgrade notes](../REFERENCE.md#upgrading-from-jev).

The 0.8.0 rename checkpoint passed Ruff, Pyright and 714 offline tests, with one
live test skipped. Its audit checked all objects in the clean repository,
including unreachable objects and commit/tag metadata, plus current source.
No real credential or new personal-data exposure was identified. The wheel has
84 files and the source archive has 160: application code, reviewed tests/docs,
build metadata and explicitly synthetic examples. Neither includes private
profiles, databases, environment files, keys or editable-install path files.
The sole installed command is `jevlab`. Editable and Python 3.12 wheel commands
were tested outside the checkout, including the key-free TUI demo. Existing
profile contents and Keychain access were checked without live provider calls.

## Earlier repository history

The first published release is 0.5.0. Its runtime source, tests, examples, and
build metadata match the snapshot preserved before Task C. Publication edits
removed personal paths and local run identifiers from documentation and added
release instructions. Ruff, Pyright, and 470 offline tests passed on Python 3.12;
one opt-in live test was skipped. A built wheel installed and ran outside its
checkout before the release was published.

The next release, 0.6.0, adds the English guide and its terminal/browser commands.
Its detailed behavior and checks are recorded in [Task C](TASK_C.md). Earlier
versions have dated checkpoint documents in the [changelog](../../CHANGELOG.md),
but no recoverable source snapshots. No old tags were invented for them.

## Earlier download and CI checks

The public main-branch ZIP was downloaded and installed with the exact installer
command in the guide, using temporary tool and profile directories. A fresh shell
outside the extracted source passed installed version, guide JSON, recorded demo,
and offline doctor checks. This verified the downloadable installation route
without replacing the owner's installation or reading private history.

[Current GitHub CI](https://github.com/javsanesq/jevlab/actions/workflows/ci.yml) uses Python
3.12 on macOS, the committed dependency lock, pinned actions, and offline tests.
It checks lint, types, tests, package resources, and an installed wheel outside
the source tree. No provider secrets are configured for these checks.

The first CI run caught a test assumption: a readable browser error wrapped at
a different point because the runner's temporary path was longer. The assertion
now compares whitespace-normalized prose while still checking stderr separation,
the exit code, the saved guide, and suppression of private exception details.
The application's error message did not need to change.

Each new release requires passing CI for its exact commit before its tag is
published. Distribution assets are built from that same commit. Published tags
and assets are never moved or replaced; corrections need a new version.

## Publication policy

The scheduled publisher was deleted at the owner's request on 2026-09-21.
Verified phase checkpoints are now pushed and released as part of development,
then work stops for review. There is no background publication task.
Full rules are in [RELEASING.md](../RELEASING.md); PyPI publication remains owner-run.

## Boundaries

The repository contains reviewed source, synthetic examples, and documentation.
It excludes the owner's keys, private `~/.jevlab` and legacy `~/.jev` profiles,
history, local datasets,
and environment files. Routine release checks make no paid API requests. Task C's
one separately authorized live TypeSafe verification is identified in its report.

The owner selected MIT. The license file and remaining release-readiness work
are scheduled for Phase 5; this checkpoint does not claim those are complete.
