"""Tests for the local tool registry.

The security-relevant tests are the calculator ones: the whole point of
parsing the expression into an AST and walking an allowlist is that
model-produced text can never reach an interpreter. Anything that would
constitute code execution is asserted to be rejected *by node type*, not by
blocklisting a few scary substrings.
"""

from __future__ import annotations

import inspect

import pytest

from tools import (
    ToolError,
    ToolRegistry,
    ToolSpec,
    build_registry,
    calculate,
    convert_units,
    days_between,
    known_units,
    lookup_fact,
)

# --------------------------------------------------------------------------
# calculate -- happy paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 1", "2"),
        ("17 * 23", "391"),
        ("10 - 4 - 3", "3"),
        ("2 + 3 * 4", "14"),
        ("(2 + 3) * 4", "20"),
        ("7 / 2", "3.5"),
        ("7 // 2", "3"),
        ("7 % 4", "3"),
        ("2 ** 10", "1024"),
        ("-5 + 2", "-3"),
        ("+5", "5"),
        ("--3", "3"),
        ("  42  ", "42"),
        ("1.5 * 4", "6"),
    ],
)
def test_calculate_evaluates_arithmetic(expression: str, expected: str) -> None:
    assert calculate(expression) == expected


def test_calculate_respects_operator_precedence() -> None:
    assert calculate("2 + 3 * 4 ** 2") == "50"


# --------------------------------------------------------------------------
# calculate -- rejections (the security bar)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malicious",
    [
        '__import__("os")',
        '__import__("os").system("echo pwned")',
        "open('/etc/passwd').read()",
        "eval('1+1')",
        "exec('x=1')",
        "().__class__.__bases__",
        "globals()",
        "x + 1",
        "print(1)",
        "lambda: 1",
        "[i for i in range(10)]",
        "{'a': 1}",
        "'a' * 5",
        'f"{1+1}"',
        "1 if True else 2",
        "1 < 2",
        "1 and 2",
        "not 1",
        "1 | 2",
        "1 << 2",
        "~1",
    ],
)
def test_calculate_rejects_non_arithmetic_input(malicious: str) -> None:
    with pytest.raises(ToolError):
        calculate(malicious)


def test_calculate_rejects_import_by_node_type_not_substring() -> None:
    """The rejection must name the AST node, proving allowlisting is the mechanism."""
    with pytest.raises(ToolError, match="Disallowed expression element: Call"):
        calculate('__import__("os")')


def test_calculate_rejects_booleans() -> None:
    # `bool` subclasses `int`, so it would silently evaluate as 1 without the
    # explicit check.
    with pytest.raises(ToolError, match="integer and floating-point"):
        calculate("True + 1")


def test_calculate_rejects_division_by_zero() -> None:
    with pytest.raises(ToolError, match="Division by zero"):
        calculate("1 / 0")


def test_calculate_rejects_runaway_exponent() -> None:
    with pytest.raises(ToolError, match="Exponent magnitude"):
        calculate("9 ** 9 ** 9")


def test_calculate_rejects_huge_exponent_base() -> None:
    with pytest.raises(ToolError, match="base magnitude"):
        calculate("99999999 ** 8")


def test_calculate_rejects_empty_expression() -> None:
    with pytest.raises(ToolError, match="empty"):
        calculate("   ")


def test_calculate_rejects_overlong_expression() -> None:
    with pytest.raises(ToolError, match="exceeds"):
        calculate("1+" * 200 + "1")


def test_calculate_rejects_unparseable_expression() -> None:
    with pytest.raises(ToolError, match="Could not parse"):
        calculate("1 +* 2")


def test_calculate_rejects_overly_complex_expression() -> None:
    # Under the 200-character limit, but over the 100-node limit.
    expression = "1+" * 60 + "1"
    assert len(expression) < 200
    with pytest.raises(ToolError, match="too complex"):
        calculate(expression)


# --------------------------------------------------------------------------
# convert_units
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "source", "target", "expected"),
    [
        (1.0, "km", "m", "1000 m"),
        (5.0, "km", "mi", "3.10686 mi"),
        (12.0, "in", "cm", "30.48 cm"),
        (2.0, "kg", "lb", "4.40925 lb"),
        (100.0, "c", "f", "212 f"),
        (32.0, "f", "c", "0 c"),
        (0.0, "c", "k", "273.15 k"),
        (90.0, "min", "h", "1.5 h"),
        (7.0, "m", "m", "7 m"),
    ],
)
def test_convert_units_converts(value: float, source: str, target: str, expected: str) -> None:
    assert convert_units(value, source, target) == expected


