"""Local scene video preview, independent of unfinished online algorithms.

A single producer owns ScenarioSource. Browsers only read an atomic latest
snapshot; their polling/render rate never creates exposures or advances time.
Video encodings carry only ordinary CameraFrame pixels. Privileged annotations
are a separate monitor field and are drawn solely in the development UI.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import threading
import time
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from chongming.contracts.model import CameraFrame
from chongming.contracts.scene import ScenarioSpec, ScenarioStep
from chongming.scenarios.demo import demo_source, demo_spec

logger = logging.getLogger(__name__)


def encode_video(frame: CameraFrame) -> str:
    """Lossless PNG transport of the ordinary gray8 plane, without annotations."""
    image = Image.frombytes(
        "L",
        (frame.image.width_px, frame.image.height_px),
        bytes(frame.image.pixels),
        "raw",
        "L",
        frame.image.row_stride_bytes,
    )
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=1)
    return base64.b64encode(output.getvalue()).decode("ascii")


def preview_payload(step: ScenarioStep, sequence: int, loop_no: int) -> dict[str, Any]:
    scene = step.truth.logical_scene
    return {
        "sequence": sequence,
        "loop": loop_no,
        "time_ns": step.video.sample_time.time_ns,
        "step_no": step.video.sample_time.step_no,
        "video": [
            {
                "station_id": frame.station_id,
                "role": frame.camera_role.value,
                "frame_id": frame.frame_id,
                "session_id": frame.station_session_id,
                "width": frame.image.width_px,
                "height": frame.image.height_px,
                "capture": asdict(frame.capture_time),
                "png": encode_video(frame),
            }
            for frame in step.video.frames
        ],
        "monitor": {
            "terrain": asdict(scene.terrain),
            "world_objects": [asdict(item) for item in scene.world_objects],
            "cameras": [asdict(item) for item in scene.cameras],
            "flying_objects": [asdict(item) for item in scene.flying_objects],
            "visibility": [asdict(item) for item in scene.target_visibility],
            "frame_truth": [asdict(item) for item in step.truth.camera_frames],
        },
    }


class PreviewRuntime:
    """Bounded latest-frame buffer and one simulation worker, shared by all viewers."""

    def __init__(self, spec: ScenarioSpec) -> None:
        self.spec = spec
        self._condition = threading.Condition()
        self._stopped = False
        self._paused = False
        self._pending_steps = 0
        self._reset_version = 0
        self._published: dict[str, Any] | None = None
        self._error: str | None = None
        self._thread = threading.Thread(
            target=self._produce, name="scene-video-producer", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
        self._thread.join(timeout=30)
        if self._thread.is_alive():
            raise RuntimeError("scene video producer did not stop")

    def control(self, action: str) -> None:
        with self._condition:
            if action == "pause":
                self._paused = True
                self._pending_steps = 0
            elif action == "play":
                self._paused = False
                self._pending_steps = 0
            elif action == "step":
                self._paused = True
                self._pending_steps = min(self._pending_steps + 1, 10)
            elif action == "restart":
                self._reset_version += 1
                self._pending_steps = 0
            else:
                raise ValueError("unknown preview control")
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "state": "failed" if self._error else "paused" if self._paused else "playing",
                "error": self._error,
                "snapshot": self._published,
                "profile": self.spec.scene_definition.cameras[0].metadata.profile_id,
            }

    def _produce(self) -> None:
        sequence, loop_no, reset_version = 0, 1, 0
        try:
            source = demo_source(self.spec)
            steps = iter(source.generate(self.spec))
            while True:

                def ready(version: int = reset_version) -> bool:
                    return (
                        self._stopped
                        or not self._paused
                        or self._pending_steps > 0
                        or self._reset_version != version
                    )

                with self._condition:
                    self._condition.wait_for(ready)
                    if self._stopped:
                        return
                    if self._reset_version != reset_version:
                        reset_version = self._reset_version
                        source = demo_source(self.spec)
                        steps = iter(source.generate(self.spec))
                        loop_no += 1
                        # Restart publishes frame zero even while paused.
                    if self._pending_steps:
                        self._pending_steps -= 1
                began = time.perf_counter()
                try:
                    step = next(steps)
                except StopIteration:
                    source = demo_source(self.spec)
                    steps = iter(source.generate(self.spec))
                    loop_no += 1
                    step = next(steps)
                payload = preview_payload(step, sequence + 1, loop_no)
                payload["render_ms"] = round((time.perf_counter() - began) * 1000, 1)
                with self._condition:
                    if reset_version != self._reset_version:
                        continue
                    sequence += 1
                    self._published = payload
                    self._condition.notify_all()
                    # Pacing only delays the worker; simulation timestamps come from the spec.
                    interval = (
                        min(
                            camera.exposure.frame_period_ns
                            for camera in self.spec.scene_definition.cameras
                        )
                        / 1e9
                    )
                    delay = max(0.0, interval - (time.perf_counter() - began))

                    def interrupted(version: int = reset_version) -> bool:
                        return self._stopped or self._paused or self._reset_version != version

                    self._condition.wait_for(interrupted, timeout=delay)
        except Exception as error:
            logger.exception("Scene video producer failed")
            with self._condition:
                self._error = f"{type(error).__name__}: {error}"
                self._condition.notify_all()


def preview_server(runtime: PreviewRuntime, port: int) -> ThreadingHTTPServer:
    page = files("chongming.web").joinpath("preview.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def reply(self, status: int, content: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                self.reply(200, page, "text/html; charset=utf-8")
            elif path == "/api/preview":
                self.reply(
                    200,
                    json.dumps(runtime.snapshot(), ensure_ascii=False, allow_nan=False).encode(),
                    "application/json; charset=utf-8",
                )
            else:
                self.reply(404, b"Not found", "text/plain")

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self.reply(403, b"Same-origin controls only", "text/plain")
                return
            if self.path != "/api/control":
                self.reply(404, b"Not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError("invalid control body length")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or not isinstance(body.get("action"), str):
                    raise TypeError("control requires a string action")
                runtime.control(body["action"])
            except (ValueError, KeyError, TypeError):
                self.reply(400, b"Invalid control", "text/plain")
                return
            self.reply(200, b'{"ok":true}', "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="启动虚拟场景主辅视频与场景总览")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--stations", type=int, default=1, choices=range(1, 5))
    parser.add_argument("--fps", type=int, default=15, help="实验预览采样率；默认 15")
    parser.add_argument("--baseline", action="store_true", help="1920×1080 / 640×480，30 fps 基线")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        spec = demo_spec(baseline=args.baseline, station_count=args.stations, fps=args.fps)
        runtime = PreviewRuntime(spec)
        server = preview_server(runtime, args.port)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    url = f"http://127.0.0.1:{server.server_port}/"
    runtime.start()
    print(
        f"虚拟场景预览：{url}\nCtrl+C 停止。视频采样来自实验时钟，页面刷新不会推进时间。",
        flush=True,
    )
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.close()


if __name__ == "__main__":
    main()
