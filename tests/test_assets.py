"""JSON content verification is strict and uses exactly the data returned."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chongming.scenarios.assets import read_verified_json
from chongming.scenarios.json_content import content_sha256, parse_json
from scripts.hash_json import main


class AssetContentTests(unittest.TestCase):
    def test_formatting_and_key_order_do_not_change_verified_content(self) -> None:
        document = parse_json('{"z":1.0,"heights_m":[[0,1.25],[2,3]]}')
        digest = content_sha256(document)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.json"
            for content in (
                b'{"z":1.0,"heights_m":[[0,1.25],[2,3]]}',
                b'{"heights_m":[[0e0,1.250],[2.0,3]], "z":1}',
                json.dumps(document, indent=2).replace("\n", "\r\n").encode() + b"\r\n",
            ):
                with self.subTest(content=content):
                    path.write_bytes(content)
                    self.assertEqual(read_verified_json(path, digest), document)

    def test_returned_data_is_the_single_value_that_was_verified(self) -> None:
        content = b'{"heights_m":[[0,1],[2,3]]}'
        value = parse_json(content)
        with (
            patch.object(Path, "read_bytes", side_effect=[content, b"changed"]) as read,
            patch("chongming.scenarios.assets.parse_json", wraps=parse_json) as parse,
            patch("chongming.scenarios.assets.content_sha256", wraps=content_sha256) as digest,
        ):
            result = read_verified_json(Path("asset.json"), content_sha256(value))
        read.assert_called_once()
        parse.assert_called_once_with(content)
        self.assertIs(digest.call_args.args[0], result)
        self.assertEqual(result, value)

    def test_changed_values_strings_and_array_order_fail_verification(self) -> None:
        original = parse_json('{"heights_m":[[0,1],[2,3]],"name":"ridge"}')
        digest = content_sha256(original)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.json"
            for content in (
                b'{"heights_m":[[0,1],[2,4]],"name":"ridge"}',
                b'{"heights_m":[[2,3],[0,1]],"name":"ridge"}',
                b'{"heights_m":[[0,1],[2,3]],"name":" ridge"}',
            ):
                with self.subTest(content=content):
                    path.write_bytes(content)
                    with self.assertRaisesRegex(ValueError, "content digest mismatch"):
                        read_verified_json(path, digest)

    def test_invalid_digest_is_rejected_before_file_access(self) -> None:
        with patch.object(Path, "read_bytes") as read:
            for digest in ("", "0" * 63, "0" * 65, "g" * 64, "A" * 64):
                with (
                    self.subTest(digest=digest),
                    self.assertRaisesRegex(ValueError, "content_sha256"),
                ):
                    read_verified_json(Path("missing.json"), digest)
        read.assert_not_called()

    def test_malformed_or_ambiguous_json_is_not_accepted_as_verified_content(self) -> None:
        digest = content_sha256(parse_json('{"heights_m":[[0,1],[2,3]]}'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.json"
            for content in (
                b"not json",
                b"\xff",
                b'{"heights_m":[],"heights_m":[[0,1],[2,3]]}',
                b'{"heights_m":[[0,1],[NaN,3]]}',
                b'{"heights_m":[[0,1],[Infinity,3]]}',
                b'{"heights_m":[[0,1],[0.10000000000000001,3]]}',
            ):
                with self.subTest(content=content):
                    path.write_bytes(content)
                    with self.assertRaises(ValueError):
                        read_verified_json(path, digest)

    def test_hashing_command_reports_content_digests_without_changing_files(self) -> None:
        content = b'{ "heights_m": [[0.0, 1], [2, 3]] }\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grid.json"
            path.write_bytes(content)
            output = io.StringIO()
            with patch("sys.argv", ["hash_json.py", str(path)]), contextlib.redirect_stdout(output):
                main()
            self.assertEqual(output.getvalue(), f"{content_sha256(parse_json(content))}  {path}\n")
            self.assertEqual(path.read_bytes(), content)

    def test_hashing_command_fails_on_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"x":1,"x":2}')
            errors = io.StringIO()
            with (
                patch("sys.argv", ["hash_json.py", str(path)]),
                contextlib.redirect_stderr(errors),
                self.assertRaises(SystemExit) as raised,
            ):
                main()
            self.assertEqual(raised.exception.code, 1)
            self.assertIn("duplicate JSON object key", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
