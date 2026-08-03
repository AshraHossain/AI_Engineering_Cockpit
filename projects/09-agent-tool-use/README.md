# 09 · Agent Tool Use (LLM Function Calling)

A tool-using agent built on Gemini function calling, in **both** modes the
`google-genai` SDK supports — automatic and manual — with the cockpit
frameworks wired in so every run reports what it cost, how long each tool
took, and which guard rails fired.

This is the first `projects/` example that **imports from `cockpit/`**.

## Automatic vs. manual function calling

Both modes send the same thing to the model: a set of tool schemas the SDK
derives by reflecting over your Python callables. The difference is who runs
the function the model asks for.

### Automatic

```python
config = types.GenerateContentConfig(tools=[calculate, days_between])
resp = client.models.generate_content(model="gemini-2.5-flash", contents=q, config=config)
resp.text  # already the final natural-language answer
```

You hand the SDK plain callables. It builds each `FunctionDeclaration` from
the signature, type hints and docstring, executes whatever the model
requests, feeds the result back, and returns only the final prose. One
`generate_content` call from your side; the tool round trips happen inside.

**Use it when** the tools are pure and read-only, and you care more about
brevity than control. There is no point in the loop where you can inspect,
approve, or veto a call.

### Manual

```python
config = types.GenerateContentConfig(
    tools=[calculate, days_between],
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)
resp = client.models.generate_content(...)
fc = resp.candidates[0].content.parts[0].function_call   # .name, .args
```

Disabling automatic function calling makes the SDK hand you the proposed
call instead of executing it. You then resolve the name, decide whether it
is allowed, run it, and append a `Part.from_function_response(...)` turn to
the transcript before calling the model again. Repeat until the model
answers in prose.

**Use it when** a tool has side effects — sending mail, writing to a
database, spending money, touching a customer record. Manual mode is the
only mode with a place to put an authorization check, an audit-log write, or
a human approval prompt. It is also the mode where you get a real trace: you
know exactly which tool ran with which arguments, because you ran it.

The trade-off is code. Manual mode is a loop, a transcript you maintain, and
an error path for every way a tool can fail.

### What this project does

`src/agent.py` implements both. `ToolUseAgent.run(question, mode=...)` picks
between them; the model client is **injected**, never constructed inside the
loop, which is what makes the whole thing testable offline.

Automatic mode still produces a trace here: the callables handed to the SDK
are recording wrappers (`functools.wraps` keeps the signature, annotations
and docstring identical, so the schema the SDK builds is unchanged), which
time each execution and record the arguments on the way through.

## The tools

`src/tools.py` — four pure local tools. No network, no filesystem writes, no
subprocesses. That property is what makes automatic mode defensible at all:
if the model can invoke a function with no human in the loop, the blast
radius of a wrong call needs to be roughly zero.

| Tool | What it does |
| --- | --- |
| `calculate` | Safe arithmetic over `+ - * / // % **` and parentheses |
| `convert_units` | Length, mass, time and temperature conversion |
| `days_between` | Signed day count between two ISO dates, with weekdays |
| `lookup_fact` | Canned key-value knowledge base of agent terminology |

**The docstrings are the tool spec.** The SDK turns the summary line into
the tool description and the `Args:` entries into parameter descriptions —
that is literally how the model learns what a tool does and what its
arguments mean. A vague docstring is a vague tool. `tests/test_tools.py`
asserts every tool has one, with an `Args:` section and full annotations.

## Safety and guard rails

### The calculator never evaluates model text

`eval()` on model-produced text is arbitrary code execution:
`__import__("os").system(...)` is one token away, and the model is being
steered by whatever the user typed. So `calculate` **parses** the expression
with `ast.parse(..., mode="eval")` and walks the tree against an allowlist:

* allowed nodes: `Expression`, `Constant` (int/float only), `UnaryOp`,
  `BinOp`;
* allowed operators: `Add Sub Mult Div FloorDiv Mod Pow`, `UAdd USub`;
* everything else — `Call`, `Name`, `Attribute`, `Subscript`, `Lambda`,
  comprehensions, f-strings, comparisons, boolean and bitwise ops — hits the
  final `raise` and is rejected **by node type**, not by blocklisting scary
  substrings.

`bool` is rejected explicitly, because it subclasses `int` and would
otherwise evaluate as `1`. Exponentiation is bounded on both operands, since
`9 ** 9 ** 9` is three tokens and would otherwise hang the process. There
are also length and node-count caps.

### An allowlist gates every proposed call

