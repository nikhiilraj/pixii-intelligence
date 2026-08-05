"""Small, strict validator for the JSON-Schema subset used by registered prompts."""

from collections.abc import Mapping, Sequence
from typing import Any

from app.llm import LLMResponseError


def validate(value: Any, schema: Mapping[str, Any], path: str = "output") -> None:
    """Raise a recoverable model-response error when required output is incomplete.

    The prompt registry uses only object/array/string/number, required, properties,
    additionalProperties, items, enum and minLength. Refusing unsupported schema keywords
    keeps this enforcement honest instead of silently pretending to validate more.
    """
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_matches(value, kind) for kind in expected):
            raise LLMResponseError(f"{path} must be one of {expected}, got {type(value).__name__}")
    elif expected and not _matches(value, expected):
        raise LLMResponseError(f"{path} must be {expected}, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        raise LLMResponseError(f"{path} must be one of {schema['enum']}, got {value!r}")
    if isinstance(value, str) and len(value) < int(schema.get("minLength", 0)):
        raise LLMResponseError(f"{path} must not be empty")

    if isinstance(value, Mapping):
        for key in schema.get("required", []):
            if key not in value:
                raise LLMResponseError(f"{path} is missing required field {key!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties")
        for key, item in value.items():
            child = properties.get(key)
            if child is None and isinstance(additional, Mapping):
                child = additional
            if child is not None:
                validate(item, child, f"{path}.{key}")

    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                validate(item, item_schema, f"{path}[{index}]")


def _matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, Sequence) and not isinstance(value, str | bytes),
        "string": isinstance(value, str),
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)
