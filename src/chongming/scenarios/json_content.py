"""One content identity rule for scenario JSON, independent of file formatting.

Canonical bytes are compact UTF-8 JSON without a BOM or trailing newline. Object
keys are sorted by Unicode code point; array order and string code points are
preserved, without Unicode normalization. Strings use Python's JSON escaping with
``ensure_ascii=False``. This is a project-specific format, not RFC 8785/JCS.

Numbers must survive conversion to binary64 and its shortest decimal spelling
without changing their exact decimal value. Integral values, regardless of input
notation, must be within ±(2**53 - 1). They serialize as integers, with all signed
zeros becoming 0; other numbers use Python's shortest float spelling. Excess
precision, overflow and underflow are rejected, never rounded to fit this rule.

Parsing rejects duplicate object keys, non-finite numbers and lone surrogates.
Content SHA-256 is the lowercase hexadecimal digest of these canonical bytes.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from typing import Never

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

MAX_SAFE_INTEGER = 2**53 - 1


def _parse_number(token: str) -> float:
    number = float(token)
    if not math.isfinite(number):
        raise ValueError("JSON numbers must be finite")
    try:
        exact = Decimal(token)
    except InvalidOperation as error:
        raise ValueError("unsupported JSON number representation") from error
    if exact != Decimal(str(number)):
        raise ValueError("JSON number loses decimal precision in binary64")
    return number


def _reject_constant(token: str) -> Never:
    raise ValueError(f"non-finite JSON number: {token}")


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _valid_string(value: str) -> str:
    value.encode("utf-8")  # Reject lone surrogates without changing any code points.
    return value


def _normalize(value: object) -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _valid_string(value)
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("JSON integer is outside the safe range ±(2**53 - 1)")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        if value.is_integer():
            if abs(value) > MAX_SAFE_INTEGER:
                raise ValueError("JSON integer is outside the safe range ±(2**53 - 1)")
            return int(value)
        return value
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {_valid_string(key): _normalize(item) for key, item in value.items()}
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def parse_json(content: bytes | str) -> JsonValue:
    """Parse strict UTF-8 JSON without silently losing number or object-key data."""
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    if not isinstance(content, str):
        raise TypeError("JSON content must be UTF-8 bytes or text")
    document: object = json.loads(
        content,
        parse_int=_parse_number,
        parse_float=_parse_number,
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_object,
    )
    return _normalize(document)


def canonical_json(value: JsonValue) -> bytes:
    """Serialize a JSON value using the single project content-identity rule."""
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def content_sha256(value: JsonValue) -> str:
    """Hash a parsed JSON value, not source-file or download bytes."""
    return hashlib.sha256(canonical_json(value)).hexdigest()
