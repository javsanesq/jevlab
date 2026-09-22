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

## First PyPI publication (owner-run)

JevLab is not yet published on PyPI. Source installation is the supported route.
The [publish workflow](../.github/workflows/release.yml) is **manual only**: a
GitHub tag or release cannot start a PyPI upload. It builds and tests the exact
tag, then uses a separate job with short-lived trusted-publishing credentials.
No long-lived PyPI token is stored in GitHub.

Before starting the workflow, the owner must complete these one-time steps:

1. Sign in to [PyPI](https://pypi.org/) with two-factor authentication. Confirm
   the `jevlab` project name is available. A pending publisher does **not** reserve
   a name until the first upload.
2. In GitHub repository **Settings → Environments**, verify `pypi` has a required
   owner review and permits only version tags (`v*`). This repository's environment
   was configured on 2026-09-22. If it is missing, recreate these protections
   **before** dispatching: GitHub can create an absent environment without them.
   A solo maintainer must be allowed to review their own run.
3. In PyPI account **Publishing**, add a **pending trusted publisher** with project
   name `jevlab`, GitHub owner `javsanesq`, repository `jevlab`, workflow filename
   `release.yml`, and environment `pypi`. The spelling must match exactly. If the
   project already exists under the owner's account, add the trusted publisher in
   that project's **Publishing** settings instead.

After a new GitHub release and its exact-commit CI run are verified, the owner
starts the matching tag's workflow. For example, for the `v0.9.1` release:

```sh
gh workflow run release.yml --ref v0.9.1
gh run list --workflow release.yml --limit 1
```

Open the listed run, inspect the build result, and approve the protected `pypi`
environment deployment. The upload does not happen until that approval. Do not
run this for an older tag that lacks the workflow. If the name or publisher setup has changed,
stop and fix that before approval. Never retry an uncertain upload with a changed
artifact or move a published tag; inspect PyPI and the run first.

After the workflow succeeds, verify what a stranger can install from the index,
outside the checkout and without the editable tool:

```sh
verification_root="$(mktemp -d)"
(
  cd "$verification_root"
  export UV_TOOL_DIR="$verification_root/tools"
  export UV_TOOL_BIN_DIR="$verification_root/bin"
  export JEVLAB_HOME="$verification_root/profile"
  export PATH="$UV_TOOL_BIN_DIR:$PATH"
  uv tool install --python 3.12 jevlab
  jevlab --version
  jevlab demo --json
)
```

These task-specific directories leave an existing editable tool alone. Check the
version and the recorded-example disclaimer. Only then update the README and
quickstart to advertise `pip install jevlab` and
`uv tool install jevlab` as supported routes. If the package name becomes
unavailable, do not upload under a different name without reviewing the metadata,
command, links, and migration instructions together.

This path follows [PyPI's pending-publisher guidance](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/),
[PyPI's security model](https://docs.pypi.org/trusted-publishers/security-model/),
and [uv's GitHub publishing guide](https://docs.astral.sh/uv/guides/integration/github/).

Earlier repository and release milestones are preserved in the
[publication archive](archive/PUBLICATION.md).
