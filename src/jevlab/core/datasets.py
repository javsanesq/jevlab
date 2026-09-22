"""Strict, streaming CSV/JSONL import without copying the source dataset."""

import csv
import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, cast

from pydantic import Field
from typesafe_sdk import Choice, JSONContent, Noul, Score

from jevlab.core.errors import JevError
from jevlab.core.models import StrictModel, Template

MAX_ROWS = 10_000
MAX_ROW_BYTES = 1_048_576
MAX_FILE_BYTES = 104_857_600


class DatasetRow(StrictModel):
    id: str
    index: int
    state: JSONContent
    expected: dict[str, str | bool | int] = Field(default_factory=dict)


class DatasetInfo(StrictModel):
    path: str
    sha256: str
    format: Literal["csv", "jsonl"]
    rows: int
    labeled_rows: int


def _invalid(message: str, row: int | None = None) -> JevError:
    location = f"Row {row + 1}: " if row is not None else ""
    return JevError(
        "invalid_dataset",
        location + message,
        "Use JSONL {id?, state, expected?} or CSV id,state,expected.<question>; "
        "labels must match the template. See README dataset format.",
    )


def _path(path: Path) -> tuple[Path, Literal["csv", "jsonl"]]:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() not in {".csv", ".jsonl"}:
        raise _invalid("Dataset extension must be .csv or .jsonl.")
    try:
        if not resolved.is_file():
            raise _invalid("Dataset is not a readable regular file.")
        if resolved.stat().st_size > MAX_FILE_BYTES:
            raise _invalid("Dataset exceeds 100 MiB. Split it into smaller files.")
    except OSError:
        raise _invalid("Cannot access the dataset path. Check its permissions.") from None
    return resolved, cast(Literal["csv", "jsonl"], resolved.suffix.lower()[1:])


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not allowed.")
        result[key] = value
    return result


def _json(value: str) -> object:
    return json.loads(value, object_pairs_hook=_object)


