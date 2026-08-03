"""The tool registry the agent is allowed to call.

Every tool here is **pure and local**: no network, no filesystem writes, no
process spawning. That property is what makes the automatic-function-calling
mode in :mod:`agent` defensible -- when the model can invoke a function
without a human in the loop, the blast radius of a wrong call has to be
approximately zero.

Two things about these functions are load-bearing and easy to get wrong:

* **The docstrings are the tool spec.** The ``google-genai`` SDK builds each
  ``FunctionDeclaration`` by reflecting over the callable: the summary line
  becomes the tool description and the ``Args:`` entries become the parameter
  descriptions. A vague docstring is a vague tool, and the model will call it
  wrongly.
* **The type hints are the schema.** Parameters are annotated with plain
  scalar types so the SDK can map them onto JSON-schema primitives.

Tools signal bad input by raising :class:`ToolError`. The agent catches it
and feeds the message back to the model as a tool result, which gives the
model a chance to retry with corrected arguments instead of the whole run
crashing.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Final

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ToolError(ValueError):
    """Raised when a tool is called with input it cannot process.

    This is an expected, recoverable condition -- the agent reports it back
    to the model rather than aborting the run.
    """


# --------------------------------------------------------------------------
# Safe arithmetic
# --------------------------------------------------------------------------
#
# `eval()` on model-produced text is arbitrary code execution, full stop:
# `__import__("os").system(...)` is a one-liner away. Instead the expression
# is parsed into an AST and walked with a strict allowlist -- only numeric
# literals and a fixed set of arithmetic operators survive. Anything else
# (names, calls, attributes, subscripts, comprehensions, lambdas, f-strings,
# walrus assignments) hits the `else` branch and is rejected by node type.

_MAX_EXPRESSION_LENGTH: Final[int] = 200
_MAX_AST_NODES: Final[int] = 100
_MAX_EXPONENT: Final[float] = 64.0
_MAX_POW_BASE: Final[float] = 1e6

_BINARY_OPS: Final[dict[type[ast.operator], Callable[[float, float], float]]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: Final[dict[type[ast.unaryop], Callable[[float], float]]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _guarded_pow(base: float, exponent: float) -> float:
    """Exponentiate with bounds that stop a cheap denial-of-service.

    ``9 ** 9 ** 9`` is three tokens and will hang the process allocating an
    integer with hundreds of millions of digits, so both operands are capped
    before the operation runs.

    Args:
        base: The base operand.
        exponent: The exponent operand.

    Returns:
        ``base ** exponent``.

    Raises:
        ToolError: If either operand exceeds its safety bound.
    """
    if abs(exponent) > _MAX_EXPONENT:
        raise ToolError(f"Exponent magnitude must be at most {_MAX_EXPONENT:g}.")
    if abs(base) > _MAX_POW_BASE:
        raise ToolError(f"Exponentiation base magnitude must be at most {_MAX_POW_BASE:g}.")
    return operator.pow(base, exponent)


def _eval_node(node: ast.AST) -> float:
    """Evaluate one allowlisted AST node.

    Args:
        node: The node to evaluate.

    Returns:
        The numeric value of the node.

    Raises:
        ToolError: If the node is not an allowlisted numeric expression.
    """
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        # `bool` subclasses `int`, so it would otherwise sneak through as 0/1.
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise ToolError("Only integer and floating-point literals are allowed.")
        return float(node.value)

    if isinstance(node, ast.UnaryOp):
        unary = _UNARY_OPS.get(type(node.op))
        if unary is None:
            raise ToolError(f"Unsupported unary operator: {type(node.op).__name__}.")
        return unary(_eval_node(node.operand))

    if isinstance(node, ast.BinOp):
        binary = _BINARY_OPS.get(type(node.op))
        if binary is None:
            raise ToolError(f"Unsupported operator: {type(node.op).__name__}.")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow):
            return _guarded_pow(left, right)
        try:
            return binary(left, right)
        except ZeroDivisionError as exc:
            raise ToolError("Division by zero.") from exc

    raise ToolError(f"Disallowed expression element: {type(node).__name__}.")


def _format_number(value: float) -> str:
    """Render a float without a trailing ``.0`` on whole numbers.

    Args:
        value: The number to render.

    Returns:
        A compact string form of ``value``.
    """
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"


# --------------------------------------------------------------------------
# The tools themselves
# --------------------------------------------------------------------------


def calculate(expression: str) -> str:
    """Evaluate a basic arithmetic expression and return the numeric result.

    Supports + - * / // % and ** with parentheses over numeric literals only.
    Use this instead of doing arithmetic mentally whenever a question needs
    an exact number.

    Args:
        expression: The arithmetic expression, for example "((3 + 4) * 12) / 7".
            Variables, function calls, and any non-numeric syntax are rejected.

    Returns:
        The result of the expression as a string.

    Raises:
        ToolError: If the expression is empty, too long, malformed, or
            contains anything other than numbers and arithmetic operators.
    """
    text = expression.strip()
    if not text:
        raise ToolError("Expression is empty.")
    if len(text) > _MAX_EXPRESSION_LENGTH:
        raise ToolError(f"Expression exceeds {_MAX_EXPRESSION_LENGTH} characters.")

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"Could not parse expression: {exc.msg}.") from exc

    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > _MAX_AST_NODES:
        raise ToolError(f"Expression is too complex ({node_count} nodes).")

    return _format_number(_eval_node(tree))


# Conversion factors to each dimension's base unit (metre, kilogram, second).
_LINEAR_UNITS: Final[dict[str, tuple[str, float]]] = {
    "mm": ("length", 0.001),
    "cm": ("length", 0.01),
    "m": ("length", 1.0),
    "km": ("length", 1000.0),
    "in": ("length", 0.0254),
    "ft": ("length", 0.3048),
    "yd": ("length", 0.9144),
    "mi": ("length", 1609.344),
    "g": ("mass", 0.001),
    "kg": ("mass", 1.0),
    "lb": ("mass", 0.45359237),
    "oz": ("mass", 0.028349523125),
    "s": ("time", 1.0),
    "min": ("time", 60.0),
    "h": ("time", 3600.0),
    "day": ("time", 86400.0),
}

_TEMPERATURE_UNITS: Final[frozenset[str]] = frozenset({"c", "f", "k"})


def _to_celsius(value: float, unit: str) -> float:
    """Convert a temperature to degrees Celsius.

    Args:
        value: The temperature reading.
        unit: One of "c", "f", "k".

    Returns:
        The equivalent value in degrees Celsius.
    """
    if unit == "f":
        return (value - 32.0) * 5.0 / 9.0
    if unit == "k":
        return value - 273.15
    return value


def _from_celsius(celsius: float, unit: str) -> float:
    """Convert degrees Celsius into the requested temperature unit.

    Args:
        celsius: The temperature in degrees Celsius.
        unit: One of "c", "f", "k".

    Returns:
        The equivalent value in ``unit``.
    """
    if unit == "f":
        return celsius * 9.0 / 5.0 + 32.0
    if unit == "k":
        return celsius + 273.15
    return celsius


def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Convert a measurement between two units of the same kind.

    Handles length (mm, cm, m, km, in, ft, yd, mi), mass (g, kg, lb, oz),
    time (s, min, h, day) and temperature (c, f, k). Both units must belong
    to the same dimension -- kilometres convert to miles, but not to
    kilograms.

    Args:
        value: The numeric quantity to convert.
        from_unit: Unit the value is currently expressed in, e.g. "km".
        to_unit: Unit to convert the value into, e.g. "mi".

    Returns:
        The converted quantity followed by its unit, e.g. "3.10686 mi".

    Raises:
        ToolError: If a unit is unrecognized or the two units measure
            different dimensions.
    """
    source = from_unit.strip().lower()
    target = to_unit.strip().lower()

    if source in _TEMPERATURE_UNITS or target in _TEMPERATURE_UNITS:
        if source not in _TEMPERATURE_UNITS or target not in _TEMPERATURE_UNITS:
            raise ToolError(f"Cannot convert between {from_unit!r} and {to_unit!r}.")
        converted = _from_celsius(_to_celsius(value, source), target)
        return f"{_format_number(converted)} {target}"

    if source not in _LINEAR_UNITS:
        raise ToolError(f"Unknown unit {from_unit!r}. Known units: {known_units()}.")
    if target not in _LINEAR_UNITS:
        raise ToolError(f"Unknown unit {to_unit!r}. Known units: {known_units()}.")

    source_dimension, source_factor = _LINEAR_UNITS[source]
    target_dimension, target_factor = _LINEAR_UNITS[target]
    if source_dimension != target_dimension:
        raise ToolError(
            f"Cannot convert {source_dimension} ({from_unit!r}) "
            f"to {target_dimension} ({to_unit!r})."
        )

    return f"{_format_number(value * source_factor / target_factor)} {target}"


