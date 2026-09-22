"""Plain explanations of saved facts; no model, UI, key lookup, or inference."""

import json
from dataclasses import dataclass

from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse

from jevlab.core.credentials import secure_store_description, secure_store_name
from jevlab.core.models import Run

WELCOME = (
    "Jev helps you sort information and make small, repeatable judgments, such as choosing "
    "which team should answer a customer's message. You describe what to look for, then see "
    "the answer and how sure the model is. You can explore a recorded example for free "
    "before connecting an account."
)

GLOSSARY: dict[str, str] = {
    "Choice": (
        "A question that picks one answer from a list you provide. For example, a customer's "
        "message might belong with billing, technical support, or another team. Each option "
        "gets a probability so you can see which alternatives were close."
    ),
    "Score": (
        "A question that places something on a scale whose levels you describe. For example, "
        "0 can mean no disruption, 1 a problem with a workaround, and 2 completely blocked. "
        "The result can fall between levels, such as 0.30; it is not a percentage."
    ),
    "Noul": (
        "A yes-or-no question that returns the probability of yes. For example, 0.95 means "
        "the model gives a 95% probability that a message asks for a refund. Noul has no "
        "separate confidence number; your cutoffs decide yes, no, or human review."
    ),
    "State": (
        "The information you give Jev to judge for one case. For example, the state could "
        "contain a customer's message and the product they bought. Include relevant facts; "
        "put the judgment instructions in the question."
    ),
    "Question": (
        "One judgment you ask Jev to make about the information supplied. For example, "
        "'Which team should answer this message?' is one question. Ask separate questions "
        "for separate judgments, such as team and urgency."
    ),
    "Confidence": (
        "A number from 0.00 to 1.00 that summarizes how strongly the probabilities concentrate "
        "around an answer, separate from any one option's probability. For example, 0.85 "
        "describes strong concentration; it does not mean 85% of these answers will be correct. "
        "Check results against known answers to learn when to trust them."
    ),
    "Probability": (
        "The model's estimated chance of an answer, shown from 0.00 to 1.00. For example, "
        "a billing bar at 0.90 represents a 90% probability for billing. This is an estimate, "
        "not proof that the answer is right."
    ),
    "Calibration": (
        "Checking whether the model's stated probabilities match what happens in labeled "
        "examples. For example, answers given a 0.80 probability should be right about "
        "80 times out of 100 similar cases. A chart helps reveal overconfidence."
    ),
    "Threshold": (
        "A cutoff you set to choose when an answer may be used automatically and when a "
        "person should check it. For example, you might require confidence of at least 0.90. "
        "The cutoff is your rule; it does not make an answer correct."
    ),
    "Template": (
        "A saved design you can reuse: the information needed, questions to ask, and review "
        "cutoffs. For example, a support template can judge each new customer message the "
        "same way. Saving or editing a template does not call a model."
    ),
    "Run": (
        "One request that sends a template and one case to Jev. For example, checking a "
        "single customer message creates a run with its answers, time, and estimated cost. "
        "Live runs need a key and may cost money."
    ),
    "Eval": (
        "A test of your design against a collection of cases with known answers. For example, "
        "you can check whether it routes 20 previously sorted messages correctly. Evals "
        "make real Jev calls and can help you compare accuracy and cost."
    ),
    "Token": (
        "A small piece of text the model processes; a word can contain more than one token. "
        "For example, a long message usually uses more tokens than a short one. Providers "
        "use token counts to work out many API charges."
    ),
    "Latency": (
        "How long a request took to finish, shown in milliseconds (ms). For example, 500 ms "
        "is half a second. A lower number means a quicker response, not a better answer."
    ),
    "API key": (
        "A private password that lets this tool use your provider account. For example, "
        "a TypeSafe key allows live Jev runs and charges them to that account. Enter it "
        f"only in the password field; jevlab saves it in {secure_store_description()}, "
        "not in your templates."
    ),
    "Keychain": (
        "The password storage built into macOS. For example, jevlab can retrieve your saved "
        "TypeSafe key without asking you to type it each time. Never put a key in a "
        "template, shared screenshot, or chat message."
    ),
    "Secret Service": (
        "Protected password storage on a Linux desktop, accessed through D-Bus. "
        "For example, jevlab can retrieve a saved TypeSafe key without placing it "
        "in a template. A headless session can use environment mode instead."
    ),
    "Human review": (
        "A recommendation that a person check the answer before it is used. For example, "
        "a close call between billing and technical support can be marked for review. "
        "This tool does not contact a person or send a ticket on its own."
    ),
    "Automate": (
        "An answer met the cutoff saved in your template, so your own program could use "
        "it without a manual check. For example, a clear billing case may qualify. Jev "
        "only reports this recommendation here; it does not move or send anything."
    ),
    "Dataset": (
        "A file containing many cases you want to check. For example, each row could hold "
        "one customer message and the correct team. Expected answers are needed for an "
        "eval, while a batch can run without them."
    ),
    "Batch": (
        "Running the same saved design on several cases from a file. For example, a batch "
        "can sort 100 messages and save all results in one file. Preview the cost first: "
        "each case may create a billable request."
    ),
    "Coach": (
        "An optional assistant that suggests or critiques designs and discusses results. "
        "For example, it can suggest clearer wording for a question. It uses a separate "
        "provider account and never supplies the actual Jev decision."
    ),
    "Model": (
        "The version of the AI service that reads your information. For example, a saved "
        "Jev version helps you compare results consistently. The coach's model is separate "
        "from the Jev model that makes decisions."
    ),
    "JSON": (
        "A text format for labeled information that programs can read. For example, "
        '{"message": "Please refund my order"} pairs the label message with its text. '
        "Use plain text instead when your template asks for it."
    ),
    "YAML": (
        "A text format used to save templates, with labels and indented lines. For example, "
        "a template file includes name, questions, and thresholds. The form editor can "
        "write this file for you; manual YAML editing is optional."
    ),
    "Export": (
        "Saving a working design as Python code to use in another project. For example, "
        "an app could call the exported function for each new message. Export itself is "
        "free; running that code makes live requests that may cost money."
    ),
    "Coverage": (
        "The share of tested cases that pass your automatic-use rules. For example, "
        "coverage of 60% means 60 out of 100 cases would be automated and 40 reviewed. "
        "Higher coverage does not always mean higher accuracy."
    ),
    "Accuracy": (
        "The share of answers that match the expected answers in your test cases. For "
        "example, 18 correct out of 20 is 90% accuracy. Results on a small or unrepresentative "
        "test do not guarantee the same accuracy in everyday use."
    ),
    "Confusion matrix": (
        "A table showing which expected answers were confused with which predictions. "
        "For example, it can show how often technical messages were sent to billing. "
        "It helps you find the kinds of mistakes hidden by an overall accuracy score."
    ),
    "Simple mode": (
        "A view that starts with fewer controls and explains the next step. For example, "
        "advanced settings are available when you choose to show them. Expert mode reveals "
        "more controls by default; both modes have the same capabilities."
    ),
    "Rate limit": (
        "A provider's limit on how many requests you can make in a period of time. For "
        "example, too many requests at once can cause a temporary refusal. Wait or lower "
        "the batch request rate before trying again."
    ),
}


