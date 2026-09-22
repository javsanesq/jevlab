# The beginner's guide to JevLab

For macOS · English · jevlab 0.8.0

The application is named **JevLab** and launches with `jevlab`. New installations
keep personal files in `~/.jevlab/`. An upgrade keeps using an existing `~/.jev/`
folder when no new folder exists, and shows a notice. Your saved work and keys
remain available; nothing is moved or copied.

Already have a working `jevlab` command? Start at [First run](#5-first-run).
If you used the earlier `jev` command, run the installer in section 4 from your
existing source folder. Keep that folder in place; its name need not change.
For the shorter route, see the [Quickstart](QUICKSTART.md).
After installation, `jevlab guide` opens this guide in a **pager**, a reader for
long terminal text. `jevlab guide --web` opens it as a page in your browser.

## 1. What this is

Imagine a small shop with three trays for incoming letters: payments, broken
products, and everything else. Someone reads each letter and chooses a tray.
You also want to know whether the letter asks for a refund, and how serious the
problem sounds. Jev can make those small judgments from the words in the letter.

**Jev** is the online decision model made by TypeSafe. A model is a computer
system trained to recognize patterns. **JevLab**, written `jevlab` in commands,
is this separate workbench: a place to prepare questions, try them, and examine
the answers. You can save a set of questions and use it again for another letter.
The workbench shows alternative answers and how strongly Jev favors them.

Jev is not a chatbot. It does not write replies, essays, or explanations. It
chooses among answers, gives a rating, or estimates the chance of yes. The
workbench adds plain explanations of those numbers. A separate, optional coach
can suggest better questions. Nothing here sends a letter, issues a refund, or
contacts a person for you. This is useful when you want repeatable judgments
that you can inspect, test, and eventually connect to your own software.

## 2. What you need before you start

You need a Mac running **macOS**, Apple's computer operating system, and an
internet connection to install the tool. Live decisions also need a TypeSafe
account and an **API key**. This is a private access code that lets a program
use your account. Requests can charge that account. A website password and an
API key are different things.

The free demo needs no account or key. It plays an **illustrative recording**:
example answers saved in a file, with values chosen for teaching. It does not
contact Jev. Reading the guide, editing questions, and browsing saved results
also need no paid request.

A **browser** is an app such as Safari or Chrome that opens websites. These
account steps use your browser. They follow TypeSafe's official
[quickstart](https://docs.typesafe.ai/introduction/quickstart); the dashboard's
button wording may change. You can postpone them until section 5.

1. Open [the TypeSafe key dashboard](https://console.typesafe.ai/keys).

   **You should see:** a sign-in page, or your account's API key page if you
   are already signed in.

2. Sign in to your TypeSafe account, or choose its account-creation option.

   **You should see:** the account's sign-in or registration form. A new account
   may require email verification before it can continue.

3. Complete the account steps shown by TypeSafe.

   **You should see:** your account dashboard. The exact steps depend on your
   sign-in method; this workbench does not create or manage that account.

4. Return to [the API key page](https://console.typesafe.ai/keys).

   **You should see:** controls for managing keys. If the page requires an
   organization or billing setup, its instructions explain what is missing.

5. Choose the control for creating an API key.

   **You should see:** a creation form or a newly generated key. If asked for a
   name, `jevlab on my Mac` is a useful description. TypeSafe controls this form.

6. Complete the key-creation form, if one appears.

   **You should see:** the new key or a copy control. Some services show the
   full key only once.

7. Save the key in your password manager.

   **You should see:** a saved private entry that you can retrieve during setup.
   Do not put it in a document, screenshot, chat message, or terminal command.
   The workbench will store its copy in **Keychain**, macOS's password storage.

Prices and account access can change. The dashboard is the place to check your
balance and billing. A saved key does not prove it is valid or has credit.

## 3. Opening the terminal

**Terminal** is a Mac app where you give instructions by typing. A **command**
is one such instruction. A **shell** reads it; macOS normally uses a shell called
zsh. A **prompt** is the line where the shell waits for your next command. It
often ends with `%`, but the name and other text vary.

1. Press Command+Space.

   **You should see:** Spotlight, a small search box.

2. Type `Terminal` into Spotlight.

   **You should see:** the Terminal app in the search results.

3. Press Return.

   **You should see:** a window with some text and a blinking cursor. Return
   and Enter mean the same key in this guide.

4. Type this command:

   ```sh
   pwd
   ```

   **You should see:** `pwd` after the prompt. Nothing runs until you press Enter.

5. Press Enter.

   **You should see:** the full location of your current folder, then another
   prompt. The location usually contains your account name. It differs between
   computers.

A location such as this is called a **path**. The character `~` means your home
folder. The command `cd` changes the folder in which the terminal is working.
Command+C copies selected text; Command+V pastes it. **Control** is a different
key from Command. `Ctrl+E` means hold Control and press E.

From here on, **run a command** means paste the complete command and press
Enter once. Copy only the text inside the command box. Do not add a `%` or `$`.
Text scrolling past during installation is normal. An error says something
failed; ordinary progress text is not itself an error.

## 4. Installation

If `jevlab --version` already works, you can skip installation and go to section 5.
This section installs from a source folder called `jevlab` in your home folder.
**Source** means the files that make up the program. Keep this folder afterward:
an **editable installation** uses it when you launch the command.

### Get the source folder

If you already have a working source folder, keep it and skip this subsection.
For an older installation at `~/jev`, use `cd ~/jev` instead of the new folder
command below. Later example dataset paths and the local-server token command
also use `~/jevlab`; use your actual source folder in those paths. Do not replace
an existing project folder with a downloaded copy.

1. Open [the jevlab repository](https://github.com/javsanesq/jevlab) in your browser.

   **You should see:** the project's README, its introductory page, and its files.
   A **repository** is a stored project with a record of its versions. **GitHub**
   is the website hosting this repository. If the page cannot be reached,
   check your connection and the link before continuing.

2. Open the **Code** menu.

   **You should see:** download options.

3. Choose **Download ZIP**.

   **You should see:** a download called `jevlab-main.zip`. A ZIP is a compressed
   bundle of files. Safari may open it automatically.

4. Open the downloaded ZIP if it has not already opened.

   **You should see:** an extracted folder called `jevlab-main`, usually in Downloads.

5. Rename that extracted folder to `jevlab` in Finder.

   **You should see:** the folder named `jevlab`. Finder is the Mac app for folders
   and files; selecting a folder and pressing Return lets you rename it.

6. Copy the selected `jevlab` folder with Command+C.

   **You should see:** the same selected folder; copying usually has no message.

7. Press Shift+Command+H in Finder.

   **You should see:** your home folder.

8. Paste the folder with Command+V.

   **You should see:** a `jevlab` folder in your home folder. If Finder offers to
   replace an existing folder, cancel that dialog and use the existing project.

### Install the command

**uv** is a program that installs Python tools and their supporting packages.
**Python** is the language this workbench uses. You do not need to learn it to
use the screens. A **package** is a bundle of code; **dependencies** are packages
that this tool needs. uv keeps them in a separate environment for `jevlab`.

1. Run this in Terminal:

   ```sh
   uv --version
   ```

   **You should see:** a line starting with `uv` and a version number. If you
   see `command not found: uv`, continue with step 2. Otherwise skip to step 4.

2. Run the [official uv installer](https://docs.astral.sh/uv/getting-started/installation/):

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   **You should see:** download and installation messages. `curl` downloads
   the official installer; `sh` runs it. If it reports a download failure,
   restore your connection before repeating this step.

3. Run this to make the new command available in the current terminal:

   ```sh
   source "$HOME/.local/bin/env"
   ```

   **You should see:** another prompt, usually with no text. This uses the
   standard location from uv's installer. If that file is absent, follow the
   location printed by the installer, or open a new Terminal window.

4. Run:

   ```sh
   cd ~/jevlab
   ```

   **You should see:** another prompt. If it says the folder does not exist,
   finish the source-folder steps above. A successful `cd` usually prints nothing.

5. Run:

   ```sh
   uv run python scripts/install.py
   ```

   **You should see:** installation progress and, after a new installation, a
   line like this:

   ```text
   Installed 1 executable: jevlab
   ```

   uv downloads a suitable Python version if needed. A warning that `jevlab`
   already exists is a collision check, not necessarily a failure. An upgrade
   preserves installed coach packages. If another unrelated program owns that
   name, the installer may refuse; ask for help rather than deleting that program.
   When upgrading, the installer removes the earlier workbench command only
   after the new installation succeeds. From then on, type `jevlab`, not `jev`.

6. Run:

   ```sh
   uv tool update-shell
   ```

   **You should see:** a message that the tool directory is already available,
   or that your shell settings were updated. **PATH** is the list of places
   where the shell looks for commands; this step prepares that list.

7. Open a new Terminal window with Command+N.

   **You should see:** a new prompt. This lets the shell read its updated settings.

8. Run:

   ```sh
   jevlab --version
   ```

   **You should see:**

   ```text
   jevlab 0.8.0
   ```

   A later release may show a higher number. If the command is not found, see
   troubleshooting. You can launch `jevlab` from any folder after installation.

## 5. First run

The **TUI**, or terminal user interface, is the full-screen app inside Terminal.
It uses **focus**: one field or button receives your keyboard input at a time.
Tab moves forward; Shift+Tab moves back. Enter activates a focused button.
Arrow keys choose an item in a list. Long screens scroll as you move through them.
Clicking a visible field or button also works.

In the steps below, **choose** means move to a button with Tab and press Enter.
**Select** means open a list with Enter, move to an item with the arrow keys,
then confirm it with Enter. Ctrl+P opens the **command palette**, a searchable
menu of actions. Escape closes a list or dialog before leaving its screen.

1. Run:

   ```sh
   jevlab
   ```

   **You should see:** **Welcome to jevlab** on a first launch. If you already
   completed or skipped it, the home screen appears instead. The repeatable
   version of the tour is available with `jevlab tour` after leaving the app.

   This is an abbreviated sketch, not an exact screenshot:

   ```text
   +------------------------------------------------------------+
   | Welcome to jevlab                                          |
   | Jev helps you sort information and make small judgments... |
   |                                                            |
   | [Continue]                                                 |
   | [Skip tour]                                                |
   +------------------------------------------------------------+
   | Esc Skip tour   F1 Help   Ctrl+E Explain   Ctrl+G Glossary   |
   +------------------------------------------------------------+
   ```

   The top explains the current step. The bottom is the **footer**, a reminder
   of available keys. Add a key and Open free demo appear at relevant tour steps.

2. Choose **Continue**.

   **You should see:** **Step 1 of 3 / Add a key when you are ready**.

### Add a key, or continue without one

These key steps are optional for the demo. Saving a key is free; running a live
decision uses the provider's online service. A **provider** is the company running
that service, such as TypeSafe.

1. Choose **Add a key** in the tour.

   **You should see:** Settings, with **Which account is this key for?**
   and **Private access key (API key)** fields.

2. Select **TypeSafe — needed for live Jev answers** in the provider list.

   **You should see:** TypeSafe selected. It may already be the default.

3. Paste your TypeSafe key into the **Private access key (API key)** field.

   **You should see:** hidden characters instead of the key. Paste only into
   this password field, never into the text being judged.

4. Choose **Save key to Keychain**.

   **You should see:** a macOS permission request, or this status:

   ```text
   Key stored in macOS Keychain. No API call was made.
   ```

5. If macOS asks, allow this tool to access the key you are saving.

   **You should see:** a confirmation that the key was stored. If access is
   denied, the error explains the next step. The workbench does not display the key.

6. Press Escape.

   **You should see:** the tour again. This means the key was stored, not that
   a live request has succeeded. The worked example will check that later.

### See a result for free

1. Choose **Continue** in the tour.

   **You should see:** **Step 2 of 3 / Explore a free recorded example**.

2. Choose **Open free demo**.

   **You should see:** **RECORDED EXAMPLE**, a customer's refund request, and
   example answer bars. There is no charge and nothing is added to history.

3. Scroll down through the example.

   **You should see:** a team choice, an impact score, and a yes-probability for
   a refund request. Section 6 explains them. This compact sketch uses the
   recorded values; a live call can give different values.

   ```text
   RECORDED EXAMPLE - teaching values, no live call
   route
     billing       #########-   0.90
     technical     #---------   0.07
     other         ----------   0.03
   Choice: billing    confidence 0.85
   This answer meets the saved cutoff for automatic use.

   impact             0.30 on the 0 to 2 scale
   confidence         0.80 -> a person should check

   refund_requested   probability of yes 0.95
   ```

4. Press Escape.

   **You should see:** the tour again.

5. Choose **Continue**.

   **You should see:** **Step 3 of 3 / Read an answer**, with a plain explanation.

6. Choose **Finish**.

   **You should see:** the home screen and the `support-triage` design. The tour
   will not open automatically next time.

### Get help and leave safely

1. Press Ctrl+E on a focused item.

   **You should see:** what that item means and why it matters, with a Glossary
   button. This help is free and does not send your field contents anywhere.

2. Press Escape.

   **You should see:** your previous screen with focus restored.

3. Press Ctrl+G.

   **You should see:** **Words explained**, with a search field and examples.

4. Press Escape.

   **You should see:** your work again.

5. Press Ctrl+Q.

   **You should see:** the terminal prompt. From an open dialog, Ctrl+Q instead
   says to close that dialog first. Unsaved work gets a confirmation instead of
   being silently discarded.

Simple mode is the default: fewer controls appear at first. **More options**
reveals advanced controls. Expert mode shows those controls from the start.
Both modes have the same capabilities. Settings contains the mode switch.

## 6. Understanding what you are looking at

A **template** is a saved set of questions and answer rules. Think of it as
the instruction card beside the shop's letter trays. The **state** is the
information for one case: the letter itself. A **question** asks for one judgment
about that information. A **run** is one request to Jev using a template and state.

### Choice: choose one tray

Choice picks one answer from a list you supply. For example: billing, technical
support, or other. Each option has a **probability**, the model's estimated chance
for that answer, from 0.00 to 1.00. A bar at 0.90 means 90%. Longer bars mean
more probability. They do not prove that an answer is right.

### Score: place something on a described scale

Score rates one property against ordered descriptions. In our example, 0 means
no work disruption described, 1 means a problem with a stated workaround, and 2
means essential work is blocked with no stated workaround. The result can fall
between levels, such as 0.30. That is a position on the 0-to-2 scale, not 30%
and not a probability. The bars show the probability assigned to each level.

### Noul: how likely is yes?

Noul asks a yes-or-no question, such as “Does this message ask for money back?”
Its number is the probability of yes. At 0.95, Jev assigns yes a 95% probability.
At 0.50, yes and no are equally likely. That does not mean “half a refund.”
Noul has no separate confidence number.

### Probability and confidence are different

**Confidence** summarizes how strongly the probabilities concentrate around an
answer. Choice and Score return it as a number from 0.00 to 1.00. A clear winner
tends to have high confidence; competing answers tend to lower it. Confidence
0.85 does **not** promise that 85% of answers are correct. Testing real examples
with known answers is how you learn whether to trust a design.

### Automatic use and human review

A **threshold** is a cutoff saved in a template. The starter design requires
Choice and Score confidence of at least 0.85 for automatic use. Its Noul rule
uses yes at 0.90 or above, no at 0.10 or below, and review between them.
These are teaching defaults, not tested guarantees for your work.

**Automate** means the answer passed that local rule. **Human review** means a
person should check it before another program acts. `jevlab` reports this
recommendation. It does not contact anyone or carry out the proposed action.
A template without review rules sends its answers to human review.

### Time, text, and money

**Latency** is how long the request took, in milliseconds (**ms**). 1,000 ms is
one second. **Tokens** are small pieces of text the model processes; a word can
contain several. Input tokens describe text sent in; output tokens describe the
answer. **Estimated cost** is an approximation in US dollars, not your final bill.
Unknown cost means a price or usage figure could not be established; it does not
mean free.

**Get answers** starts a single paid Jev run immediately, with no price prompt.
The result shows its cost estimate based on the returned token usage. Batch and
eval runs ask before starting; each has its own **Don't ask again** choice.
Even a one-row batch or eval asks. That choice is saved only when you accept
the job. Cancelling never turns future prompts off.
Coach requests, comparisons, and lesson grading still ask before spending.
An estimate is not a hard cap: **retries**, or repeated attempts
after a temporary failure, can add cost. The advanced `--json` option asks for
results formatted for another program. **Piped input** sends one program's text
straight to another program. Those forms preserve their existing automated
rules; they do not acquire these beginner prompts.

The history screen keeps totals by day and by template. Those totals use saved
usage and prices, not the provider's final invoice.

## 7. Doing something useful: sort a refund request

This example uses the bundled `support-triage` template. **Triage** means deciding
where something should go first. The sample contains no private customer data.
It asks for a team, an impact rating, and whether money back was requested.

This section makes **one real paid TypeSafe request** when you press Ctrl+R. You
need the key from section 5. A real result is not guaranteed to match the demo.

1. Run:

   ```sh
   jevlab
   ```

   **You should see:** the home screen. If the tour is still open, Escape skips it.

2. Select `support-triage` in the template list.

   **You should see:** its row highlighted. Tab moves focus to the list; arrow
   keys move between rows.

3. Press Enter.

   **You should see:** **TRY A TEMPLATE / ask Jev about your information**.
   This playground is a screen for trying one case. Its example
   contains the following information. **JSON** is a text format with named
   fields; here `ticket.message` identifies the customer's words.

   ```json
   {
     "ticket": {
       "message": "I was charged twice for one order. Please refund the duplicate charge."
     }
   }
   ```

4. Press Ctrl+R.

   **You should see:** a running message, then the result. There is no price
   confirmation for one run. A key, billing, or connection
   problem produces an explanation and a suggested next step.

5. Read the answer named `route`.

   **You should see:** the selected team and its alternatives. Billing is the
   natural expectation for this example, but inspect what the model actually
   returned. A different answer is something to investigate, not hide.

6. Scroll to `refund_requested`.

   **You should see:** the probability of yes and the saved review recommendation.
   The message explicitly requests a refund, so yes is the expected judgment.

7. Read the time, token counts, and estimated cost.

   **You should see:** recorded details for this real request. Values will vary.
   An unknown figure is labeled rather than replaced by zero.

8. Press Ctrl+Q.

   **You should see:** the terminal prompt. The result is saved locally in
   the workbench's data folder: normally `~/.jevlab/`, or an existing `~/.jev/`
   folder kept during an upgrade. `jevlab doctor` shows which folder is active.

You have now tried a reusable decision design on a concrete case. The decision
is saved; no support ticket was sent and no payment was refunded.

## 8. Making your own template

We will create `my-message-sorter`, starting from the built-in support design.
The New screen starts with that design, not an empty page. We will make its
question more explicit and describe the billing option in our own words.
The other two questions and their example cutoffs stay visible for practice.
Saving a template is free.

In these forms, Tab finds the next field. A one-line field uses **Ctrl+Shift+A**
to select its entire contents. A multi-line writing box uses **F7** to select
all; on some Macs the key combination is **Fn+F7**. Ctrl+A moves to the start
of a line here; it does not select everything.

Each field has a name, a short explanation, and an example. Simple mode shows
that help beside the field. Expert mode keeps it under **Field help**, which
can be expanded. Invalid entries show a correction beside the field while you
edit. The Save button stays unavailable until the form is valid. Ctrl+E gives
a longer explanation of the focused field without using the text you entered.

1. Run:

   ```sh
   jevlab templates new my-message-sorter
   ```

   **You should see:** **TEMPLATE / save questions you can use again**, with
   this name filled in. If that name already exists, the command reports an
   error instead; the matching troubleshooting row explains how to open it.

2. Move focus to **What this design does** with Tab.

   **You should see:** the one-line description field highlighted.

3. Press Ctrl+Shift+A.

   **You should see:** the current description selected.

4. Type `Sort customer messages and spot refund requests.`

   **You should see:** that description in the field.

5. Move focus to the **Question to edit** list.

   **You should see:** a selector showing question names such as `route`.

6. Select `route` in that list.

   **You should see:** `route   choice` in the closed selector.

7. Choose **Edit**.

   **You should see:** a question dialog with its short name, type, wording,
   and option descriptions.

8. Move focus to **Question wording**.

   **You should see:** the multi-line question box focused.

9. Press F7, or Fn+F7 if your Mac uses F7 as a media key.

   **You should see:** all of the current question text selected.

10. Paste this wording:

    ```text
    Which team should handle the main request in ticket.message? Judge the customer's requested help, not passing mentions of another topic.
    ```

    **You should see:** that question in the writing box. `ticket.message`
    points to the customer's words in the example; it is not a command.

11. Move focus to **Answer name 1 — when to use it**, below `billing`.

    **You should see:** the first option's description field highlighted.

12. Press Ctrl+Shift+A.

    **You should see:** the existing description selected.

13. Type `Payment problems, invoices, subscription charges, or requests for money back.`

    **You should see:** the new description. These descriptions are **criteria**:
    the rules that say when each answer fits. The other options remain technical
    problems and anything outside those categories.

14. Press Ctrl+S.

    **You should see:** the question dialog close. This applies the question
    to the open design; it has not yet saved the entire template.

15. Press Ctrl+S again in the template editor.

    **You should see:** a saved-template notification and this status:

    ```text
    Saved my-message-sorter. Open Playground from Ctrl+P to try it.
    ```

    Playground is the one-case test screen; Ctrl+P opens the actions menu.

16. Press Ctrl+Q.

    **You should see:** the terminal prompt.

17. Run:

    ```sh
    jevlab
    ```

    **You should see:** `my-message-sorter` in the list. It can be tried using
    the same steps as section 7; starting another run creates another charge.

Your design is saved as a **YAML** file, a readable text format with labels and
indented lines. You do not have to edit that file yourself. Its example is still
JSON, and its cutoffs are still the starter cutoffs. Clearer wording is a design
hypothesis; testing several known examples is how you find out whether it helps.

## 9. Other features

These are optional. History and lessons are good next steps. Evals, thresholds,
batch work, export, and the local server can wait until you need them.
Each command below starts at the terminal prompt, after the full-screen app
has closed. Ctrl+Q is the app's quit shortcut.

### History: revisit a result

History keeps previous runs so you can inspect an answer and the exact questions
used. Reading a saved run is free. Trying the case again makes a new paid request
without a price prompt. **Daily totals** and **Template totals** show retained
usage, cost estimates, and the number of runs with unknown cost.

1. Run:

   ```sh
   jevlab history
   ```

   **You should see:** past results. The worked example should be there if it
   completed. **More options** exposes filters. By default, old history is cleaned
   up after 90 days or when the
   database grows past about 100 MB. The **database** is the local file storing
   runs and progress; this is not a permanent archive.

2. Move focus to the results table with Tab.

   **You should see:** a highlighted row when saved runs are present.

3. Move to the saved run with the arrow keys.

   **You should see:** that run's row highlighted.

4. Press Enter.

   **You should see:** its original answer bars and saved details. This makes
   no new request.

### Evals: test several known answers

An **eval**, short for evaluation, tests a design against cases whose correct
answers you have supplied. That collection is a **dataset**. A **label** is the
expected answer for one question. Use an eval to find recurring mistakes before
relying on a design. It makes a paid request for each case that needs running.

1. Run:

   ```sh
   jevlab eval
   ```

   **You should see:** **TEST A DESIGN / compare with known answers**, with a design selector and
   a file field. No calls start from opening it. A small sample file is included
   at `~/jevlab/examples/support-eval.jsonl`; JSONL is a file with one JSON case on
   each line. CSV, a table saved as text, is also supported.

**Check file and price** reads the file and estimates its cost without calling
Jev. **Start with Jev** shows a price prompt unless you previously turned it off
for evaluations. Its **Don't ask again before evaluations** checkbox affects
evaluations only, and saves your choice only after you accept the job.

The report shows **accuracy**, the share of correct answers. Its **More options**
view also shows a **confusion matrix**, a table of which answers were mistaken
for others. **Calibration**
checks whether stated probabilities match observed success. For example, a
collection of 0.80 predictions should be right about 80% of the time. Tiny
practice datasets cannot establish reliability for real work.

### Thresholds: decide when someone should check

The threshold tuner changes review cutoffs using a completed eval. **Coverage**
is the share of tested cases that would qualify for automatic use. The tuner
also shows accuracy among those cases. Changing a slider is free and does not
change the answers already returned by Jev.

A completed eval is required. If there are no completed rows under **SAVED JOBS**,
this section can wait until you have tested a design.

1. Run:

   ```sh
   jevlab eval
   ```

   **You should see:** **TEST A DESIGN / compare with known answers**, with
   saved evals under **SAVED JOBS**.

2. Move focus to the **SAVED JOBS** table with Tab.

   **You should see:** a highlighted job row.

3. Move to a completed job with the arrow keys.

   **You should see:** its row highlighted, with `completed` in its status.

4. Choose **Inspect**.

   **You should see:** an **EVAL** report with answer statistics.

5. Choose **Adjust review rules**.

   **You should see:** **REVIEW RULES / decide when a person should check**,
   a question selector, and a slider marked **Minimum confidence** for the
   `route` question in the support example. Other designs can start with a
   different question type.

6. Move focus to the **Minimum confidence** slider with Tab.

   **You should see:** the slider highlighted.

7. Press the Right arrow once, or Left if the cutoff is already 1.00.

   **You should see:** the cutoff change by 0.01 in that direction.
   The preview updates coverage and accuracy without another Jev call. For
   a Noul question, two probability sliders appear instead of confidence.

8. Choose **Save review rules** if you want this new cutoff.

   **You should see:** `Saved review rules in your design. Earlier results keep
   their original rules.` Saving changes future routing recommendations for
   that template. Escape instead offers to discard an unsaved change.

### Batch runs: work through a file

A **batch** applies one template to many cases. Expected answers are optional.
Use this when you want a result for every row of a file. Cost rises with the
number of requests. Large source datasets stay at their original paths.

1. Run:

   ```sh
   jevlab batch
   ```

   **You should see:** **PROCESS A FILE / run the same design on many examples**.
   The screen has fields for a design, input file, and output file, plus a
   **Check file and price** button. Opening it is free.
   It can resume interrupted jobs. A request interrupted after reaching the
   provider may already have been billed; the app explains that before retrying.

**Start with Jev** asks before processing the file, including a file with one
case. **Don't ask again before batch runs** remembers your choice for future
interactive batch runs only. It does not turn off evaluation prompts.

In terminal commands, `--yes` skips that job's prompt once. For example,
`jevlab eval run support-triage ~/jevlab/examples/support-eval.jsonl --yes` starts
a paid evaluation immediately. It does not save a preference. Scripted JSON
and piped commands keep their cost-budget checks; a saved interactive preference
does not bypass them.

The **Ask before batch runs** and **Ask before evaluations** controls in Settings
can restore the prompts. The following commands do the same without making a
paid request, after leaving the full-screen app:

1. Run:

   ```sh
   jevlab config --set confirm_batch_cost=true
   ```

   **You should see:** a settings-saved message. Future interactive batches ask
   before starting again.

2. Run:

   ```sh
   jevlab config --set confirm_eval_cost=true
   ```

   **You should see:** a settings-saved message. Future interactive evaluations
   ask before starting again.

### Learn mode: practice on small exercises

Learn mode has ten short lessons. Each explains a concept and gives you a design
to improve. Grading runs that design against small bundled datasets using real
Jev calls, so it has a cost. The coach is optional and cannot assign the grade.

1. Run:

   ```sh
   jevlab learn
   ```

   **You should see:** the lesson list and saved progress. The first lesson is
   a useful starting point. **Edit draft** changes your practice design;
   **Test my design** shows an estimate before grading. Coach feedback, if
   enabled, asks separately before another paid call.

### Coach: get suggestions about your design

The coach is a separate text-generating assistant from Anthropic or OpenAI.
It can propose a template, critique wording, or discuss a saved result. It
cannot make a Jev decision, prove why Jev chose an answer, or change a grade.
It needs its own provider key, provider package, and enabled setting. A paid
chatbot subscription does not establish that its separate API account is ready.

1. Run:

   ```sh
   jevlab doctor --coach --offline
   ```

   **You should see:** each provider's package, key source, model, and any missing
   setup. **Offline** means no live request is made. Missing setup gets a specific
   next step; optional coach setup is described in the README's coach section.

2. Run:

   ```sh
   jevlab coach
   ```

   **You should see:** the advice screen. Opening it is free. Asking for advice
   sends the selected design or case to that separate provider after confirmation.

### Export: take your design into another project

An **export** writes code that a programmer can use in another application.
It retains the template's questions and review rules. Exporting is free;
running that code can make paid calls. You can ignore this until you have a project.

1. Run:

   ```sh
   jevlab export support-triage --lang python --output ~/Desktop/jevlab-support.py
   ```

   **You should see:** a message starting `Exported support-triage to`, and a
   `jevlab-support.py` file on your Desktop. The `.py` ending means Python code.
   An existing file is not overwritten. If it already exists, keep it and choose
   a different filename when you need another export.

### Local server: let another program ask for decisions

`jevlab serve` starts a **local server**, a program waiting for requests on this
Mac. An **HTTP API** is a way for programs to send those requests. This feature
is for developers and can be ignored at first. It is not a website for visitors.
It needs a separate access **token**, a temporary secret for the local server;
that token must not be your TypeSafe key.

1. Run this to generate the local token without printing it:

   ```sh
   export JEVLAB_SERVER_TOKEN="$(uv run --project ~/jevlab python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   ```

   **You should see:** another prompt, possibly after uv environment messages.
   `export` here is a shell instruction that sets an **environment variable**:
   a named value available to programs started from this terminal. No secret
   value should appear on screen.

2. Run:

   ```sh
   jevlab serve --check
   ```

   **You should see:**

   ```text
   Local API configuration valid: http://127.0.0.1:8766
   ```

   This checks setup; it does not start the server or test the TypeSafe key.
   `127.0.0.1` means this Mac. `8766` is the **port**, a numbered connection point.

3. Run:

   ```sh
   jevlab serve
   ```

   **You should see:** a confirmation explaining that later requests can cost
   money. Starting the server is free; its total future cost is unknown.

4. Answer `y` only if you want to allow connected programs to make paid requests.

   **You should see:** a listening message for the local address. Individual
   requests do not get a new terminal prompt. Existing request budget rules
   still apply. This guide does not send a decision request to the server.

5. Press Ctrl+C when you are finished.

   **You should see:** the terminal prompt again. The server has stopped.

## 10. Troubleshooting

The normal error shape is **What happened / Why / Next**. It avoids a
**traceback**, the programmer's long record of a failure. F2 shows safe error
details inside the app. A command beginning `jevlab --verbose` includes extra safe
error **metadata**, meaning descriptive information such as a code identifying
the kind of problem. Provider failures also show the service's reason, its HTTP
status (a numbered result code), and its request ID (a reference for support),
when available. F2 includes the original response body with credentials removed.
That body can contain the information you submitted; read it before sharing it.
Older saved failures may not contain details that earlier versions discarded.

| Symptom | What it means and how to fix it |
| --- | --- |
| `command not found: jevlab` | The shell cannot find the installed command. **1.** Run `uv tool update-shell`. You should see an update or already-configured message. **2.** Open a new Terminal window. You should see a new prompt. **3.** Run `jevlab --version`. You should see its version. If no installation exists, section 4 provides the numbered installation steps. |
| `command not found: jev` after an upgrade | The workbench command has changed. **1.** Run `jevlab --version`. You should see `jevlab 0.8.0` or a later version. Use `jevlab` for the other commands in this guide. |
| Old saved work seems missing | Two data folders may exist, or an environment variable may select a separate profile. A **profile** is the folder containing this app's saved work and settings. **1.** Run `jevlab doctor`. You should see the active folder beside **Local files**. **2.** If your earlier work is in `~/.jev/`, run `JEVLAB_HOME=~/.jev jevlab`. You should see the earlier templates and history; neither folder is merged or erased. |
| `command not found: uv` | uv is absent or not on PATH. The installer steps in section 4 show how to add it and check its version. |
| `cd: no such file or directory` | The source folder is not at `~/jevlab`. Section 4's source-folder steps show where to put it. A successful `cd ~/jevlab` then returns a prompt without an error. Older installations may still be at `~/jev`. |
| A key is missing | **1.** Run `jevlab tour`. You should see the welcome screen. The numbered key-setup steps in section 5 lead to the hidden field. A coach key cannot replace a TypeSafe key. An error report must never contain a key. |
| Reading the API key timed out | The app could not finish reading Keychain before its time limit. No Jev request was sent. **1.** Unlock your login Keychain and allow the access prompt. You should be able to try the run again. |
| A key is rejected | A stored key can be expired, incomplete, or for the wrong provider. Section 2's numbered steps explain how to obtain a TypeSafe key; section 5 explains saving it. A later live request checks whether the replacement works. Single runs start without a price prompt. |
| `Unknown model: jev` | `jev` is the model family, not an API model identifier. **1.** Change the template's Model field to `jev-latest`. You should see the validation message clear. The same rule applies to the default model in Settings. |
| Billing, quota, or account-access error | The provider declined this account or model. **1.** Open that provider's account dashboard. You should see its balance, access, or billing controls and any required account steps. Repeating the same request will not repair billing. |
| Coach does not work | **1.** Run `jevlab doctor --coach --offline`. You should see the package, key, model, or setting that needs attention. **2.** Run `jevlab doctor --coach` when the offline checks are ready. You should see a price confirmation before small live checks. These are paid checks; their results distinguish success and failure for each provider. |
| No internet or a timeout | The online service could not be reached in time. **1.** Open a familiar website in your browser. You should see its page if your connection works. If it also fails, your connection needs attention. The free demo and saved results remain available. A timed-out request might already have been processed and billed. |
| Rate limit | The provider received too many requests in a period. The error's **Next** line gives the wait or request-rate change appropriate to that failure. Retrying before that wait ends can fail again. |
| Permission denied | macOS or folder permissions blocked access. **1.** Read the error's **Next** line. You should see whether the problem concerns Keychain or a file. Section 5 covers Keychain access. A file must be in a folder your account can write to, such as Desktop. `sudo` runs commands with administrator powers; it is not a general fix for this error. |
| I am stuck in an app screen | **1.** Press Escape once. You should go back or close a dialog; an unfinished edit may ask whether to discard it. **2.** Press Ctrl+Q after the dialog closes. You should see the terminal prompt, or a confirmation for other unsaved work. |
| I am stuck in the guide or server | **1.** Press `q` in the guide's usual pager, or Ctrl+C in the running server. You should see the terminal prompt. Those are different programs from the full-screen app. |
| I cannot see a button | **1.** Press Tab to move to later controls. You should see focus move and the screen scroll as needed. **2.** Choose **More options** if the control is advanced. You should see additional controls. |
| Ctrl+A did not select my text | It moves to the start of a line. **1.** Press Ctrl+Shift+A in a one-line field, or F7 in a multi-line box. You should see the whole field selected. Some Macs require Fn+F7. |
| `my-message-sorter` already exists | The earlier exercise already saved it. **1.** Run `jevlab templates edit my-message-sorter`. You should see that saved template; nothing is overwritten without editing and saving it. |
| Export says the file exists | The tool protects the existing file. **1.** Run the export command with a different output filename. You should see a new exported-file message. The earlier file remains intact. |
| Dataset changed while preparing a job | The input file changed after its price and contents were checked. No request from that preparation was sent. **1.** Restore the original file before resuming. You should see the job accept its original cases; changed cases need a new job and price check. |
| JSON or YAML is too deeply nested | The text has too many lists or objects inside one another. Your draft stays in the editor. **1.** Remove unnecessary nesting from the named field. You should see its validation message clear once the structure is readable. |
| Score contradicts its probability-weighted mean | The provider returned contradictory numbers, so the app did not accept an automated answer. **1.** Press F2 on the error. You should see safe technical details and a request ID, when available, to report to TypeSafe. |
| Result differs from the picture | Pictures use recorded teaching values. A live answer can differ. **1.** Open the saved result from history using section 9's steps. You should see its actual answer bars and saved details rather than the recording. |
| A price is unknown | The app cannot verify that model's price or usage. It is not free. In a batch, eval, or coach price prompt, **1.** Choose **Cancel this request** to avoid starting it. You should return with no new request sent. A single run starts immediately; an unknown cost afterward cannot undo that request. |
| Browser guide does not open | **1.** Run `jevlab guide`. You should see the terminal version. A reported local HTML path is another way to reach the page; HTML is the file format browsers display. |

Some fixes in this table are multi-step sequences. Each numbered action within
its cell has its own visible check. A problem that persists belongs in a support
report, not a cycle of repeated paid requests.

## 11. Glossary

These definitions cover the technical words used in this guide. Keyboard keys
such as Tab, Enter, Escape, Command, Control, Shift, and Fn are printed on the
keyboard; Fn changes the behavior of the function-key row on some Macs.

| Word | Plain meaning |
| --- | --- |
| Account | Your registration with a service, including access and billing. |
| Accuracy | The share of tested answers that match their expected labels. |
| AI / model | A trained computer system that recognizes patterns to make predictions. |
| API | A defined way for programs to ask another program for information or work. |
| API key | A private code allowing a program to use a provider account. |
| Automate / automatic use | A result met a saved rule so another program could use it without a manual check. This workbench does not execute the action. |
| Batch / job | A saved operation that processes several cases, usually from a file. |
| Billing / credit / quota | The account's charges, available balance, or allowed usage. |
| Browser | An app for websites and HTML pages, such as Safari. |
| Calibration | Comparing predicted probabilities with how often answers are actually correct. |
| Case | One item to judge, such as one customer message. |
| Choice | A question that chooses one of the named options you supplied. |
| CLI / command line | The typed-command way of using a program, rather than its full-screen interface. |
| Coach | A separate optional assistant that suggests or critiques designs. It does not supply Jev decisions. |
| Code / source | Written instructions that make a program work. |
| Command | An instruction typed at a terminal prompt. |
| Command palette | The searchable actions menu opened with Ctrl+P. |
| Confidence | A summary of how concentrated the answer probabilities are. It is not a guarantee of correctness. |
| Confusion matrix | A table showing which expected answers were confused with other answers. |
| Criteria / levels | Descriptions explaining when a Choice option or Score level fits. |
| Coverage | The share of tested cases passing the automatic-use rules. |
| CSV | A table stored as text, often exported from a spreadsheet. |
| Dashboard | A provider's account-management webpage. |
| Database / SQLite | A local organized data file; SQLite is the format used for JevLab history. |
| Dataset / label | A collection of cases; a label is the known answer supplied for testing. |
| Dependency / package / SDK | Supporting code installed for a program. An SDK is a provider's set of tools for calling its service. |
| Dialog | A smaller screen asking for information or a choice. |
| Editable installation | An installation that reads the source folder directly, so its code changes apply without copying the whole program again. |
| Environment | A set of programs and settings kept together. uv isolates the tool's packages from other projects. |
| Environment variable | A named setting passed to programs from the shell. It can contain a secret and must not be printed carelessly. |
| Eval / evaluation | Testing a template against examples with expected answers. |
| Export | Saving a design as reusable code. In shell syntax, `export` instead sets an environment variable. |
| Finder / home folder | The Mac's file browser; the personal folder represented by `~`. |
| Focus / footer | The control receiving keyboard input; the bottom line of shortcut reminders. |
| GitHub / repository | A service for hosting projects; a repository stores a project and its version history. |
| HTML | The format of a page displayed by a browser. |
| HTTP / URL | A way programs communicate over web connections; a URL is an address such as `http://127.0.0.1:8766`. |
| Human review | A recommendation that a person check an answer before it is used. Nobody is contacted automatically. |
| Input / output | Information sent into a program or model; information returned from it. |
| JSON / JSONL | A text format with named fields; JSONL stores one such case per line. |
| Keychain | macOS's built-in password storage. |
| Latency / ms | The request's elapsed time; ms means milliseconds, with 1,000 per second. |
| Local / online / offline | On this Mac; using an internet service; operating without a live service request. |
| macOS | Apple's operating system for Mac computers. |
| MB | Megabytes, a unit of storage size. The default history limit is about 100 MB. |
| Metadata | Descriptive information, such as a run's time, model, or error code. |
| Noul | A yes-or-no question returning the probability of yes, without separate confidence. |
| Package / Python | A bundle of code; the programming language used to build `jevlab`. |
| Pager | A terminal reader for long text. Space advances, `b` goes back, and `q` closes the usual reader. |
| Path / PATH | A file or folder location; uppercase PATH is the shell's list of places to find commands. |
| Piped input / script / `--json` | Advanced ways to connect programs: pass one program's text to another, run saved commands, or request machine-readable output. |
| Playground | The screen for trying one template on one case. |
| Port | A numbered connection point used by a server. |
| Probability | The model's estimated chance for an answer, between 0.00 and 1.00. |
| Profile | The folder containing JevLab's saved templates, results, and settings. Separate profiles keep separate work. |
| Prompt | Here, the shell's ready line. In AI discussions the same word can mean instructions given to a model. |
| Provider | The company running the online model service. |
| Rate limit | A limit on requests in a period of time. |
| README | A project's introductory page, with its purpose and setup information. |
| Retry / timeout | Another attempt after a failure; stopping a wait because the time limit was reached. Neither proves an earlier request was free. |
| Routing | Applying local rules to recommend automatic use or review of a returned answer. |
| Run / request | One call sending a template and a case to Jev. |
| Score | A rating along ordered, described levels. A value between levels is not a percentage. |
| Server / local server | A program waiting for requests from other programs; a local one listens only on this Mac. |
| Settings / config | The saved choices controlling how the tool behaves. |
| Shell / zsh / sh | A program that reads terminal commands; zsh and sh are shell names. |
| Simple / Expert mode | Two views of the same capabilities, with fewer or more controls shown by default. |
| State | The information for one case, such as the customer's message. |
| sudo / administrator | A command for running with higher permissions; an administrator is an account allowed to make system-wide changes. |
| Template / question | A reusable decision design; one judgment requested within it. |
| Terminal / TUI | The Mac app for commands; the full-screen interface inside that app. |
| Threshold | A cutoff for deciding whether a result needs human review. |
| Token | Usually a small piece of model text. A server access token instead means a private access code. |
| Traceback / verbose | A programmer's failure trace; a setting requesting extra diagnostic detail. jevlab's verbose mode uses safe metadata. |
| Triage | Deciding where an incoming case should go first. |
| uv | The tool that installs Python and manages this program's supporting packages. |
| YAML | A readable labeled text format used for saved templates. |
| ZIP | A compressed bundle of files, unpacked into a folder before installation. |

## 12. Where to get help

The [project README](../README.md) has developer details. TypeSafe's
[official documentation](https://docs.typesafe.ai) describes the model, and its
[quickstart](https://docs.typesafe.ai/introduction/quickstart) links to account
setup. The [confidence page](https://docs.typesafe.ai/confidence) explains the
numbers. These are provider references, not guarantees about your own design.

1. Run:

   ```sh
   jevlab doctor
   ```

   **You should see:** a local installation report. In Simple mode it begins
   like this; counts and storage sizes vary:

   ```text
   Installation check
   Saved history: healthy
   Decision designs (templates): 1 of 1 can be read
   Space used: ... MB
   ```

   It reports whether keys are present and their sources, never their values.
   This local check does not validate a key or test your internet connection.

2. Run:

   ```sh
   jevlab doctor --json
   ```

   **You should see:** a longer report starting with a structure like this:

   ```json
   {"schema_version": 1, "ok": true, "data": {}}
   ```

   This is a shape example: your `data` contains the report rather than an empty
   object. It can include local folder names and template names. Review those
   before sharing it; do not add keys, customer text, or raw history to a report.

3. Describe the failed action in [a project issue](https://github.com/javsanesq/jevlab/issues).

   **You should see:** GitHub's issue form, possibly after signing in. Include
   `jevlab --version`, the safe error message, and whether the free demo works.
   GitHub issues are public. A billing or key-access problem belongs with the
   relevant provider, rather than being solved by posting the secret publicly.

The guide itself is available locally through `jevlab guide`. It opens a **pager**,
a reader for long terminal text. Space advances, `b` goes back, and `q` returns
to the prompt with the default reader. `jevlab guide --web` opens a local HTML
copy in your browser. Neither command makes a model request. Verification notes
and the steps requiring a personal account are recorded in [TASK_C.md](TASK_C.md).
