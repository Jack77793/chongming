"""UI exchange: Python pushes state and sensor images, Godot returns queued commands."""

from __future__ import annotations

import json
import logging
import os
import shutil
import struct
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import chongming.contracts.scene as s
from chongming.contracts.model import CameraRole, PixelFormat
from chongming.preview import GodotUiSession, PreviewRuntime, ui_images
from chongming.rendering.godot import (
    GodotRenderer,
    UiImage,
    decode_ui_reply,
    encode_ui_request,
)
from chongming.scenarios.authoring import compile_authoring, demo_authoring, save_authoring
from chongming.scenarios.demo import demo_spec
from tests.test_scene_rendering import snapshot, test_scene


def _split(frame: bytes) -> dict[str, object]:
    (length,) = struct.unpack("<I", frame[:4])
    assert len(frame) == 4 + length
    document: dict[str, object] = json.loads(frame[4:])
    return document


class UiFramingTests(unittest.TestCase):
    def test_request_declares_each_image_in_trailer_order(self) -> None:
        images = (
            UiImage("station-01/main", 2, 1, bytes(6)),
            UiImage("station-01/auxiliary", 1, 1, bytes(3)),
        )
        request = _split(encode_ui_request({"version": 7, "playback": "paused"}, images))
        self.assertEqual(request["mode"], "ui")
        self.assertEqual(request["state"], {"version": 7, "playback": "paused"})
        self.assertEqual(request["binary_length"], 9)
        self.assertEqual(
            request["images"],
            [
                {"key": "station-01/main", "width": 2, "height": 1, "offset": 0, "length": 6},
                {"key": "station-01/auxiliary", "width": 1, "height": 1, "offset": 6, "length": 3},
            ],
        )

    def test_request_without_images_has_no_trailer(self) -> None:
        request = _split(encode_ui_request({"version": 0}, ()))
        self.assertEqual((request["images"], request["binary_length"]), ([], 0))

    def test_request_rejects_invalid_state_and_images(self) -> None:
        cases: dict[str, tuple[dict[str, Any], tuple[UiImage, ...], type[Exception]]] = {
            "missing version": ({}, (), TypeError),
            "boolean version": ({"version": True}, (), TypeError),
            "short pixels": ({"version": 1}, (UiImage("a", 2, 2, bytes(11)),), ValueError),
            "zero size": ({"version": 1}, (UiImage("a", 0, 2, b""),), ValueError),
            "duplicate key": ({"version": 1}, (UiImage("a", 1, 1, bytes(3)),) * 2, ValueError),
        }
        for name, (state, images, error) in cases.items():
            with self.subTest(name), self.assertRaises(error):
                encode_ui_request(state, images)

    def test_reply_returns_commands_only_when_godot_accepted_what_was_sent(self) -> None:
        commands = [{"type": "control", "action": "pause"}, {"type": "quit"}]
        reply = json.dumps(
            {"accepted": {"version": 3, "images": ["a", "b"]}, "commands": commands}
        ).encode()
        self.assertEqual(decode_ui_reply(reply, 3, ("a", "b")), commands)

    def test_reply_rejects_stale_or_malformed_answers(self) -> None:
        def reply(document: object) -> bytes:
            return json.dumps(document).encode()

        good: dict[str, Any] = {"accepted": {"version": 3, "images": ["a"]}, "commands": []}
        cases: dict[str, tuple[bytes, type[Exception]]] = {
            "not an object": (reply([]), TypeError),
            "missing accepted": (reply({"commands": []}), TypeError),
            "untyped command": (reply({**good, "commands": [{"action": "play"}]}), TypeError),
            "non-object command": (reply({**good, "commands": ["play"]}), TypeError),
            "stale version": (
                reply({**good, "accepted": {"version": 2, "images": ["a"]}}),
                RuntimeError,
            ),
            "dropped image": (
                reply({**good, "accepted": {"version": 3, "images": []}}),
                RuntimeError,
            ),
        }
        for name, (payload, error) in cases.items():
            with self.subTest(name), self.assertRaises(error):
                decode_ui_reply(payload, 3, ("a",))