@dataclass(frozen=True)
class Explanation:
    title: str
    body: str
    term: str | None = None


# IDs, not widget values, determine guidance: password and user text never enter help.
CONTROL_GUIDANCE: dict[str, Explanation] = {}


def _controls(ids: str, title: str, body: str, term: str | None = None) -> None:
    for control_id in ids.split():
        CONTROL_GUIDANCE[control_id] = Explanation(title, body, term)


_controls("api-key", "Your private API key", GLOSSARY["API key"], "API key")
_controls(
    "key-provider",
    "Which account is this key for?",
    "Choose TypeSafe for Jev decisions, "
    "or Anthropic/OpenAI for optional coaching. Each provider needs its own key.",
    "API key",
)
_controls(
    "save-key",
    "Save the key securely",
    "This saves the password-field contents in "
    f"{secure_store_description()} for the selected provider. It does not make a paid request.",
    secure_store_name(),
)
_controls(
    "credential-mode",
    "Where jevlab looks for keys",
    f"Protected-storage mode checks {secure_store_description()} "
    "and then an environment variable. Environment mode reads only variables set "
    "outside the app; that is an advanced setup option.",
    secure_store_name(),
)
_controls(
    "state-editor compare-state state-example",
    "Information to judge (state)",
    GLOSSARY["State"] + " Keep private keys out of this field.",
    "State",
)
_controls(
    "state-description state-guidance",
    "Describe the information needed",
    "Explain what someone should provide for each case, such as the customer's own "
    "message. This description guides the user; the actual message belongs in the state.",
    "State",
)
_controls(
    "play-format state-format compare-format",
    "Text or labeled information",
    "Choose "
    "plain text for a message or JSON for labeled information. Match the format of the "
    "text you enter so jevlab can read it.",
    "JSON",
)
_controls(
    "load-state compare-load",
    "Read a file into the form",
    "Choose a local text or JSON "
    "file to fill the information box. Loading is free; nothing is sent until you run.",
    "State",
)
_controls(
    "play-template job-template compare-left compare-right export-template templates",
    "Choose a saved design",
    GLOSSARY["Template"],
    "Template",
)
_controls(
    "template-name lesson-template",
    "Name for a saved design",
    "Use a short name with "
    "lowercase letters, numbers, hyphens, or underscores, starting with a letter. For "
    "example, my-support-test can be selected again later.",
    "Template",
)
_controls(
    "description notes",
    "A note for you",
    "Explain what this design is for or what "
    "you want to remember. These notes help you recognize it later; questions define "
    "the judgment sent to Jev.",
    "Template",
)
_controls(
    "question-id",
    "Name for this answer",
    "Give the question a short unique name, "
    "such as route or urgency. Results use this name so you can find the answer.",
    "Question",
)
_controls(
    "question-type",
    "What kind of answer do you need?",
    "Choose Choice for one of "
    "several options, Score for a described scale, or Noul for a yes-or-no probability.",
    "Question",
)
_controls(
    "instructions",
    "What should Jev judge?",
    "Ask one clear thing about the supplied "
    "information. For example: Which team should handle the customer's primary request? "
    "Avoid combining team, urgency, and refund eligibility into one question.",
    "Question",
)
_controls(
    "criteria",
    "Describe each possible answer",
    "For Choice, describe options that "
    "do not overlap. For Score, describe levels in order from lowest to highest. For "
    "Noul, describe what counts as yes and no. This field uses YAML.",
    "YAML",
)
_controls(
    "questions eval-question threshold-question",
    "Choose a question",
    "A design can "
    "ask several questions about the same case. Select the answer you want to edit or "
    "inspect, such as team or urgency.",
    "Question",
)
_controls(
    "thresholds confidence-slider no-slider yes-slider threshold-preview saved-routing",
    "When may an answer be used automatically?",
    GLOSSARY["Threshold"] + " Choice and "
    "Score use confidence; Noul uses separate yes and no probability cutoffs.",
    "Threshold",
)
_controls(
    "save-thresholds",
    "Save your review rules",
    "Save the chosen cutoffs to the "
    "template. Future runs use them; old results keep the rules used at the time. "
    "Test on new labeled cases before relying on the apparent accuracy.",
    "Threshold",
)
_controls(
    "run-jevlab job-run grade-lesson compare-run",
    "Make a paid Jev request",
    "This sends "
    "the selected design and information to TypeSafe. A single Playground run starts "
    "immediately and shows its cost afterward. An eval or batch asks first unless "
    "you saved a preference for that scope. A comparison runs twice.",
    "Run",
)
_controls(
    "cancel-run cancel-grade job-cancel compare-cancel",
    "Stop waiting for results",
    "A "
    "request already sent may still be processed and billed. Cancel stops further local "
    "work where possible, but cannot undo a provider's completed request.",
    "Run",
)
_controls(
    "inspect-design advanced-yaml yaml-editor",
    "See the saved design as text",
    "YAML "
    "is the text format for the whole template. You can use the form instead; advanced "
    "text editing is optional.",
    "YAML",
)
_controls(
    "raw-json raw-content raw-advice full-eval-report grade-report compare-raw",
    "Detailed record for advanced use",
    "This shows the full saved information in a "
    "structured format. You do not need to read it to understand the main answer. "
    "It can help with troubleshooting or connecting another program.",
    "JSON",
)
_controls(
    "result-visuals demo-result",
    "Read the answer and its uncertainty",
    "Longer bars "
    "mean higher probabilities. Confidence is a separate measure for Choice and Score. "
    "The sentence beneath an answer explains whether your rule recommends automatic "
    "use or a person's review.",
    "Probability",
)
_controls(
    "replay",
    "Try this case again",
    "Load the saved design and information into the "
    "playground. You can edit them before deciding whether to make another paid call.",
    "Run",
)
_controls(
    "history-search",
    "Find a saved run",
    "Search the words stored in earlier inputs, "
    "responses, or designs. Searching saved history is free and makes no model calls.",
    "Run",
)
_controls(
    "history-template history-model history-status",
    "Narrow the history list",
    "Show "
    "only runs with this design, model version, or completion status. Clear the filter "
    "to see the other saved runs again.",
    "Run",
)
_controls(
    "history-table jobs-table job-rows case-grades worst-misses",
    "Select a saved case",
    "Use "
    "the up and down arrows to choose a row, then open or inspect it. This reads saved "
    "data and does not repeat the paid request.",
    "Run",
)
_controls(
    "daily-totals template-totals",
    "See time and spending totals",
    "Add up recorded "
    "runs by day or by saved design. Costs are estimates; requests with unknown costs "
    "and possible provider retry charges need separate attention.",
    "Run",
)
_controls(
    "config-model template-model left-model right-model",
    "Jev model version",
    "This is the TypeSafe model used to make decisions. A pinned version is useful "
    "for comparing results. In a comparison, an empty override keeps the saved version.",
    "Model",
)
_controls(
    "coach-provider",
    "Choose an optional coach",
    "Select Anthropic or OpenAI to "
    "request design advice, or Disabled to work without a coach. Live Jev decisions "
    "still use TypeSafe. Saving this choice does not make a paid call.",
    "Coach",
)
_controls(
    "coach-anthropic-model coach-openai-model",
    "Coach model identifier",
    "Enter a "
    "model identifier supported by this provider. Each provider keeps its own setting. "
    "The coach model is separate from the Jev decision model.",
    "Model",
)
_controls(
    "coach-mode",
    "Choose the kind of advice",
    "Design proposes a template from your "
    "goal. Critique reviews a design. Explain discusses a saved result without changing "
    "its answer or inventing a new Jev decision.",
    "Coach",
)
_controls(
    "coach-intent",
    "Describe what you want to judge",
    "Explain your goal in ordinary "
    "words, such as sorting messages into the right support team. The coach proposes "
    "a design for you to review, not an answer to a real case.",
    "Coach",
)
_controls(
    "coach-target",
    "Which design or run?",
    "For a new design, enter a proposed name. "
    "For critique, use a saved template name. For explanation, use the saved run ID "
    "shown in history.",
    "Coach",
)
_controls(
    "ask-coach explain-coach",
    "Request paid advice",
    "This sends the relevant design "
    "or result to your selected coach provider. Review the estimate before confirming. "
    "The advice does not replace Jev's actual answer.",
    "Coach",
)
_controls(
    "edit-proposal",
    "Review a suggested design",
    "Open the coach's draft in the "
    "template editor. Check the wording and save it if useful; editing makes no model "
    "call.",
    "Template",
)
_controls(
    "timeout retries",
    "How long to wait and when to retry",
    "The time limit prevents "
    "waiting forever. Retries can recover from a temporary failure, but another "
    "provider request may also create another charge.",
    "Latency",
)
_controls(
    "retention-days retention-bytes",
    "How much history to keep",
    "Old history is "
    "removed when it exceeds the age or disk-size limit. Templates are kept. Preview "
    "cleanup before changing a limit if you need to retain old results.",
    "Run",
)
_controls("ui-mode", "Choose how many controls to show", GLOSSARY["Simple mode"], "Simple mode")
_controls(
    "confirm-batch-cost confirm-eval-cost remember-cost",
    "Remember spending confirmation for this scope",
    "Batch and evaluation prompts are separate preferences. Leave confirmation on to review "
    "the estimate each time, or turn it off when you are comfortable with repeated jobs. "
    "You can turn either prompt on again in Settings. Single runs never need a cost confirmation.",
    "Batch",
)
_controls("dataset-path lesson-data pattern-data", "Cases to test", GLOSSARY["Dataset"], "Dataset")
_controls(
    "lesson-fields",
    "Which facts should Jev see?",
    "These are the field names taken "
    "from each lesson case. Remove irrelevant information while keeping the facts "
    "needed for the question. Separate names with commas.",
    "State",
)
_controls(
    "job-concurrency",
    "How many cases run at once?",
    "A higher number can finish a "
    "batch sooner, but creates more simultaneous requests. Lower it if your provider "
    "refuses requests because too many are active.",
    "Batch",
)
_controls(
    "job-rate",
    "Requests per second",
    "This limits how quickly a batch starts new "
    "requests. A lower value can help stay within your provider's limits.",
    "Rate limit",
)
_controls(
    "resume-id job-resume",
    "Continue a saved batch",
    "Use an existing job ID to pick "
    "up unfinished work. Cases already completed are kept. Retrying an uncertain "
    "request can incur a second charge.",
    "Batch",
)
_controls(
    "retry-failed retry-unknown",
    "Choose whether to repeat problem cases",
    "Failed "
    "cases did not produce usable results. Uncertain cases may already have been "
    "processed by the provider, so repeating them can cost money twice.",
    "Batch",
)
_controls(
    "output-path export-path",
    "Where to save the file",
    "Enter a local file path "
    "where you have permission to write. Results may contain the original information; "
    "choose a private location if your cases are sensitive.",
    "Export",
)
_controls(
    "job-preview lesson-plan export-preview cleanup-preview",
    "Preview before acting",
    "This "
    "shows the planned local result or cost estimate. A preview does not call a model "
    "or authorize the next step.",
    "Run",
)
_controls(
    "calibration-plot calibration-table",
    "Do probabilities match reality?",
    GLOSSARY["Calibration"],
    "Calibration",
)
_controls(
    "confusion-matrix",
    "Which answers were mixed up?",
    GLOSSARY["Confusion matrix"],
    "Confusion matrix",
)
_controls("eval-metrics", "How did this design perform?", GLOSSARY["Accuracy"], "Accuracy")
_controls(
    "tune-thresholds",
    "Explore the review tradeoff",
    "Try different cutoffs and see "
    "how many cases would be automatic and how often those were correct. These are "
    "results on this test data, not a promise for future cases.",
    "Coverage",
)
_controls(
    "export-language",
    "Choose the generated code format",
    "Plain Python uses the "
    "official SDK. LangChain and Pydantic AI versions fit projects using those tools. "
    "You can ignore export until you want to connect a program.",
    "Export",
)
_controls(
    "export-code export-save", "Use this design in another project", GLOSSARY["Export"], "Export"
)
_controls(
    "cleanup-apply",
    "Remove eligible old history",
    "This deletes the records shown "
    "in the cleanup preview. It does not delete your saved templates or external "
    "datasets. Check the preview before accepting.",
    "Run",
)
_controls(
    "patterns try-pattern fork-pattern export-pattern-data",
    "Start from a worked example",
    "The "
    "library includes reusable designs and labeled practice cases. Trying or forking "
    "opens a design. Selecting Get answers then sends a live request immediately.",
    "Template",
)
_controls(
    "lesson-table open-lesson edit-draft last-attempt",
    "Learn by practicing",
    "Read "
    "the short lesson, edit a draft, and test it against known answers. Grading uses "
    "paid Jev calls; reading, editing, and inspecting saved attempts are free.",
    "Eval",
)
_controls(
    "doctor",
    "Check setup without making a paid call",
    "This checks local settings "
    "and dependencies. For a live coach check, run jevlab doctor --coach in the terminal "
    "and review its cost confirmation.",
    "API key",
)
_controls(
    "coach-settings settings save-settings",
    "Settings and account setup",
    "Choose "
    "your view, account storage, and model settings here. Saving settings is local "
    "and does not call a model.",
    "Simple mode",
)
_controls(
    "add-question edit-question apply-question remove-question move-up move-down",
    "Edit the questions in this design",
    "Add, change, remove, or reorder judgments "
    "before saving. Each question should ask one thing. These edits make no paid calls.",
    "Question",
)
_controls(
    "save-template",
    "Save your reusable design",
    "This validates the questions and "
    "writes the template locally. It does not send the state or call a model.",
    "Template",
)
_controls(
    "keep cancel cancel-question discard accept apply close",
    "Confirm or go back",
    "Read "
    "the dialog's action carefully. Escape returns without accepting it; accepting "
    "performs the action named on the button.",
)
_controls(
    "tour-next tour-key tour-demo tour-finish tour-skip demo-back",
    "Explore without spending",
    "The "
    "tour and recorded demo do not make live calls. You can skip key setup and "
    "return later with jevlab tour. Escape returns to the previous screen.",
)
_controls(
    "guidance-search glossary-list glossary-close explain-close explain-glossary",
    "Plain-language help",
    "Search or read the glossary for an explanation and "
    "example. Escape closes help and returns you to the same place.",
)