def _rows(path: Path, format: str) -> Iterator[dict[str, object]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        # A source may grow after stat(); keep limits effective during the read.
        def lines() -> Iterator[str]:
            total_bytes = 0
            while line := source.readline(MAX_ROW_BYTES + 1):
                size = len(line.encode("utf-8"))
                total_bytes += size
                if size > MAX_ROW_BYTES:
                    raise _invalid("A line exceeds 1 MiB. Reduce the state size.")
                if total_bytes > MAX_FILE_BYTES:
                    raise _invalid("Dataset exceeds 100 MiB. Split it into smaller files.")
                yield line

        if format == "jsonl":
            for line in lines():
                data = _json(line)
                if not isinstance(data, dict):
                    raise _invalid("Every JSONL line must be an object.")
                yield cast(dict[str, object], data)
            return

        # csv.reader consumes physical lines as needed for quoted multiline states.
        # Bound every line as well as the complete decoded row below.
        csv.field_size_limit(MAX_ROW_BYTES)
        reader = csv.reader(lines(), strict=True)
        header = next(reader, None)
        if header is None or not header or len(set(header)) != len(header):
            raise _invalid("CSV needs a unique header row.")
        if "state" not in header or any(
            name not in {"state", "id"} and not name.startswith("expected.") for name in header
        ):
            raise _invalid("CSV requires state; only id and expected.<question> may accompany it.")
        for values in reader:
            if len(values) != len(header):
                raise _invalid("CSV row width differs from its header.")
            record = dict(zip(header, values, strict=True))
            row: dict[str, object] = {"state": record["state"]}
            if "id" in record:
                row["id"] = record["id"]
            row["expected"] = {
                key.removeprefix("expected."): value
                for key, value in record.items()
                if key.startswith("expected.") and value != ""
            }
            # Keep names even for empty cells so unknown label columns cannot hide.
            row["_csv_label_columns"] = [
                key.removeprefix("expected.") for key in header if key.startswith("expected.")
            ]
            yield row


def _labels(
    raw: object, template: Template, *, csv_input: bool, require_labels: bool, index: int
) -> dict[str, str | bool | int]:
    if not isinstance(raw, dict):
        raise _invalid("expected must be an object keyed by question ID.", index)
    values = cast(dict[str, object], raw)
    if set(values) - set(template.questions):
        raise _invalid("expected contains an unknown question ID.", index)
    if require_labels and set(values) != set(template.questions):
        missing = ", ".join(sorted(set(template.questions) - set(values)))
        raise _invalid(f"Evaluation requires every question label; missing: {missing}.", index)
    labels: dict[str, str | bool | int] = {}
    for key, value in values.items():
        question = template.questions[key]
        if csv_input and isinstance(value, str):
            if isinstance(question, Noul) and value in {"true", "false"}:
                value = value == "true"
            elif isinstance(question, Score) and re.fullmatch(r"0|[1-9][0-9]*", value):
                value = int(value)
        if isinstance(question, Choice):
            if not isinstance(value, str) or value not in question.criteria:
                raise _invalid(f"Label {key} must exactly match a Choice option.", index)
        elif isinstance(question, Noul):
            if type(value) is not bool:
                raise _invalid(f"Label {key} must be the boolean true or false.", index)
        elif type(value) is not int or not 0 <= cast(int, value) < len(question.criteria):
            raise _invalid(f"Label {key} must be a zero-based integer Score level.", index)
        labels[key] = cast(str | bool | int, value)
    return labels


def iter_dataset(
    path: Path, template: Template, require_labels: bool = False
) -> Iterator[DatasetRow]:
    """Yield validated rows; CSV JSON states obey template.state.format.

    Automatic IDs are ``row-1`` etc.; indexes are zero-based. Every evaluation
    question needs a label. Batch labels may be absent, but supplied labels are
    always validated. Imports are limited to 10,000 rows, 100 MiB/file, 1 MiB/row.
    """
    resolved, format = _path(path)
    seen: set[str] = set()
    count = 0
    try:
        for index, data in enumerate(_rows(resolved, format)):
            if index >= MAX_ROWS:
                raise _invalid("Dataset exceeds 10,000 rows. Split it into smaller files.")
            if format == "csv":
                columns = cast(list[str], data.pop("_csv_label_columns"))
                if set(columns) - set(template.questions):
                    raise _invalid("CSV has an unknown expected.<question> column.", index)
                if template.state.format == "json":
                    data["state"] = _json(cast(str, data["state"]))
            if set(data) - {"id", "state", "expected"}:
                raise _invalid("Unknown row fields; allowed: id, state, expected.", index)
            if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode()) > MAX_ROW_BYTES:
                raise _invalid("Row exceeds 1 MiB. Reduce the state size.", index)
            state = data.get("state")
            if not isinstance(state, (str, list, dict)) or not state:
                raise _invalid("State must be nonempty text, a JSON object or array.", index)
            if isinstance(state, str) and not state.strip():
                raise _invalid("State must not be blank.", index)
            identifier = data.get("id", f"row-{index + 1}")
            if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 128:
                raise _invalid("id must be nonempty text of at most 128 characters.", index)
            if identifier in seen:
                raise _invalid("Duplicate row id. Give each case a unique id.", index)
            seen.add(identifier)
            expected = _labels(
                data.get("expected", {}),
                template,
                csv_input=format == "csv",
                require_labels=require_labels,
                index=index,
            )
            count += 1
            yield DatasetRow(
                id=identifier, index=index, state=cast(JSONContent, state), expected=expected
            )
        if count == 0:
            raise _invalid("Dataset has no rows.")
    except JevError:
        raise
    except (OSError, UnicodeError, ValueError, csv.Error, RecursionError):
        raise _invalid(
            "Cannot decode a row. Check UTF-8, finite JSON values, unique JSON keys, "
            "CSV quoting and row size.",
            count,
        ) from None


def inspect_dataset(path: Path, template: Template, require_labels: bool = False) -> DatasetInfo:
    """Validate streaming rows and return their source path and SHA-256 digest."""
    resolved, format = _path(path)
    try:
        before = resolved.stat()
        count = labeled = 0
        for row in iter_dataset(resolved, template, require_labels):
            count += 1
            labeled += bool(row.expected)
        digest = hashlib.sha256()
        with resolved.open("rb") as source:
            total_bytes = 0
            while chunk := source.read(1_048_576):
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_BYTES:
                    raise _invalid("Dataset exceeds 100 MiB. Split it into smaller files.")
                digest.update(chunk)
        after = resolved.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise _invalid("Dataset changed during inspection. Retry with a stable source file.")
    except OSError:
        raise _invalid("Cannot read dataset. Check its path and permissions.") from None
    return DatasetInfo(
        path=str(resolved),
        sha256=digest.hexdigest(),
        format=format,
        rows=count,
        labeled_rows=labeled,
    )