def known_units() -> str:
    """List every unit :func:`convert_units` understands.

    Returns:
        A comma-separated, sorted list of unit abbreviations.
    """
    return ", ".join(sorted(set(_LINEAR_UNITS) | _TEMPERATURE_UNITS))


def days_between(start_date: str, end_date: str) -> str:
    """Count the whole days between two calendar dates.

    Use this for any "how long until/since" question rather than estimating.
    The result is signed: a start date later than the end date yields a
    negative count.

    Args:
        start_date: The earlier date in ISO format, e.g. "2026-01-31".
        end_date: The later date in ISO format, e.g. "2026-08-03".

    Returns:
        A sentence stating the day count and the weekday of each date.

    Raises:
        ToolError: If either argument is not a valid ISO-8601 calendar date.
    """
    parsed: list[date] = []
    for label, raw in (("start_date", start_date), ("end_date", end_date)):
        try:
            parsed.append(date.fromisoformat(raw.strip()))
        except ValueError as exc:
            raise ToolError(f"{label} {raw!r} is not a valid ISO date (YYYY-MM-DD).") from exc

    start, end = parsed
    delta = (end - start).days
    return (
        f"{delta} days from {start.isoformat()} ({start.strftime('%A')}) "
        f"to {end.isoformat()} ({end.strftime('%A')})."
    )


