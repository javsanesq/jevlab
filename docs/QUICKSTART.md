# jev quickstart

Jev makes small judgments: choose a team, rate an issue, or estimate whether a
message asks for a refund. This workbench lets you try those judgments and see
their uncertainty. It does not send messages or issue refunds.

New to Terminal? Use the [complete beginner's guide](GUIDE.md). Already installed?
Skip to step 4. The source folder should be `jevlab` inside your home folder;
[installation instructions](GUIDE.md#4-installation) explain how to get it and uv.
The repository is now **jevlab**; this release's command is still `jev` and its
data folder is still `~/.jev/`. Keep an existing installation's source folder.

1. Run this in Terminal:

   ```sh
   cd ~/jevlab
   ```

   **You should see:** another prompt. A missing-folder error means the source
   folder needs to be placed there first. For an older installation at `~/jev`,
   use `cd ~/jev` instead.

2. Run:

   ```sh
   uv run python scripts/install.py
   ```

   **You should see:** installation progress, then an installed `jev` executable.
   If the command is not found afterward, the full guide explains PATH setup.

3. Run:

   ```sh
   jev --version
   ```

   **You should see:** `jev 0.7.0`, or a later version.

4. Run:

   ```sh
   jev demo
   ```

   **You should see:** **RECORDED EXAMPLE**, with bars and plain explanations.
   Its values are authored teaching examples, not live Jev output. No key or
   charge is involved. Tab moves between controls; Enter selects.

5. Press Ctrl+Q.

   **You should see:** the terminal prompt.

6. Run:

   ```sh
   jev tour
   ```

   **You should see:** the welcome tour. Its Continue button leads to optional
   key setup and an explained example. The [full guide](GUIDE.md#5-first-run)
   spells out each tour step. A TypeSafe API key is a private account access
   code; the tour's password field saves it in macOS Keychain. The remaining
   steps below assume the tour is finished and you have left with Ctrl+Q.

7. Run:

   ```sh
   jev
   ```

   **You should see:** the tour if unfinished, otherwise the home screen. Escape
   skips the tour. The built-in design is called `support-triage`.

8. Open `support-triage` with Enter when its row is focused.

   **You should see:** the sample refund request in the playground.

9. Press Ctrl+R when you want one real paid request.

    **You should see:** the answer, probabilities, review recommendation, time,
    token counts, and a cost estimate based on returned usage, or a clear setup
    error. There is no price prompt for a single run. A valid TypeSafe key is needed.
    The result is saved; no ticket or refund is sent.

**Helpful keys:** Ctrl+E explains the focused item; Ctrl+G opens definitions;
Escape goes back; Ctrl+Q quits. More options reveals advanced controls.
Forms explain each field and show corrections while you edit. Expert mode keeps
descriptions under **Field help**.

**Free commands:** `jev demo` replays the example; `jev doctor` checks local setup;
`jev guide` opens the full guide; `jev guide --web` opens a local browser copy.
Confidence summarizes the spread of probabilities, not the chance of being right.
Prices are estimates, not caps. JSON and piped commands retain their automation
rules and should be treated as advanced.
Batch and eval jobs still ask first. Their **Don't ask again** choices are saved
separately only when you accept; `--yes` skips one job's prompt without saving.
