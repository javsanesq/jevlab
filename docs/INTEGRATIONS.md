# Portable exports

Verified on 2026-09-20 with `typesafe-sdk==0.7.0`, `langchain-core==1.6.3`, and
`pydantic-ai-slim==2.46.0`. The tests execute generated code using those actual
packages and mock only the HTTP transport. No paid calls are needed to export.

## Python

```sh
jevlab export support-triage --lang python --output decision.py
# In your own uv project:
uv add 'typesafe-sdk==0.7.0'
```

Supply `TYPESAFE_API_KEY` through your application's environment or secret manager.
Exported files do not read the workbench's Keychain, config, or SQLite database.
They contain the complete template, including descriptions, examples, and notes;
review that content before publishing the file. Export never includes stored
credentials or past run states. A destination file must be new and end in `.py`.

```python
from decision import evaluate

result = evaluate({"ticket": {"message": "I was charged twice. Please refund the duplicate."}})
route = result.routing["route"]
if route.disposition == "automate":
    print(route.value)
else:
    print("Send this ticket for human review")

print(result.response.model)
print(result.response.usage.input_tokens)
print(result.response.choices["route"].probabilities)
```

`evaluate(state)` and `await aevaluate(state)` return a typed `DecisionResult`
with the official `SystemOneResponse` and `RoutingDecision` objects. All saved
questions, structured instructions/criteria, model, and thresholds are retained.
Choice and Score compare their confidence to the saved boundary. Noul uses its
separate yes/no probability boundaries; its abstention region returns review and
no boolean verdict. Questions without thresholds always request review.

Each exported function validates state and response coverage, answer kinds,
probability distributions, Choice options, Score levels, and token counts before
routing. Malformed responses raise instead of producing automation. SDK errors
propagate to the caller. Code must still enforce business policy and authorize
side effects: an automation disposition is a confidence routing decision.

The functions send directly to `https://api.typesafe.ai` with the
[official synchronous or asynchronous SDK client](https://docs.typesafe.ai/sdk/python).
They use the saved model unless explicitly overridden with `model=...`.
Re-evaluate calibration before applying saved thresholds to a different model.
The optional `timeout` is an HTTP-operation timeout; `max_retries` defaults to two.
For an async overall deadline, wrap the call in `asyncio.timeout(...)`.
Cancellation/timeouts can leave remote completion and billing unknown. These
semantics follow the [SDK client](https://docs.typesafe.ai/sdk/python/api/clients/sync)
and [retry policy](https://docs.typesafe.ai/sdk/python/api/retries).

## LangChain

```sh
jevlab export support-triage --lang langchain --output decision_chain.py
uv add 'typesafe-sdk==0.7.0' 'langchain-core==1.6.3'
```

```python
from decision_chain import decision_runnable

result = decision_runnable.invoke({"ticket": {"message": "Please refund the duplicate charge."}})
# Async: result = await decision_runnable.ainvoke(state)
print(result.routing["route"].disposition)
```

The exported [`RunnableLambda`](https://reference.langchain.com/python/langchain-core/runnables/base/RunnableLambda)
wraps the same synchronous and asynchronous functions and works with standard
Runnable composition. Jev questions are preserved as SDK objects. No chat model,
prompt generation, or schema translation is involved.

LangChain also publishes [`langchain-typesafe`](https://github.com/langchain-ai/langchain/tree/master/libs/partners/typesafe).
Its native classifier is a valid separate integration. This export deliberately
wraps the official SDK to keep the workbench's request and validation path.

## Pydantic AI

```sh
jevlab export support-triage --lang pydantic-ai --output decision_tool.py
uv add 'typesafe-sdk==0.7.0' 'pydantic-ai-slim==2.46.0'
```

```python
from pydantic_ai import Agent
from decision_tool import decision_tool

# Supply the model and provider dependencies appropriate to your existing app.
agent = Agent(your_configured_model, tools=[decision_tool])
```

The exported [`Tool`](https://pydantic.dev/docs/ai/api/pydantic-ai/tools/) exposes
`jev_decision(state)` and returns the typed result. It does not select an agent
model or add provider credentials. Tool retries are disabled; the SDK still owns
its request retries. Applications must honor review dispositions themselves.
The host agent may choose to call a tool more than once; configure its request,
cost, and workflow limits as appropriate.

The [native TypeSafe model adapter](https://pydantic.dev/docs/ai/models/typesafe/)
maps output schemas into questions. Its [source](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/models/typesafe.py)
also derives boolean certainty and rounds some Score outputs. That serves a
different interface; these exports preserve the original rubric, continuous
Score, Noul probabilities, and routing by presenting the SDK call as a tool.

## Verification and limits

Offline tests import every export variant, run real sync/async SDK requests with
mock HTTP, check exact request bodies and threshold parity, and reject malformed
responses. They execute the LangChain Runnable and a Pydantic AI agent with a
local `FunctionModel` through the exported tool. Additional tests cover hostile
strings in structured criteria, missing thresholds, inclusive boundary values,
invalid state, and refusing existing files or symlinks. Standalone generated
modules pass Pyright as well as the workbench's own checks.

Only the receiving application needs framework packages; `jevlab` does not require
them for exporting. Imports make no inference calls. Generated modules suppress
SDK/HTTP wire logging and write no local history. Host framework tracing and
callbacks are separate facilities that can record inputs and results; configure
them deliberately before using sensitive state. No end-to-end paid agent run or
third-party tracing backend was tested. Package versions are the tested snapshot,
not a guarantee of compatibility with future releases.