# A deliberately tiny, hand-curated knowledge base. It stands in for the
# retrieval step a real agent would delegate to a vector store, without
# dragging a network dependency into a demo about tool calling.
_KNOWLEDGE_BASE: Final[dict[str, str]] = {
    "function calling": (
        "Function calling lets a model return a structured request to invoke a named "
        "function with typed arguments, instead of answering in prose. The application "
        "executes the function and returns the result for the model to summarize."
    ),
    "automatic function calling": (
        "In automatic mode the SDK is handed plain Python callables, builds their schemas "
        "by reflection, executes any function the model requests, and returns only the "
        "final natural-language answer. Convenient, but there is no authorization step."
    ),
    "manual function calling": (
        "In manual mode automatic execution is disabled, so the application receives the "
        "proposed function_call part and decides whether to run it. This is the mode to "
        "use when a tool has side effects that need auditing or approval."
    ),
    "tool allowlist": (
        "An allowlist is the guard rail that stops a model from invoking a function the "
        "application never registered. Any proposed call whose name is not in the "
        "registry is refused and the refusal is reported back to the model."
    ),
    "iteration cap": (
        "An iteration cap bounds how many model/tool round trips a single question may "
        "consume, so a model that keeps requesting tools cannot spend unbounded time or "
        "money."
    ),
}


def lookup_fact(topic: str) -> str:
    """Look up a short explanation of a term from the local knowledge base.

    The knowledge base covers agent and tool-use terminology only. Prefer
    this over guessing when asked to define one of those terms.

    Args:
        topic: The term to look up, e.g. "tool allowlist". Matching is
            case-insensitive and ignores surrounding whitespace.

    Returns:
        The explanation for ``topic``.

    Raises:
        ToolError: If the topic is not in the knowledge base. The message
            lists the available topics so the model can retry.
    """
    key = topic.strip().lower()
    if key in _KNOWLEDGE_BASE:
        return _KNOWLEDGE_BASE[key]
    raise ToolError(f"No entry for {topic!r}. Known topics: {', '.join(sorted(_KNOWLEDGE_BASE))}.")


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """One callable tool plus the metadata the agent reports on.

    Attributes:
        name: The name the model uses to request this tool. It must match
            ``func.__name__`` -- that is what the SDK puts in the schema.
        func: The pure local callable implementing the tool.
    """

    name: str
    func: Callable[..., str]

    @property
    def summary(self) -> str:
        """First line of the tool's docstring.

        Returns:
            The summary line, or an empty string if the tool has no
            docstring (which would be a bug -- see
            ``test_every_tool_has_a_docstring``).
        """
        doc = (self.func.__doc__ or "").strip()
        return doc.splitlines()[0] if doc else ""


@dataclass(frozen=True)
class ToolRegistry:
    """The set of tools an agent run is permitted to execute.

    The registry doubles as the authorization allowlist: :meth:`get` is the
    single place a tool name proposed by the model is resolved, and an
    unknown name has nowhere else to go.

    Attributes:
        specs: The registered tools, in declaration order.
    """

    specs: tuple[ToolSpec, ...]

    def __contains__(self, name: object) -> bool:
        """Whether ``name`` is a registered tool name.

        Args:
            name: Candidate tool name.

        Returns:
            True if a tool with that exact name is registered.
        """
        return any(spec.name == name for spec in self.specs)

    def __iter__(self) -> Iterator[ToolSpec]:
        """Iterate the registered tool specs.

        Yields:
            Each :class:`ToolSpec` in declaration order.
        """
        return iter(self.specs)

    def __len__(self) -> int:
        """Number of registered tools.

        Returns:
            The tool count.
        """
        return len(self.specs)

    def names(self) -> tuple[str, ...]:
        """List the registered tool names.

        Returns:
            Tool names in declaration order.
        """
        return tuple(spec.name for spec in self.specs)

    def get(self, name: str) -> ToolSpec:
        """Resolve a tool name against the allowlist.

        Args:
            name: The tool name the model proposed.

        Returns:
            The matching :class:`ToolSpec`.

        Raises:
            ToolError: If no tool with that name is registered.
        """
        for spec in self.specs:
            if spec.name == name:
                return spec
        raise ToolError(f"Unknown tool {name!r}. Registered tools: {', '.join(self.names())}.")

    def functions(self) -> list[Callable[..., str]]:
        """Return the raw callables, ready to hand to the SDK.

        Returns:
            The underlying functions in declaration order.
        """
        return [spec.func for spec in self.specs]


def build_registry() -> ToolRegistry:
    """Build the default registry of local tools.

    Returns:
        A :class:`ToolRegistry` containing the calculator, unit converter,
        date utility, and knowledge-base lookup.
    """
    return ToolRegistry(
        specs=tuple(
            ToolSpec(name=func.__name__, func=func)
            for func in (calculate, convert_units, days_between, lookup_fact)
        )
    )
