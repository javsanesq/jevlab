# Release checklist

JevLab uses `vMAJOR.MINOR.PATCH` Git tags. A release is a verified source commit,
not a version bump alone. Published tags and assets are immutable.

## Verify the candidate

1. Finish the scoped change, regression tests, and documentation.
2. Keep versions consistent in `pyproject.toml`, `src/jevlab/__init__.py`, and `uv.lock`.
3. Add a dated [changelog entry](../CHANGELOG.md) describing behavior, limitations,
   and verification. Leave unfinished work marked incomplete.
4. Run `make lint` and `make test`. Live tests remain opt-in; a release does not
   require paid API calls. Distinguish live checks from mocked verification.
5. Run `make install` and check the installed command outside the checkout using
   an isolated profile. Check demo, help, JSON output, and the changed behavior.
6. Build distributions with `uv build --no-sources`. Inspect both wheel and source
   archive: the guide and license must be present; private profiles, credentials,
   databases, logs, caches, and editable-install paths must be absent.
7. Review the complete diff and file inventory, including tracked files ignored
   by `.gitignore`. Scan repository history and author metadata before publication.
8. Mark the changelog entry complete only after these checks pass, then commit the
   reviewed source and record its verification results.

Use synthetic data and clearly fake credentials in tests and screenshots. Exported
templates contain examples and notes, so review them as source. Never publish a
personal profile or assume that a passing test suite is a privacy scan. If a real
key appears in history, treat it as compromised and rotate it before proceeding.

## Publish a GitHub release

Publication is a separate maintainer action and needs explicit authorization for
the current work. Do not publish unfinished changes or bypass a privacy blocker.

1. Push the reviewed commit without force to the intended repository.
2. Wait for CI to pass for that exact commit.
3. Create its matching immutable version tag and GitHub release.
4. Build attached distributions from the tagged commit, not another working tree.
5. Confirm the remote tag, release notes, and artifacts match the tested source.

If an existing release needs a fix, publish a new version. Never move published
tags, overwrite release assets, fabricate earlier snapshots, or repair divergence
with a force-push. Report authentication or CI blockers instead.

## PyPI status

JevLab is not yet published on PyPI. Source installation is the supported route.
PyPI publication remains an owner-run step after package-name access and the
publishing workflow are prepared. Do not advertise `pip install jevlab` or
`uv tool install jevlab` as working until the published package has been verified
from a clean environment.

Earlier repository and release milestones are preserved in the
[publication archive](archive/PUBLICATION.md).
