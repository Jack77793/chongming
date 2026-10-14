"""Local scenario assets, verified before their bytes are interpreted."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


def resolve_external_asset(uri: str, asset_root: Path) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme not in ("", "file") or parsed.netloc:
        raise ValueError(f"unsupported local asset URI scheme: {parsed.scheme}")
    if not parsed.path or parsed.query or parsed.fragment:
        raise ValueError("local asset URI requires a path without query or fragment")
    path = Path(unquote(parsed.path))
    return path if path.is_absolute() else asset_root / path


def read_verified_asset(path: Path, expected_digest: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise ValueError("asset sha256 must contain 64 lowercase hexadecimal characters")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_digest:
        raise ValueError(f"external asset digest mismatch for {path}")
    return content