`ToolRegistry` is both the tool set and the authorization allowlist.
`registry.get(name)` is the single place a model-proposed name is resolved,
and an unregistered name has nowhere else to go: the call is refused, never
executed, recorded as `authorized=False`, and the refusal is fed back to the
model as a `function_response` error so it can recover.

### An iteration cap bounds the loop

The manual loop runs at most `max_iterations` model round trips. A model
that keeps asking for tools gets `StopReason.ITERATION_CAP` rather than an
unbounded spend. Automatic mode gets the same bound via
`AutomaticFunctionCallingConfig(maximum_remote_calls=...)`.

### Input and output screening

* The question is run through `cockpit.security.input_security.validate_input`
  before it reaches the model; a prompt-injection match raises
  `UnsafeInputError` and nothing is sent.
* Every tool result is screened with
  `cockpit.evaluation.safety_evaluation.detect_pii_leakage` **before** it is
  appended to the transcript. A tool result is untrusted data heading into
  the model's context and back out to the user, so a result containing what
  looks like an email address, SSN or card number is replaced with a
  redaction notice.
* The final answer is checked with `detect_refusal`, so "the model declined"
  is a reported outcome rather than something the caller has to grep for.

### Tool errors are recoverable, not fatal

A tool that raises `ToolError` (bad expression, unknown unit, malformed
date) or gets called with the wrong arguments does not abort the run. The
error text goes back to the model as `{"error": ...}`, which gives it a
chance to retry correctly.

## Cockpit instrumentation

`src/instrumented.py` is the module that reaches into `cockpit/`:

* `PerformanceTracker.measure(...)` times every model call and every tool
  execution under separate operation names (`model.generate` vs.
  `tool.calculate`), so a slow tool is distinguishable from a slow model.
  Refused calls are deliberately *not* timed — nothing ran.
* `CostTracker.record_usage(...)` prices each model call from
  `response.usage_metadata.prompt_token_count` /
  `.candidates_token_count`. An unpriced model id is not fatal; it just
  contributes 0 to the total.
* Both trackers are injected, so tests supply a `PerformanceTracker` with a
  deterministic `clock` and assert exact durations without a single `sleep`.

## Importing `cockpit/` from a subproject

The repo root is `package = false` on purpose, so it is never installed into
this project's venv. Two lines wire it up:

```toml
[tool.pytest.ini_options]
pythonpath = ["src", "../.."]
```

and, for the CLI, a `sys.path` insert at the top of `src/main.py` that runs
before any module importing `cockpit.*`. (That is why `src/main.py` carries
an `E402` per-file ignore — the imports genuinely cannot sit at the top.)

## Usage

```bash
cp .env.example .env      # add GEMINI_API_KEY, or skip it and use --dry-run
uv sync

# Offline demo -- canned client, no API key, no network.
uv run python src/main.py --dry-run
uv run python src/main.py --dry-run --manual

# Live.
uv run python src/main.py "What is 17 * 23?"
uv run python src/main.py --manual "How many days between 2026-01-01 and 2026-08-03?"
```

Output is the answer, the tool trace (which tools ran, with what arguments,
how long each took), why the loop stopped, and the cost/latency rollup:

```
Tool trace (2 call(s)):
  1. calculate(expression='17 * 23') [0.0001s]
     -> 391
  2. days_between(start_date='2026-01-01', end_date='2026-08-03') [0.0001s]
     -> 214 days from 2026-01-01 (Thursday) to 2026-08-03 (Monday).

Stopped because: completed | model round trips: 2

Cost & latency:
  model.generate           calls=2   total=0.000s mean=0.000s p95=0.000s errors=0
  tool.calculate           calls=1   total=0.000s mean=0.000s p95=0.000s errors=0
  tool.days_between        calls=1   total=0.000s mean=0.000s p95=0.000s errors=0
  tokens: in=440 out=62 | cost: $0.000287
```

Model: `gemini-2.5-flash`. The `gemini-1.5-*` ids have been retired and
return HTTP 404.

## Tests

```bash
uv run pytest
```

118 tests, no API key and no network. `src/fake_client.py` reproduces the
part of the SDK response tree the agent actually reads —
`response.candidates[0].content.parts[i].function_call` with `.name` and
`.args`, plus `response.text` and `response.usage_metadata` — and replays a
scripted list of turns. In automatic mode it goes further and *emulates* the
SDK's automatic function calling by invoking the callables it finds in
`config.tools`, which is what makes `--dry-run` a faithful demo rather than
a stub: the tools really run.

The requests the agent *builds* are genuine `google.genai.types` objects, so
the suite also pins the SDK call shape — that the manual config really
carries `automatic_function_calling.disable`, and that tool results really
are `Part.from_function_response` parts.
