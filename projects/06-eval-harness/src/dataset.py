"""Loading and validation for the evaluation reference dataset.

The eval set is plain JSON on disk so it can be reviewed in a diff and
extended without touching code. This module is the only place that knows the
file's shape: it parses it, validates every field, and hands back frozen
:class:`EvalItem` records. A malformed file fails loudly here rather than
producing a silently wrong scorecard three layers up.

No model access and no cockpit imports live here -- this is pure I/O plus
validation, so it is trivially testable offline.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DATASET_PATH = Path(__file__).resolve().parent.parent / "data" / "eval_set.json"


class DatasetError(ValueError):
    """Raised when the evaluation dataset is missing, unparseable, or malformed."""


@dataclass(frozen=True)
class EvalItem:
    """One question/reference pair the harness scores a model against.

    Attributes:
        item_id: Stable identifier, unique within the dataset.
        question: The question to put to the model.
        reference_answer: The expected answer, used for token-overlap,
            fuzzy-similarity, and normalized-match scoring.
        required_keywords: Phrases a correct answer must mention. May be
            empty, in which case keyword coverage is not scored for the item.
    """

    item_id: str
    question: str
    reference_answer: str
    required_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalSet:
    """A named collection of :class:`EvalItem` records.

    Attributes:
        name: Human-readable name of the dataset.
        description: What the dataset covers and how it was built.
        items: The evaluation items, in file order.
    """

    name: str
    description: str = ""
    items: tuple[EvalItem, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        """Return the number of items in the set.

        Returns:
            The item count.
        """
        return len(self.items)


def _require_str(
    mapping: dict[str, Any], key: str, *, where: str, allow_blank: bool = False
) -> str:
    """Pull a required string field out of a decoded JSON object.

    Args:
        mapping: The decoded JSON object to read from.
        key: Name of the field to read.
        where: Human-readable location used in error messages.
        allow_blank: Whether an empty or whitespace-only value is acceptable.

    Returns:
        The field value.

    Raises:
        DatasetError: If the field is absent, not a string, or blank when
            blanks are not allowed.
    """
    if key not in mapping:
        raise DatasetError(f"{where}: missing required field {key!r}.")
    value = mapping[key]
    if not isinstance(value, str):
        raise DatasetError(f"{where}: field {key!r} must be a string, got {type(value).__name__}.")
    if not allow_blank and not value.strip():
        raise DatasetError(f"{where}: field {key!r} must not be blank.")
    return value


def _parse_keywords(mapping: dict[str, Any], *, where: str) -> tuple[str, ...]:
    """Parse the optional ``required_keywords`` list of an item.

    Args:
        mapping: The decoded item object.
        where: Human-readable location used in error messages.

    Returns:
        The keyword phrases, with blank entries dropped. Empty when the
        field is absent.

    Raises:
        DatasetError: If the field is present but is not a list of strings.
    """
    raw = mapping.get("required_keywords", [])
    if isinstance(raw, str) or not isinstance(raw, list):
        raise DatasetError(f"{where}: field 'required_keywords' must be a list of strings.")
    keywords: list[str] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, str):
            raise DatasetError(
                f"{where}: required_keywords[{index}] must be a string, "
                f"got {type(entry).__name__}."
            )
        if entry.strip():
            keywords.append(entry)
    return tuple(keywords)


def parse_eval_set(payload: Any) -> EvalSet:
    """Validate a decoded JSON payload and build an :class:`EvalSet`.

    Separated from file reading so callers (and tests) can validate a
    dictionary they built in memory.

    Args:
        payload: The decoded JSON value, expected to be an object with
            ``name`` and a non-empty ``items`` list.

    Returns:
        The validated :class:`EvalSet`.

    Raises:
        DatasetError: If the payload is not an object, ``items`` is missing,
            empty, or malformed, or two items share an ``item_id``.
    """
    if not isinstance(payload, dict):
        raise DatasetError(f"Dataset root must be a JSON object, got {type(payload).__name__}.")

    name = _require_str(payload, "name", where="dataset")
    description = payload.get("description", "")
    if not isinstance(description, str):
        raise DatasetError("dataset: field 'description' must be a string.")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise DatasetError("dataset: field 'items' must be a list.")
    if not raw_items:
        raise DatasetError("dataset: field 'items' must contain at least one item.")

    items: list[EvalItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        where = f"items[{index}]"
        if not isinstance(raw, dict):
            raise DatasetError(f"{where}: each item must be a JSON object.")

        item_id = _require_str(raw, "item_id", where=where)
        if item_id in seen:
            raise DatasetError(f"{where}: duplicate item_id {item_id!r}.")
        seen.add(item_id)

        items.append(
            EvalItem(
                item_id=item_id,
                question=_require_str(raw, "question", where=where),
                reference_answer=_require_str(raw, "reference_answer", where=where),
                required_keywords=_parse_keywords(raw, where=where),
            )
        )

    return EvalSet(name=name, description=description, items=tuple(items))


def load_eval_set(path: Path = DEFAULT_DATASET_PATH) -> EvalSet:
    """Load and validate the evaluation dataset from disk.

    Args:
        path: Path to the JSON dataset file.

    Returns:
        The validated :class:`EvalSet`.

    Raises:
        DatasetError: If the file is missing, is not valid JSON, or fails
            structural validation.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DatasetError(f"Evaluation dataset not found at {path}.") from exc
    except OSError as exc:
        raise DatasetError(f"Could not read evaluation dataset at {path}: {exc}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"Evaluation dataset at {path} is not valid JSON: {exc}") from exc

    return parse_eval_set(payload)


def filter_items(items: Sequence[EvalItem], limit: int | None = None) -> tuple[EvalItem, ...]:
    """Take the first ``limit`` items, for quick smoke runs.

    Args:
        items: Items to take from.
        limit: Maximum number of items to keep. ``None`` keeps all of them.

    Returns:
        The selected items, in the original order.

    Raises:
        ValueError: If ``limit`` is not positive.
    """
    if limit is None:
        return tuple(items)
    if limit <= 0:
        raise ValueError("limit must be positive.")
    return tuple(items[:limit])