@unittest.skipUnless(
    os.environ.get("CHONGMING_GODOT") or shutil.which("godot4") or shutil.which("godot"),
    "Godot 4 executable is required",
)
class UiRoundTripTests(unittest.TestCase):
    def test_godot_accepts_state_and_images_between_sensor_renders(self) -> None:
        scene = snapshot(test_scene(s.SphereGeometry(1.0)))
        camera = replace(
            scene.cameras[0],
            actual_intrinsics=replace(
                scene.cameras[0].actual_intrinsics,
                image_width_px=96,
                image_height_px=64,
                focal_x_px=65,
                focal_y_px=65,
                principal_x_px=47.5,
                principal_y_px=31.5,
            ),
        )
        renderer = GodotRenderer()
        try:
            before, _ = renderer.render(scene, camera)
            self.assertEqual(renderer.ui_exchange({"version": 1}), [])
            image = UiImage("station-01/main", 96, 64, before)
            self.assertEqual(
                renderer.ui_exchange({"version": 2, "playback": "paused"}, (image,)), []
            )
            after, _ = renderer.render(scene, camera)
            self.assertEqual(after, before, "UI exchanges must not change sensor pixels")
        finally:
            renderer.close()

    def test_full_resolution_images_reach_display_rate(self) -> None:
        # Two stations, main 1920x1080 and auxiliary 640x480: about 14 MB per exchange.
        images = tuple(
            UiImage(f"station-0{n}/{role}", w, h, bytes(w * h * 3))
            for n in (1, 2)
            for role, w, h in (("main", 1920, 1080), ("auxiliary", 640, 480))
        )
        renderer = GodotRenderer()
        try:
            renderer.ui_exchange({"version": 0}, images)
            began = time.perf_counter()
            for version in range(1, 6):
                renderer.ui_exchange({"version": version}, images)
            per_exchange_ms = (time.perf_counter() - began) * 1000 / 5
        finally:
            renderer.close()
        # The preview render clock defaults to 10 Hz; display must not be the bottleneck.
        self.assertLess(per_exchange_ms, 100.0)


def _frame(
    station: str,
    role: CameraRole,
    width: int,
    height: int,
    *,
    stride: int | None = None,
    pixel_format: PixelFormat = PixelFormat.RGB8,
) -> SimpleNamespace:
    stride = stride if stride is not None else width * 3
    pixels = bytes((y * stride + x) % 251 for y in range(height) for x in range(stride))
    return SimpleNamespace(
        station_id=station,
        camera_role=role,
        frame_id=f"{station}:{role.value}",
        image=SimpleNamespace(
            width_px=width,
            height_px=height,
            pixel_format=pixel_format,
            row_stride_bytes=stride,
            pixels=pixels,
        ),
    )


def _step(*frames: SimpleNamespace) -> s.ScenarioStep:
    # ui_images only reads step.video.frames; the rest of ScenarioStep is irrelevant here.
    return cast(s.ScenarioStep, SimpleNamespace(video=SimpleNamespace(frames=frames)))


class UiImageTests(unittest.TestCase):
    def test_selects_station_and_repacks_padded_rows(self) -> None:
        tight = _frame("station-01", CameraRole.MAIN, 3, 2)
        padded = _frame("station-01", CameraRole.AUXILIARY, 2, 2, stride=8)
        other = _frame("station-02", CameraRole.MAIN, 3, 2)
        images = ui_images(_step(tight, padded, other), "station-01")
        self.assertEqual(
            [(i.key, i.width, i.height) for i in images],
            [("station-01/main", 3, 2), ("station-01/auxiliary", 2, 2)],
        )
        self.assertEqual(images[0].pixels, tight.image.pixels)
        rows = padded.image.pixels
        self.assertEqual(images[1].pixels, rows[0:6] + rows[8:14])

    def test_rejects_formats_and_strides_the_display_cannot_show(self) -> None:
        cases = {
            "bgr": _frame("a", CameraRole.MAIN, 2, 2, pixel_format=PixelFormat.BGR8),
            "short stride": _frame("a", CameraRole.MAIN, 2, 2, stride=5),
        }
        truncated = _frame("a", CameraRole.MAIN, 2, 2)
        truncated.image.pixels = truncated.image.pixels[:-1]
        cases["truncated"] = truncated
        for name, frame in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                ui_images(_step(frame), "a")


class _FakeGodot:
    """Records what Python pushed and answers each exchange with scripted commands."""

    def __init__(self, *replies: list[dict[str, Any]], strict: bool = False) -> None:
        self.replies = list(replies)
        self.strict = strict
        self.sent: list[tuple[dict[str, Any], tuple[UiImage, ...]]] = []

    def __call__(self, state: dict[str, Any], images: tuple[UiImage, ...]) -> list[dict[str, Any]]:
        if self.strict and not self.replies:
            # Raised outside command dispatch, so it escapes run() instead of looping.
            raise AssertionError("exchange requested after the scripted replies")
        self.sent.append((state, images))
        return self.replies.pop(0) if self.replies else []


