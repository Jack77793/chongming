"""Local JSON assets: read once, parse strictly, and verify the content used."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from chongming.scenarios.json_content import JsonValue, content_sha256, parse_json


def resolve_external_asset(uri: str, asset_root: Path) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme not in ("", "file") or parsed.netloc:
        raise ValueError(f"unsupported local asset URI scheme: {parsed.scheme}")
    if not parsed.path or parsed.query or parsed.fragment:
        raise ValueError("local asset URI requires a path without query or fragment")
    path = Path(unquote(parsed.path))
    return path if path.is_absolute() else asset_root / path


def read_verified_json(path: Path, expected_content_sha256: str) -> JsonValue:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_content_sha256):
        raise ValueError("asset content_sha256 must contain 64 lowercase hexadecimal characters")
    document = parse_json(path.read_bytes())
    if content_sha256(document) != expected_content_sha256:
        raise ValueError(f"external asset content digest mismatch for {path}")
    return document