_controls(
    "home-options play-options result-options history-options settings-options editor-options "
    "job-more eval-more compare-more comparison-more export-more lesson-more coach-more "
    "grade-more library-more",
    "More controls for this screen",
    "Show or hide advanced controls without changing your saved settings. "
    "Simple mode starts with these tucked away; all capabilities remain available.",
    "Simple mode",
)
_controls(
    "job-inspect inspect-job-row compare-inspect-left compare-inspect-right inspect-miss "
    "inspect-case inspect-run",
    "Inspect the saved result",
    "Open the selected recorded case to see its inputs, answers, and review recommendation. "
    "This reads saved data and does not make another paid request.",
    "Run",
)
_controls(
    "template-search template-table",
    "Find a reusable design",
    "Search by name or description, then select a row with the arrow keys. "
    "Trying a design opens its form; editing lets you change the questions before saving.",
    "Template",
)
_controls(
    "add-criterion remove-criterion",
    "Describe an answer option",
    "Add a clear description for each Choice option, each ordered Score level, or the "
    "yes and no cases for Noul. Avoid overlapping options and vague levels.",
    "Question",
)
_controls(
    "prompt-value",
    "Enter the requested name or path",
    "The label above the field says what to enter. A name identifies something in "
    "jevlab; a path identifies a file on your computer. Enter accepts and Escape cancels.",
)
_controls(
    "close-error",
    "Return after reading the problem",
    "Close this explanation to return to your work. Nothing is retried automatically; "
    "follow the suggested next step before starting another request.",
)
_controls(
    "playground",
    "Try one case",
    "Open a saved design and enter the information "
    "to judge. You can inspect and edit before deciding to make a live call.",
    "Run",
)
_controls(
    "edit new",
    "Make or change a design",
    "Use the form to describe the information "
    "needed and the questions to ask. Editing and saving are free.",
    "Template",
)
_controls(
    "history",
    "Read past results",
    "Browse and search previous runs, then inspect "
    "answers and spending totals. This does not repeat a model request.",
    "Run",
)
_controls(
    "learn",
    "Practice with short lessons",
    "Read an example, edit a design, then optionally pay to test it against known answers.",
    "Eval",
)
_controls(
    "library",
    "Explore useful starting patterns",
    "Browse designs for common tasks "
    "and copy one to edit. Opening the library does not call a model.",
    "Template",
)
_controls(
    "demo",
    "Free recorded example",
    "Replay illustrative answers from a bundled "
    "file. No key, live call, payment, or run-history entry is involved.",
    "Probability",
)
_controls(
    "tour",
    "Repeat the welcome tour",
    "Walk through optional key setup, a free "
    "recorded example, and a plain explanation of the result. You can skip at any time.",
)
_controls("coach", "Ask for optional design advice", GLOSSARY["Coach"], "Coach")
_controls("eval", "Test a design against known answers", GLOSSARY["Eval"], "Eval")
_controls("batch", "Run a file of cases", GLOSSARY["Batch"], "Batch")
_controls(
    "compare",
    "Compare two designs",
    "Judge the same information twice with two "
    "designs or model versions. Preview and confirm the cost before sending.",
    "Run",
)
_controls("export", "Connect a design to your code", GLOSSARY["Export"], "Export")
_controls(
    "cleanup",
    "Manage saved history",
    "Preview old records that meet your retention "
    "limits before choosing to delete them. Templates and external data files are kept.",
    "Run",
)