class UiSessionTests(unittest.TestCase):
    def test_controls_reach_the_runtime_and_state_reflects_them(self) -> None:
        runtime = PreviewRuntime(demo_spec(fps=2), video_enabled=False)
        godot = _FakeGodot(
            [{"type": "control", "action": "pause"}], [{"type": "control", "action": "play"}]
        )
        session = GodotUiSession(runtime, godot)
        for _ in range(3):
            self.assertTrue(session.tick())
        states = [state["playback"]["state"] for state, _ in godot.sent]
        self.assertEqual(states, ["playing", "paused", "playing"])
        self.assertEqual([state["version"] for state, _ in godot.sent], [1, 2, 3])

    def test_edit_commands_use_the_session_token_and_commit_to_disk(self) -> None:
        document = demo_authoring(stations=2, duration_s=1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.json"
            save_authoring(document, path)
            runtime = PreviewRuntime(
                compile_authoring(document),
                authoring_document=document,
                authoring_path=path,
                video_enabled=False,
            )
            godot = _FakeGodot(
                [{"type": "edit_begin"}],
                [
                    {
                        "type": "edit_update",
                        "kind": "station",
                        "id": "station-02",
                        "patch": {"position_enu_m": [30, -22, 6]},
                    }
                ],
                [{"type": "edit_commit"}],
            )
            session = GodotUiSession(runtime, godot)
            for _ in range(4):
                session.tick()
            self.assertNotIn("token", json.dumps([state for state, _ in godot.sent]))
            self.assertTrue(godot.sent[1][0]["editor"]["active"])
            self.assertFalse(godot.sent[3][0]["editor"]["active"])
            saved = json.loads(path.read_bytes())
            self.assertEqual(saved["stations"][1]["position_enu_m"], [30, -22, 6])

    def test_failed_commands_reach_the_info_panel_with_tracebacks(self) -> None:
        runtime = PreviewRuntime(demo_spec(fps=2), video_enabled=False)
        godot = _FakeGodot(
            [{"type": "control", "action": "rewind"}, {"type": "teleport"}],
            [{"type": "edit_commit"}],
            [{"type": "quit"}],
            strict=True,
        )
        package = logging.getLogger("chongming")
        level = package.level
        GodotUiSession(runtime, godot, rate_hz=120).run()
        self.assertEqual(len(godot.sent), 3, "quit must end the loop after its exchange")
        log = godot.sent[-1][0]["log"]
        self.assertEqual(
            [entry["message"] for entry in log],
            [
                "UI command control failed",
                "UI command teleport failed",
                "UI command edit_commit failed",
            ],
        )
        self.assertIn("unknown preview control", log[0]["traceback"])
        self.assertIn("edit session is not active", log[2]["traceback"])
        self.assertEqual([seq["seq"] for seq in log], [1, 2, 3])
        self.assertEqual(package.level, level)
        self.assertFalse(any(type(h).__name__ == "_UiLogBuffer" for h in package.handlers))

    def test_frames_and_images_are_sent_once_per_sequence_and_station(self) -> None:
        runtime = PreviewRuntime(demo_spec(station_count=2, fps=2), video_enabled=False)
        step = _step(
            _frame("station-01", CameraRole.MAIN, 4, 2),
            _frame("station-02", CameraRole.MAIN, 4, 2),
        )
        status = runtime.ui_view()[0]
        view: list[Any] = [status, None, None]
        godot = _FakeGodot([], [], [{"type": "select_station", "station": "station-02"}], [], [])
        session = GodotUiSession(runtime, godot)
        with patch.object(runtime, "ui_view", side_effect=lambda: tuple(view)):
            session.tick()
            view[1:] = [{"sequence": 1}, step]
            session.tick()
            session.tick()
            session.tick()
            view[1] = {"sequence": 2}
            session.tick()
        sent = [
            (state["frame_sequence"], state["frame"], [i.key for i in images])
            for state, images in godot.sent
        ]
        self.assertEqual(
            sent,
            [
                (None, None, []),
                (1, {"sequence": 1}, ["station-01/main"]),
                (1, None, []),
                (1, {"sequence": 1}, ["station-02/main"]),
                (2, {"sequence": 2}, ["station-02/main"]),
            ],
        )
        self.assertEqual(godot.sent[3][0]["station"], "station-02")

    def test_unknown_station_keeps_the_current_selection(self) -> None:
        runtime = PreviewRuntime(demo_spec(fps=2), video_enabled=False)
        godot = _FakeGodot([{"type": "select_station", "station": "station-09"}])
        session = GodotUiSession(runtime, godot)
        with self.assertLogs("chongming.preview", "WARNING") as captured:
            session.tick()
        session.tick()
        self.assertEqual(godot.sent[1][0]["station"], "station-01")
        self.assertIn("unknown station: station-09", captured.output[0])


class SeekTests(unittest.TestCase):
    def wait(self, runtime: PreviewRuntime, predicate: Any, timeout: float = 30) -> dict[str, Any]:
        with runtime._condition:
            ready = runtime._condition.wait_for(lambda: predicate(runtime.ui_view()[0]), timeout)
        status = runtime.ui_view()[0]
        self.assertTrue(ready, status)
        return status

    def test_paused_seek_lands_on_the_target_and_stays_paused(self) -> None:
        runtime = PreviewRuntime(demo_spec(fps=2), video_enabled=False)
        runtime.control("pause")
        runtime.start()
        try:
            runtime.seek(10.0)
            status = self.wait(
                runtime, lambda st: not st["seeking"] and (st["time_s"] or 0) >= 10.0
            )
            self.assertEqual(status["state"], "paused")
            timeline = runtime.spec.timeline
            self.assertEqual(
                status["duration_s"], (timeline.end_time_ns - timeline.start_time_ns) / 1e9
            )
            # The landing step is the first sample at or after the target.
            self.assertLess(status["time_s"], 10.0 + 0.5 + 1e-9)
            landed = status["time_s"]
            time.sleep(0.2)
            self.assertEqual(runtime.ui_view()[0]["time_s"], landed, "paused seek must not drift")
            runtime.seek(2.0)
            status = self.wait(runtime, lambda st: not st["seeking"] and st["time_s"] < landed)
            self.assertGreaterEqual(status["time_s"], 2.0)
        finally:
            runtime.close()

    def test_rejects_concurrent_out_of_range_and_non_numeric_seeks(self) -> None:
        runtime = PreviewRuntime(demo_spec(fps=2), video_enabled=False)
        for value in (-0.1, 24.5, float("nan"), float("inf")):
            with self.subTest(value), self.assertRaises(ValueError):
                runtime.seek(value)
        for bad in (True, "3", None):
            with self.subTest(bad), self.assertRaises(TypeError):
                runtime.seek(cast(Any, bad))
        # Not started: the first seek stays pending, so the second is rejected.
        runtime.seek(5.0)
        self.assertTrue(runtime.ui_view()[0]["seeking"])
        with self.assertRaisesRegex(ValueError, "already in progress"):
            runtime.seek(6.0)
        with self.assertRaisesRegex(ValueError, "during a seek"):
            runtime._authoring_document = {}
            runtime.begin_edit()

    def test_restart_supersedes_a_pending_seek(self) -> None:
        runtime = PreviewRuntime(demo_spec(fps=2), video_enabled=False)
        runtime.control("pause")
        runtime.seek(20.0)
        runtime.control("restart")
        self.assertFalse(runtime.ui_view()[0]["seeking"])
        runtime.start()
        try:
            runtime.control("step")
            status = self.wait(runtime, lambda st: st["time_s"] is not None)
            self.assertLess(status["time_s"], 1.0, "restart must start from the beginning")
        finally:
            runtime.close()

    def test_seek_is_refused_while_editing(self) -> None:
        document = demo_authoring(stations=1, duration_s=4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.json"
            save_authoring(document, path)
            runtime = PreviewRuntime(
                compile_authoring(document),
                authoring_document=document,
                authoring_path=path,
                video_enabled=False,
            )
            runtime.start()
            try:
                runtime.begin_edit()
                with self.assertRaisesRegex(ValueError, "locked during editing"):
                    runtime.seek(1.0)
            finally:
                runtime.close()

    def test_session_forwards_seek_and_logs_rejections(self) -> None:
        runtime = PreviewRuntime(demo_spec(fps=2), video_enabled=False)
        godot = _FakeGodot([{"type": "seek", "time_s": 3.0}, {"type": "seek", "time_s": 4.0}], [])
        session = GodotUiSession(runtime, godot)
        with self.assertLogs("chongming.preview", "WARNING") as captured:
            session.tick()
        session.tick()
        self.assertTrue(godot.sent[1][0]["playback"]["seeking"])
        self.assertIn("UI command seek failed", captured.output[0])
        self.assertIn("already in progress", captured.output[0])


@unittest.skipUnless(
    os.environ.get("CHONGMING_GODOT") or shutil.which("godot4") or shutil.which("godot"),
    "Godot 4 executable is required",
)
class UiSessionRoundTripTests(unittest.TestCase):
    def test_rendered_sensor_frames_reach_godot_through_the_session(self) -> None:
        runtime = PreviewRuntime(demo_spec(fps=2), render_fps=20)
        runtime.start()
        try:
            from chongming.rendering.godot import default_renderer

            sent: list[tuple[dict[str, Any], tuple[UiImage, ...]]] = []
            exchange = default_renderer().ui_exchange

            def recording(
                state: dict[str, Any], images: tuple[UiImage, ...]
            ) -> list[dict[str, Any]]:
                sent.append((state, images))
                return exchange(state, images)

            session = GodotUiSession(runtime, recording)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not any(images for _, images in sent):
                session.tick()
                time.sleep(0.05)
        finally:
            runtime.close()
        delivered = next(images for _, images in sent if images)
        self.assertEqual(
            {(i.key, i.width, i.height) for i in delivered},
            {("station-01/main", 1920, 1080), ("station-01/auxiliary", 640, 480)},
        )
        self.assertTrue(any(i.pixels.count(0) < len(i.pixels) for i in delivered))
        self.assertIsNone(sent[-1][0]["playback"]["error"])

    def test_window_shows_playback_seek_freeview_and_editing_without_script_errors(self) -> None:
        from chongming.rendering.godot import default_renderer

        document = demo_authoring(stations=2, duration_s=4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.json"
            save_authoring(document, path)
            runtime = PreviewRuntime(
                compile_authoring(document),
                render_fps=5,
                authoring_document=document,
                authoring_path=path,
            )
            runtime.start()
            renderer = default_renderer()
            queued: list[dict[str, Any]] = []
            sent: list[dict[str, Any]] = []

            def exchange(
                state: dict[str, Any], images: tuple[UiImage, ...]
            ) -> list[dict[str, Any]]:
                sent.append(state)
                commands = renderer.ui_exchange(state, images) + queued
                queued.clear()
                return commands

            session = GodotUiSession(runtime, exchange)

            def until(condition: Any) -> None:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    session.tick()
                    if condition(sent[-1]):
                        return
                    time.sleep(0.02)
                self.fail("window state did not reach the expected condition")

            # Commands as the window would queue them; each step waits for the
            # state the user would see before issuing the next one.
            try:
                with self.assertNoLogs("chongming", logging.WARNING):
                    until(lambda state: state["frame"] is not None)
                    queued.append({"type": "control", "action": "pause"})
                    until(lambda state: state["playback"]["state"] == "paused")
                    queued.append({"type": "seek", "time_s": 2.5})
                    until(
                        lambda state: (
                            not state["playback"]["seeking"]
                            and state["playback"]["time_s"] is not None
                            and state["playback"]["time_s"] >= 2.5
                        )
                    )
                    queued.append({"type": "select_station", "station": "station-02"})
                    queued.append({"type": "freeview", "enabled": True})
                    until(lambda state: state["editor"]["freeview"])
                    queued.append({"type": "edit_begin"})
                    until(lambda state: state["editor"]["active"])
                    queued.append(
                        {
                            "type": "edit_update",
                            "kind": "station",
                            "id": "station-02",
                            "patch": {"position_enu_m": [30, -22, 6]},
                        }
                    )
                    until(
                        lambda state: (
                            state["editor"]["document"]["stations"][1]["position_enu_m"]
                            == [30, -22, 6]
                        )
                    )
                    queued.append({"type": "edit_cancel"})
                    until(lambda state: not state["editor"]["active"])
                    queued.append({"type": "freeview", "enabled": False})
                    until(lambda state: not state["editor"]["freeview"])
                diagnostics = renderer._diagnostics()
            finally:
                runtime.close()
            self.assertEqual(sent[-1]["station"], "station-02")
            self.assertEqual(sent[-1]["playback"]["state"], "paused")
            self.assertNotIn("SCRIPT ERROR", diagnostics)


if __name__ == "__main__":
    unittest.main()
