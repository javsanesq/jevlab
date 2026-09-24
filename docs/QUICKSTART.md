# Quickstart

Install from source, inspect a free recorded example, then run a real Jev decision.
This guide follows macOS. Linux users can use the same installation commands and
[Linux credential setup](REFERENCE.md#linux-credentials). Install
[uv](https://docs.astral.sh/uv/getting-started/installation/) first.
New to Terminal? Use the [beginner's guide](GUIDE.md). [All documentation](README.md).

## Install

```sh
git clone https://github.com/javsanesq/jevlab.git
cd jevlab
uv run --no-project --python '>=3.12' python scripts/install.py
jevlab --version
```

Expect `jevlab 0.10.0` or later. Keep the checkout: this is an editable installation.
`make install` is an optional shortcut when `make` is available.
If the command is missing, run `uv tool update-shell` and open a new terminal.
PyPI installation is not yet available. Existing users should keep their current
checkout and follow the [upgrade notes](REFERENCE.md#upgrading-from-jev).

## See a result without a key

```sh
jevlab demo
```

Expect **RECORDED EXAMPLE**, probability bars, and explanations. The values are
synthetic teaching examples, not live Jev output. No key, request, or charge is
involved. Press **Ctrl+Q** to return to your shell.

## Make a real request

Obtain a [TypeSafe API key](https://console.typesafe.ai/keys), then run:

```sh
jevlab config
```

The default Simple setup asks for the TypeSafe key in one hidden prompt; press
Enter to skip it. Do not put the key in a command or file. A saved key goes to
macOS Keychain. `jevlab doctor` checks local setup without making a paid call.

```sh
jevlab
```

Home opens directly. Select `support-triage` and press **Enter**. Its Playground
contains an example refund request; press **Ctrl+R** to send it to Jev.
**This makes one billable call without a confirmation prompt.** The result shows
answers, uncertainty, review routing, latency, tokens, and a usage-based cost
estimate. It does not send a message or issue a refund.

For the same workflow in a script:

```sh
printf '%s' '{"ticket":{"message":"I was charged twice. Please refund one charge."}}' \
  | jevlab run support-triage --state - --json
```

Expect one JSON envelope with `ok: true`, or a specific error and next step.

## Make a design and take it into code

From Home, press **Ctrl+N**. The new template starts with one Choice question and
plain-text state. Edit its name and question, then press **Ctrl+S** to save.
New designs request human review until you set and validate thresholds.
The [guided example](GUIDE.md#8-making-your-own-template) walks through each field.

Export a saved design without calling an API:

```sh
jevlab export support-triage --lang python --output decision.py
```

Expect a new Python module. Existing files are never overwritten.
[Integration instructions](INTEGRATIONS.md) explain dependencies and result handling.

**Next:** [work with project YAML and regression baselines](PROJECTS.md),
[evaluate labeled examples](REFERENCE.md#evaluate-tune-batch-and-compare),
[inspect history](REFERENCE.md#cli-and-pipes), or run `jevlab tour` for optional guidance.
**Keys:** Ctrl+P finds actions; Ctrl+E explains a control; Esc goes back; Ctrl+Q quits.
Batches, evals and comparisons ask first only when the estimate is above your $1.00 confirmation budget
(`jevlab config --set confirm_cost_usd=…`) or the price is unknown. Estimates are not spending caps.