SCREEN_GUIDANCE: dict[str, Explanation] = {
    "Home": Explanation(
        "Your saved designs",
        "Choose a template to try, create one, or "
        "open the recorded demo. Use Tab to move and Ctrl+E to explain the "
        "focused control. Nothing is sent by selecting a design.",
        "Template",
    ),
    "Playground": Explanation(
        "Try one case",
        "Choose a design, enter the information to "
        "judge, and review the estimate before running it.",
        "State",
    ),
    "TemplateEditor": Explanation(
        "Build a reusable design",
        "Describe the information "
        "needed and add one question for each judgment. Saving "
        "is free; you can test the design afterwards.",
        "Template",
    ),
    "QuestionEditor": Explanation(
        "Write one question",
        "Choose the answer type, ask one clear thing, and describe what each answer means.",
        "Question",
    ),
    "SettingsScreen": Explanation(
        "Setup and preferences",
        "Save a provider key, choose "
        "Simple or Expert mode, or adjust advanced settings. "
        "Keys belong only in the password field.",
        "API key",
    ),
    "CoachScreen": Explanation("Advice about your design", GLOSSARY["Coach"], "Coach"),
    "ResultScreen": Explanation(
        "Understand this answer",
        "Read each answer, its "
        "probabilities, and its review recommendation. An automatic "
        "recommendation does not send or change anything.",
        "Run",
    ),
    "History": Explanation(
        "Your past runs",
        "Search earlier inputs and answers, open "
        "one to inspect it, or look at spending totals. Reading is free.",
        "Run",
    ),
    "JobScreen": Explanation("Work through a file of cases", GLOSSARY["Batch"], "Batch"),
    "EvalScreen": Explanation("Check the quality of a design", GLOSSARY["Eval"], "Eval"),
    "ThresholdScreen": Explanation(
        "Choose when a person should check", GLOSSARY["Threshold"], "Threshold"
    ),
    "CompareScreen": Explanation(
        "Compare two designs",
        "Give both designs the same "
        "information to see how their answers differ. A comparison "
        "makes two paid Jev calls after confirmation.",
        "Run",
    ),
    "CompareResultScreen": Explanation(
        "Compare the recorded answers",
        "Inspect either "
        "saved run and compare its probabilities, time, and "
        "estimated cost. Reading this screen is free.",
        "Run",
    ),
    "LearnScreen": Explanation(
        "Practice with short lessons",
        "Open a lesson, edit a "
        "design, then test it on cases with known answers. Reading "
        "is free; grading uses live Jev calls.",
        "Eval",
    ),
    "LessonScreen": Explanation(
        "Your next exercise",
        "Read the concept and example, "
        "edit the draft, then preview the grading cost. You can "
        "read and edit without calling a model.",
        "Eval",
    ),
    "GradeScreen": Explanation(
        "How your exercise performed",
        "Compare the answers with "
        "the known labels. Inspect mistakes to decide what wording "
        "or input to change next.",
        "Accuracy",
    ),
    "LibraryScreen": Explanation(
        "Useful starting patterns",
        "Open or copy a design "
        "for a common task. Each includes practice cases with "
        "known answers and a note on when it is useful.",
        "Template",
    ),
    "ExportScreen": Explanation("Connect a design to code", GLOSSARY["Export"], "Export"),
    "CleanupScreen": Explanation(
        "Manage saved history",
        "Preview which old records "
        "meet your retention limits before deleting them. Your "
        "templates and external datasets are kept.",
        "Run",
    ),
    "RawScreen": Explanation(
        "Full saved details",
        "This structured text is useful for "
        "troubleshooting or programming. Return to the result screen "
        "for the plain explanation and bars.",
        "JSON",
    ),
    "TourScreen": Explanation("Welcome tour", WELCOME),
    "DemoScreen": Explanation(
        "Recorded teaching example",
        "These illustrative answers "
        "are loaded from a bundled file. They were authored to teach "
        "the display; no model or account is contacted.",
        "Probability",
    ),
}