def test_convert_units_is_case_and_whitespace_insensitive() -> None:
    assert convert_units(1.0, " KM ", "M") == "1000 m"


def test_convert_units_rejects_unknown_source_unit() -> None:
    with pytest.raises(ToolError, match="Unknown unit 'parsec'"):
        convert_units(1.0, "parsec", "m")


def test_convert_units_rejects_unknown_target_unit() -> None:
    with pytest.raises(ToolError, match="Unknown unit 'furlong'"):
        convert_units(1.0, "m", "furlong")


def test_convert_units_rejects_dimension_mismatch() -> None:
    with pytest.raises(ToolError, match="Cannot convert length"):
        convert_units(1.0, "km", "kg")


def test_convert_units_rejects_temperature_to_length() -> None:
    with pytest.raises(ToolError, match="Cannot convert"):
        convert_units(1.0, "c", "m")


def test_known_units_lists_every_unit() -> None:
    listed = known_units()
    assert "km" in listed
    assert "c" in listed


# --------------------------------------------------------------------------
# days_between
# --------------------------------------------------------------------------


def test_days_between_counts_forward() -> None:
    result = days_between("2026-01-01", "2026-08-03")
    assert result.startswith("214 days")
    assert "Thursday" in result
    assert "Monday" in result


def test_days_between_is_signed() -> None:
    assert days_between("2026-08-03", "2026-01-01").startswith("-214 days")


def test_days_between_same_day_is_zero() -> None:
    assert days_between("2026-08-03", "2026-08-03").startswith("0 days")


@pytest.mark.parametrize("bad", ["03/08/2026", "not-a-date", "2026-13-01", ""])
def test_days_between_rejects_invalid_dates(bad: str) -> None:
    with pytest.raises(ToolError, match="not a valid ISO date"):
        days_between(bad, "2026-08-03")


def test_days_between_names_the_offending_argument() -> None:
    with pytest.raises(ToolError, match="end_date"):
        days_between("2026-01-01", "nope")


# --------------------------------------------------------------------------
# lookup_fact
# --------------------------------------------------------------------------


def test_lookup_fact_returns_an_explanation() -> None:
    assert "allowlist" in lookup_fact("tool allowlist").lower()


def test_lookup_fact_is_case_insensitive() -> None:
    assert lookup_fact("  ITERATION Cap  ") == lookup_fact("iteration cap")


def test_lookup_fact_lists_topics_on_a_miss() -> None:
    with pytest.raises(ToolError, match="Known topics:"):
        lookup_fact("quantum gravity")


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


def test_registry_exposes_all_four_tools() -> None:
    registry = build_registry()
    assert registry.names() == ("calculate", "convert_units", "days_between", "lookup_fact")
    assert len(registry) == 4


def test_registry_get_resolves_a_known_tool() -> None:
    assert build_registry().get("calculate").func is calculate


def test_registry_get_rejects_an_unknown_tool() -> None:
    with pytest.raises(ToolError, match="Unknown tool 'rm_rf'"):
        build_registry().get("rm_rf")


def test_registry_membership_is_the_allowlist() -> None:
    registry = build_registry()
    assert "calculate" in registry
    assert "rm_rf" not in registry


def test_registry_functions_are_the_raw_callables() -> None:
    assert build_registry().functions() == [calculate, convert_units, days_between, lookup_fact]


def test_registry_spec_names_match_function_names() -> None:
    # The SDK puts `func.__name__` in the schema, so a mismatch would make
    # the allowlist check reject every call the model makes.
    for spec in build_registry():
        assert spec.name == spec.func.__name__


def test_every_tool_has_a_load_bearing_docstring() -> None:
    # The docstring *is* the tool description the model sees; a missing
    # `Args:` section means the model gets no parameter descriptions.
    for spec in build_registry():
        doc = inspect.getdoc(spec.func)
        assert doc, f"{spec.name} has no docstring"
        assert "Args:" in doc, f"{spec.name} docstring has no Args section"
        assert spec.summary.endswith(".")


def test_every_tool_parameter_is_annotated() -> None:
    for spec in build_registry():
        signature = inspect.signature(spec.func)
        for parameter in signature.parameters.values():
            assert parameter.annotation is not inspect.Parameter.empty
        assert signature.return_annotation is not inspect.Parameter.empty


def test_registry_can_be_built_with_a_custom_tool_set() -> None:
    def echo(text: str) -> str:
        """Echo the text back.

        Args:
            text: Anything.

        Returns:
            The same text.
        """
        return text

    registry = ToolRegistry(specs=(ToolSpec(name="echo", func=echo),))
    assert registry.names() == ("echo",)
    assert registry.get("echo").summary == "Echo the text back."
