# Task C: English beginner's guide checkpoint

> Historical checkpoint: commands and measured results below describe that release.
> Since 0.8.0, the app is JevLab and the command is `jevlab`. Use the
> [current guide](GUIDE.md) for installation, commands, and existing-data compatibility.

Completed: 2026-09-21. Application version: 0.6.0.

## Delivered

- [GUIDE.md](GUIDE.md): the twelve requested sections, from opening Terminal to
  getting help. It defines terms, uses concrete examples and ASCII screen sketches,
  and pairs numbered actions with visible checks. It distinguishes recorded
  examples, actual decisions, local routing, and advisory coaching.
- [QUICKSTART.md](QUICKSTART.md): a short path to installation, the free demo,
  the tour, and a first confirmed live request.
- [README.md](../README.md): a plain introduction and prominent guide links,
  followed by the developer and integration reference.
- `jev guide`: an offline terminal reader. Space advances, `b` returns, and `q`
  exits the default pager. Redirected output is the complete Markdown document.
- `jev guide --web`: a local, script-free HTML copy under `~/.jev/docs/guide.html`.
  It uses the browser without a model request. Link addresses are visible text.
- `jev guide --json`: the existing versioned envelope, containing the complete
  Markdown text; no pager, browser, credential lookup, or history initialization.
  JSON takes precedence if combined with `--web`.
- Editable installations read the authoritative guide from `docs/GUIDE.md`.
  Built wheels include that same guide, so normal installations need no checkout.

No decision logic, exported code, credential values, or saved user preferences
changed. Important choices are recorded in [DECISIONS.md](DECISIONS.md).

## Try it from any directory

```sh
jev guide
jev guide --web
jev tour
```

Each command is free. The guide explicitly identifies the steps that can make
paid requests, including the worked example and optional coach checks.

## Verification performed

`make lint` passed Ruff checks, formatting, and Pyright with zero errors or
warnings. `make test` passed **481 tests**, with **1 skipped** opt-in live test,
in 85.21 seconds on the final local rerun. The new command's eleven regression tests cover terminal and
redirected behavior, JSON precedence, safe browser failure, editable/resource
loading, and static HTML. Offline tests block outbound sockets.

The guide was checked against actual code, UI labels, and installed commands.
The following were run rather than inferred:

| Guide area | Verification |
| --- | --- |
| Installation | Fresh `zsh -f` from a temporary directory: `pwd`, `uv --version`, `cd ~/jev`, `uv run python scripts/install.py`, `uv tool update-shell`, and `jev --version`. Global editable 0.6.0 installed; the existing coach packages were preserved. |
| First run and recorded demo | Isolated 80×24 Textual walkthrough: all tour steps, Settings entry without saving a key, recorded demo, finish, explain key, glossary, and return navigation. No network or credential access was allowed. |
| Template creation | All seventeen section 8 steps performed with keyboard navigation and a terminal-paste event. The saved `my-message-sorter` contained the exact edited wording and criterion; its three questions, example, and cutoffs were preserved. Save, quit, and reopening succeeded. No inference occurred. |
| Worked real example | Installed `jev` in a real terminal from a temporary directory: open support-triage, Ctrl+R, inspect estimate, choose Get answers, read the actual response, quit. One real TypeSafe request; details below. |
| History, learning, evaluation, thresholds | Screens opened and evaluated using synthetic SDK responses. A completed mocked eval opened Adjust review rules; arrow keys changed the cutoff by 0.01 and Save review rules persisted it. |
| Advanced CLI | Dataset import, evaluation planning, lesson planning, and export ran against isolated synthetic data. They made no provider requests. Batch/eval execution syntax was checked without another live paid run. |
| Export | The guide's exact Desktop export command succeeded. Its generated module parsed as Python. Only the temporary export created for this verification was removed afterward. |
| Server | The guide's exact token-generation, `serve --check`, and `serve` commands ran from a fresh shell. The server's cost acknowledgment was accepted, `/health` returned version 0.6.0, and Ctrl+C stopped it. No decision request was sent to it. The temporary local token was never printed or written to a file. |
| Doctor and errors | Installed ordinary doctor, JSON doctor, and offline coach doctor ran outside the project in an environment-only profile. Missing coach keys correctly produced setup reports and exit 3. |
| Terminal guide | Installed command from a temporary directory, with the default-style `less` pager: content displayed; Space, `b`, and `q` worked; exit 0. Redirected Markdown and JSON matched the source guide. |
| Browser guide | Installed `guide --web` opened a readable local HTML file in Safari. Static output contains no scripts, remote assets, or credential material. |
| Distributions | Built a wheel and source archive, verified the exact guide bytes in both, rebuilt the wheel from the source archive, and installed a noneditable wheel in a temporary environment. Guide/demo JSON worked outside the source tree with no editable fallback. |

After the public repository was created, its main-branch ZIP was downloaded,
extracted, and renamed to `jev` in a temporary folder. The documented
`uv run python scripts/install.py` command installed that copy into an isolated
tool environment. A fresh `zsh -f` from a temporary directory passed installed version,
guide JSON, recorded demo JSON, and offline doctor JSON checks. The installed
guide matched the downloaded source. See [publication](PUBLICATION.md).

## One real call, explicitly identified

This was a **live TypeSafe call**, not a mock or demo. It used the already saved
Keychain credential, synthetic refund text from the bundled template, and an
isolated temporary profile. No key was changed or copied to a file. Retries were
disabled in that temporary profile to bound this verification to one request.

| Returned model | Choice | Choice confidence | Score | Score confidence | Noul P(yes) |
| --- | --- | ---: | ---: | ---: | ---: |
| jev-1.13.0 | billing | 1.00 | 0.09 | 0.86 | 0.98 |

The request took **892 ms**, used **511 input / 69 output tokens**, and had an
estimated cost of **$0.000021462** at the recorded rate. The preflight estimate
was $0.00001004; this difference illustrates the documented estimation limits.
The provider's bill is authoritative. All three answers met the starter routing
rules; no external action was performed. This one example is not a benchmark.
No Anthropic or OpenAI request was made for Task C; previous coach live checks
are documented in [Task A](TASK_A.md).

## What could not be verified literally

- Creating a new TypeSafe account and issuing another real key were not performed:
  a working key already existed, and replacing it was unnecessary. The account
  link was verified against the current official quickstart. Dashboard labels,
  email verification, and billing requirements can vary by account.
- uv's installer was not rerun on a clean Mac: uv was already installed, so the
  guide's explicit skip branch applied. The installation command was checked
  against uv's current official documentation. This is a fresh-shell check, not
  a claim that the computer had never installed Python or developer tools.
- Finder downloading, renaming, and moving a ZIP on a new user's Mac were not
  simulated over the existing source folder. The downloadable archive and its
  extracted-folder installation were verified using a temporary copy, as above.
- Physical Mac clipboard shortcuts, Fn/media-key settings, Spotlight, and native
  Keychain permission dialogs were not reproduced with new credentials. The app's
  actual key bindings and paste handling were verified with Textual and PTY input.
- Other features were not rerun against live providers merely to write their
  descriptions. Mocked flows and actual command parsing were used where noted.

The guide states these differences where needed and does not promise identical
model numbers, account screens, or installation progress messages on every Mac.
