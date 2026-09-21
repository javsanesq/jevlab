# Releasing Jev

The owner has authorized publishing completed, verified phase checkpoints to
GitHub as development proceeds, followed by a stop for review. There is no
scheduled publisher; it was deleted at the owner's request on 2026-09-21.
PyPI publication remains an owner-run step. That authorization covers routine publication of
those versions. It does not make unfinished local edits a release, authorize
publication of private data, or authorize force-pushing shared history.

## What makes a version ready

1. Finish the intended change and its documentation. A partially implemented
   task is not release-ready even if the current tests pass.
2. Set the same application version in `pyproject.toml` and
   `src/jev/__init__.py`, and update `uv.lock`. Do not reuse a published version.
3. Add the version to [CHANGELOG.md](../CHANGELOG.md), describing the final behavior,
   limitations, verification, and any actual live calls separately from mocks.
4. Run `make lint` and `make test`. Both must pass. A skipped, explicitly opt-in
   live test is expected; do not make paid API calls merely to publish a release.
5. Run `make install` and verify the installed `jev` command from a directory
   outside the source checkout, such as a temporary directory. Use an isolated profile without
   keys for free demo, help, diagnostics, and JSON smoke checks. Also verify any
   release-specific behavior. Record the commands and results in the checkpoint.
6. Build and inspect distribution artifacts. They must contain the application
   and its documented resources, including the guide when that feature exists.
   Editable wheels pointing at a developer's filesystem are not release assets.
7. Review the complete source diff and publication file inventory. Do not rely
   on `.gitignore` alone: an already tracked private file stays tracked.
8. Mark the changelog entry `Status: complete`, add the completion date, and link
   its verification record. Commit the finished source, tests, and documentation.

The release commit must contain that complete changelog entry and matching version
metadata. A passing test run, version bump, or tag by itself is insufficient.

## Public files and private state

Publish reviewed source code, tests with fake credentials, original bundled
datasets, documentation, and reproducible example images. Inspect files for real
credential literals, private endpoints, personal paths, local run identifiers,
private input states, and account-specific reports before staging them.

Never stage `~/.jev/`, configuration containing credentials, Keychain material,
environment files containing secrets, history databases or sidecars, logs,
personal datasets, local outputs, virtual environments, or caches. Do not copy
the uv cache into the repository. An exported template may contain private
examples or notes; review it before publication too.

Tests deliberately contain obvious fake key-shaped strings to exercise redaction.
Their synthetic status should remain clear. Scan the entire history, including
author metadata, before publishing. If it contains private information, prefer a
fresh repository with a reviewed initial commit over rewriting published tags.
The owner selected MIT; adding the license is part of the release-readiness phase.

## Tags and GitHub releases

Use `v` followed by the application version, for example `v0.5.0`. Each tag points
at the exact verified release commit. Create a GitHub release for that tag with
the matching changelog notes and verification limits. Any attached wheel or
source archive must be built from that same commit.

Tags and published assets are immutable. If a release needs a fix, increment the
version and publish the new commit. Do not move a tag, overwrite its assets,
rewrite public history, or force-push. Check that the remote tag and release
resolve to the intended commit after publishing.

The 0.5.0 baseline was preserved before the 0.6.0 changes. Its public snapshot may
include documentation redactions for personal paths and local run identifiers;
the release notes disclose that preparation. Versions 0.1.0 through 0.4.1 have
historical checkpoint documents but no retained source snapshots. Do not create
old tags pointing at newer code or present metadata-only editable wheels as
recoverable releases.

## Publication during development

After a checkpoint passes local checks and the privacy gate, push its reviewed
commit to the expected repository without force. Wait for CI to pass for that
exact commit before creating its immutable version tag and GitHub release.
Verify that the remote tag and release point to the tested source.

Do not publish unfinished changes, move old tags, or silently repair a diverged
remote. Report authentication, CI, or privacy blockers. Publication does not
change the user's keys, models, preferences, or saved history.

The current repository is [jevlab](https://github.com/javsanesq/jevlab), starting
with one clean initial commit authored as Javi. Its first checkpoint is 0.7.0;
it includes the verified 0.6.1 error fixes. Earlier development records remain
as documentation. The installed command remains `jev` until the rename phase.
