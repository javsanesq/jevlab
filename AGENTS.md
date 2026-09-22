# JevLab repository instructions

JevLab is a Python terminal workbench for TypeSafe Jev decisions. Keep `core` independent
of the CLI, TUI, and coach. The coach only proposes or critiques; actual decisions
must come from the official TypeSafe SDK. Preserve machine-readable JSON,
stdout/stderr separation, and documented exit codes.

## Completing changes

- Follow the user's current scope and checkpoint boundaries. A request to finish
  one task does not authorize silently starting a separate feature task.
- Before declaring work complete, run `make lint` and `make test`, install the
  tool, and test the installed `jevlab` command from outside this checkout. Verify
  meaningful changed behavior. State which checks use mocks and which make real
  API calls; never imply a recorded demo is a live result.
- Record important decisions in `docs/DECISIONS.md`. For each completed future
  version, keep `pyproject.toml`, `src/jevlab/__init__.py`, and `uv.lock` consistent,
  and add a dated changelog entry describing the behavior, verification, and
  material limitations. Use `Status: complete` only after the work and required
  checks are actually complete. Keep unfinished entries clearly marked.
- The owner now authorizes pushing and publishing verified phase checkpoints to
  GitHub as work proceeds. Work in reviewable phases and stop after each one.
  There must be no scheduled publisher; it was deleted at the owner's request.
  The full-history privacy scrub must pass before publication, and PyPI publication
  remains an owner-run step. A privacy blocker must be resolved before a push.
  Never publish unfinished files, private state, or secrets, and never force-push
  or move a published tag.
- Do not fabricate earlier source snapshots. Versions before the preserved
  0.5.0 baseline have checkpoint documentation only.

## Private data and verification

Use isolated temporary profiles and mocked providers for routine checks. Never
print, log, commit, or copy API keys into files. Key presence/source diagnostics
must never reveal the value. Keep real credentials in Keychain or the supported
environment fallback. Do not read or publish the owner's history merely to
create examples. Bundled examples and screenshots must use clearly identified
synthetic data.

Use live provider documentation for new API details and record what was verified.
Keep published claims tied to evidence. A handful of bundled examples is not a
benchmark, and confidence is not a guaranteed probability of correctness.