def explain_control(control_id: str | None, screen_name: str) -> Explanation:
    """Explain semantics using identifiers only, never user-entered text or secrets."""
    if control_id and control_id in CONTROL_GUIDANCE:
        return CONTROL_GUIDANCE[control_id]
    if control_id and control_id.startswith("criterion-name-"):
        return Explanation(
            "Name one possible answer",
            "Use a short, distinct answer label, such as billing or technical. The answer "
            "description explains when it applies; avoid options that mean the same thing.",
            "Choice",
        )
    if control_id and control_id.startswith("criterion-description-"):
        return Explanation(
            "Explain when this answer applies",
            "Describe concrete evidence for this option or level. For example, billing "
            "covers charges and refunds; technical covers a malfunctioning feature. "
            "For a scale, keep levels ordered and specific.",
            "Question",
        )
    if control_id and control_id.startswith("probability-"):
        return Explanation("Predicted probabilities", GLOSSARY["Probability"], "Probability")
    if control_id and control_id.startswith("confidence-"):
        return Explanation("How sure was the answer?", GLOSSARY["Confidence"], "Confidence")
    if control_id and control_id.startswith("routing-curve-"):
        return Explanation("Automatic use versus review", GLOSSARY["Coverage"], "Coverage")
    return SCREEN_GUIDANCE.get(
        screen_name,
        Explanation(
            "Move, inspect, and return",
            "Tab moves to the next control and "
            "Shift+Tab moves back. Press Ctrl+E on a control to learn what it does; "
            "Ctrl+G opens the glossary. Escape closes a dialog or returns a screen.",
        ),
    )


def explain_answer(run: Run, question_name: str) -> str:
    """Describe only recorded answer and routing fields, never infer a new decision."""
    if run.status != "succeeded" or run.response is None:
        return "There is no completed answer to explain. Check the failure details before retrying."
    response = SystemOneResponse.model_validate_json(json.dumps(run.response))
    answer = response.answers.get(question_name)
    if answer is None:
        return "This run has no recorded answer for that question."
    routing = (run.routing or {}).get(question_name)
    decision = routing if isinstance(routing, dict) else {}
    automated = decision.get("disposition") == "automate"
    if isinstance(answer, ChoiceAnswer):
        text = (
            f"Jev chose {answer.choice}. Its confidence is {answer.confidence:.2f} "
            "(how strongly its probabilities point toward an answer); this is separate "
            "from the option probabilities above."
        )
    elif isinstance(answer, ScoreAnswer):
        levels = sorted(answer.legend)
        scale = f" on the {levels[0]}–{levels[-1]} scale" if levels else " on your scale"
        text = (
            f"Jev gave a score of {answer.score:.2f}{scale}, with "
            f"confidence {answer.confidence:.2f} "
            "(how tightly its probabilities cluster on the scale)."
        )
    elif isinstance(answer, NoulAnswer):
        text = (
            f"Jev gives yes a {answer.noul:.2%} probability. "
            "This question type has no separate confidence number."
        )
        if automated:
            routed = decision.get("value")
            if isinstance(routed, bool):
                text += f" Your cutoffs interpret this as {'yes' if routed else 'no'}."
    else:
        return "This answer type has no plain explanation yet; inspect the saved details."
    text += (
        " Under the saved settings, this case qualifies for automatic use."
        if automated
        else " Under the saved settings, a person should check this answer before it is used."
    )
    return text + " This is a recommendation; jevlab has not sent or changed anything."
