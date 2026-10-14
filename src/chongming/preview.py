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
import math
import statistics
import threading
import time
import webbrowser
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from chongming.contracts.model import CameraFrame
from chongming.contracts.scene import CameraFrameTruth, ScenarioSpec, ScenarioStep
from chongming.rendering.godot import close_default_renderer
from chongming.scenarios.demo import demo_source, demo_spec
from chongming.scenarios.serialization import load_scenario

logger = logging.getLogger(__name__)


class DiscreteRenderClock:
    """Wall-clock pacing for preview rendering, independent of experiment time.

    A tick only controls when the next already-defined experiment step is
    produced. It never changes exposure timestamps, advances the experiment
    clock, or drops a step from a quantitative scenario source.
    """

    def __init__(self, render_fps: float) -> None:
        if not 0 < render_fps <= 240:
            raise ValueError("render_fps must be greater than 0 and at most 240")
        self.render_fps = float(render_fps)
        self.period_ns = max(1, round(1_000_000_000 / self.render_fps))
        self._next_tick_ns: int | None = None

    def begin(self) -> None:
        self._next_tick_ns = time.perf_counter_ns() + self.period_ns

    def wait_for_next_tick(
        self, condition: threading.Condition, interrupted: Callable[[], bool]
    ) -> None:
        """Wait until the next render tick without blocking shutdown/control."""
        now = time.perf_counter_ns()
        if self._next_tick_ns is None:
            self.begin()
        assert self._next_tick_ns is not None
        delay_ns = self._next_tick_ns - now
        if delay_ns > 0 and condition.wait_for(interrupted, timeout=delay_ns / 1_000_000_000):
            return
        now = time.perf_counter_ns()
        if self._next_tick_ns <= now:
            missed = (now - self._next_tick_ns) // self.period_ns + 1
            self._next_tick_ns += missed * self.period_ns
        else:
            self._next_tick_ns += self.period_ns


def encode_video(frame: CameraFrame) -> str:
    """Lossless PNG transport of the ordinary RGB8 plane, without annotations."""
    image = Image.frombytes(
        "RGB",
        (frame.image.width_px, frame.image.height_px),
        bytes(frame.image.pixels),
        "raw",
        "RGB",
        frame.image.row_stride_bytes,
    )
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=1)
    return base64.b64encode(output.getvalue()).decode("ascii")


def _video_payload(step: ScenarioStep, station_id: str | None) -> list[dict[str, Any]]:
    return [
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
        if station_id is None or frame.station_id == station_id
    ]


def _preview_metadata(step: ScenarioStep, sequence: int, loop_no: int) -> dict[str, Any]:
    scene = step.truth.logical_scene
    return {
        "sequence": sequence,
        "loop": loop_no,
        "time_ns": step.video.sample_time.time_ns,
        "step_no": step.video.sample_time.step_no,
        "rendered": step.video.rendered,
        "render_skip_reason": step.video.render_skip_reason,
        "skipped_frame_ids": list(step.video.skipped_frame_ids),
        "packets": {
            "calibration_event_count": len(step.packets.calibration_events),
            "ray_count": len(step.packets.rays),
            "diagnostic_count": len(step.packets.diagnostics),
            "rays": [asdict(item) for item in step.packets.rays],
        },
        "monitor": {
            "terrain": asdict(scene.terrain),
            "world_objects": [asdict(item) for item in scene.world_objects],
            "cameras": [asdict(item) for item in scene.cameras],
            "flying_objects": [asdict(item) for item in scene.flying_objects],
            "visibility": [asdict(item) for item in scene.target_visibility],
            "frame_truth": [asdict(item) for item in step.truth.camera_frames],
        },
    }


def preview_payload(step: ScenarioStep, sequence: int, loop_no: int) -> dict[str, Any]:
    return {**_preview_metadata(step, sequence, loop_no), "video": _video_payload(step, None)}


class PreviewRuntime:
    """Bounded latest-frame buffer and one simulation worker, shared by all viewers."""

    def __init__(
        self, spec: ScenarioSpec, *, render_fps: float = 10.0, video_enabled: bool = True
    ) -> None:
        self.spec = spec
        self.video_enabled = video_enabled
        self.render_clock = DiscreteRenderClock(render_fps)
        minimum_period = min(
            (camera.exposure.frame_period_ns for camera in spec.scene_definition.cameras),
            default=self.render_clock.period_ns,
        )
        # Allow one nanosecond of period quantization: floor(1e9 / 15)
        # must still select every second exposure for a 7.5 Hz render clock.
        self.render_every = max(1, math.ceil(self.render_clock.period_ns / (minimum_period + 1)))
        self._condition = threading.Condition()
        self._stopped = False
        self._paused = False
        self._pending_steps = 0
        self._reset_version = 0
        self._published: dict[str, Any] | None = None
        self._latest_step: ScenarioStep | None = None
        self._encoded: dict[str, dict[str, Any]] = {}
        self._encode_lock = threading.Lock()
        self._station_ids = tuple(
            dict.fromkeys(str(camera.station_id) for camera in spec.scene_definition.cameras)
        )
        self._error: str | None = None
        self._generation_samples_ms: deque[float] = deque(maxlen=120)
        self._rendered_samples_ms: deque[float] = deque(maxlen=120)
        self._last_logical_timing: dict[str, Any] | None = None
        self._gpu_submissions = 0
        self._logical_step_count = 0
        self._rendered_step_count = 0
        self._skipped_step_count = 0
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

    def snapshot(
        self, station_id: str | None = None, after_sequence: int | None = None
    ) -> dict[str, Any]:
        station_id = station_id or self._station_ids[0]
        if station_id not in self._station_ids:
            raise ValueError("unknown preview station")
        with self._condition:
            result = {
                "state": "failed" if self._error else "paused" if self._paused else "playing",
                "error": self._error,
                "snapshot": self._published,
                "profile": self.spec.scene_definition.cameras[0].metadata.profile_id,
                "render_clock_fps": self.render_clock.render_fps,
                "render_every": self.render_every,
                "logical_step_count": self._logical_step_count,
                "rendered_step_count": self._rendered_step_count,
                "skipped_step_count": self._skipped_step_count,
                "gpu_submissions": self._gpu_submissions,
                "last_logical_timing": self._last_logical_timing,
                "generation_stats_ms": self._timing_stats(self._generation_samples_ms),
                "rendered_generation_stats_ms": self._timing_stats(self._rendered_samples_ms),
            }
            step, payload, cache = self._latest_step, self._published, self._encoded
        if payload is not None and payload["sequence"] == after_sequence:
            result["snapshot"] = None
            return result
        if step is not None and payload is not None:
            # Encode on demand outside the producer's lock. All viewers share
            # one per-station cache for this immutable latest sample. Replacing
            # the cache on publication bounds history; paused polls reuse PNGs.
            with self._encode_lock:
                encoded = cache.get(station_id)
                if encoded is None:
                    began = time.perf_counter()
                    encoded = {"video": _video_payload(step, station_id)}
                    encoded["png_encode_ms"] = round((time.perf_counter() - began) * 1000, 2)
                    cache[station_id] = encoded
            result["snapshot"] = {**payload, **encoded, "preview_station_id": station_id}
        return result

    @staticmethod
    def _timing_stats(values: deque[float]) -> dict[str, Any]:
        samples = sorted(values)
        return {
            "p50": round(statistics.median(samples), 2) if samples else None,
            "p95": round(samples[max(0, math.ceil(len(samples) * 0.95) - 1)], 2)
            if samples
            else None,
            "max": round(max(samples), 2) if samples else None,
            "sample_count": len(samples),
            "window_capacity": values.maxlen,
        }

    def _produce(self) -> None:
        sequence, loop_no, reset_version = 0, 1, 0
        latest_frames: dict[tuple[str, str], CameraFrame] = {}
        latest_truths: dict[tuple[str, str], CameraFrameTruth] = {}
        try:
            source = demo_source(
                self.spec, render_every=self.render_every, video_enabled=self.video_enabled
            )
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
                        source = demo_source(
                            self.spec,
                            render_every=self.render_every,
                            video_enabled=self.video_enabled,
                        )
                        steps = iter(source.generate(self.spec))
                        loop_no += 1
                        latest_frames.clear()
                        latest_truths.clear()
                        # Restart publishes frame zero even while paused.
                began = time.perf_counter()
                try:
                    step = next(steps)
                except StopIteration:
                    source = demo_source(
                        self.spec, render_every=self.render_every, video_enabled=self.video_enabled
                    )
                    steps = iter(source.generate(self.spec))
                    loop_no += 1
                    latest_frames.clear()
                    latest_truths.clear()
                    step = next(steps)
                generated = time.perf_counter()
                timing = step.truth.timing
                assert timing is not None
                with self._condition:
                    self._logical_step_count += 1
                    self._generation_samples_ms.append(timing.total_ms)
                    self._last_logical_timing = asdict(timing)
                    self._gpu_submissions += timing.gpu_submissions
                if not step.video.rendered and self.video_enabled:
                    # Logic, virtual events and privileged truth were still generated;
                    # this tick simply has no pixel payload to publish or encode.
                    with self._condition:
                        self._skipped_step_count += 1
                    continue
                with self._condition:
                    if step.video.rendered:
                        self._rendered_step_count += 1
                        self._rendered_samples_ms.append(timing.total_ms)
                    else:
                        self._skipped_step_count += 1
                    stats = self._timing_stats(self._generation_samples_ms)
                    render_stats = self._timing_stats(self._rendered_samples_ms)
                for frame in step.video.frames:
                    key = (str(frame.station_id), frame.camera_role.value)
                    latest_frames[key] = frame
                    latest_truths[key] = next(
                        t for t in step.truth.camera_frames if t.frame_id == frame.frame_id
                    )
                # Each role retains its own last rendered capture and matching
                # overlay truth. Independent schedules never blank the other role.
                display_step = replace(
                    step,
                    video=replace(step.video, frames=tuple(latest_frames.values())),
                    truth=replace(step.truth, camera_frames=tuple(latest_truths.values())),
                )
                payload = _preview_metadata(display_step, sequence + 1, loop_no)
                payload.update(
                    gpu_render_ms=round(timing.gpu_ms, 2),
                    imager_overhead_ms=round(max(0, timing.render_ms - timing.gpu_ms), 2),
                    scene_logic_ms=round(timing.logic_ms + timing.frame_truth_ms, 2),
                    virtual_ms=round(timing.virtual_ms, 2),
                    render_ms=round((generated - began) * 1000, 2),
                    generation_stats_ms=stats,
                    rendered_generation_stats_ms=render_stats,
                )
                with self._condition:
                    if reset_version != self._reset_version:
                        continue
                    sequence += 1
                    if self._pending_steps:
                        self._pending_steps -= 1
                    self._published = payload
                    self._latest_step = display_step
                    self._encoded = {}
                    self._condition.notify_all()

                    def interrupted(version: int = reset_version) -> bool:
                        return self._stopped or self._paused or self._reset_version != version

                    # This is wall-clock pacing only. The source's experiment
                    # timestamps remain authoritative and are never rewritten.
                    self.render_clock.wait_for_next_tick(self._condition, interrupted)
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
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    station_id = query.get("station", [None])[0]
                    after = query.get("after", [None])[0]
                    snapshot = runtime.snapshot(
                        station_id, int(after) if after is not None else None
                    )
                except ValueError:
                    self.reply(400, b"Invalid preview request", "text/plain")
                    return
                self.reply(
                    200,
                    json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode(),
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
    parser.add_argument("--fps", type=int, default=30, help="实验采样率；默认 30，图像分辨率固定")
    parser.add_argument(
        "--render-fps",
        type=float,
        default=10.0,
        help="独立的预览渲染墙钟频率；不改变实验时间戳，默认 10",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="从 scenario-authoring-v2 文件加载可编辑场景；提供后忽略 --stations/--fps",
    )
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--no-video", action="store_true", help="只看逻辑与事件，完全停止像素生产")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        spec = (
            load_scenario(Path(args.scenario), asset_root=Path(args.scenario).parent)
            if args.scenario is not None
            else demo_spec(station_count=args.stations, fps=args.fps)
        )
        runtime = PreviewRuntime(spec, render_fps=args.render_fps, video_enabled=not args.no_video)
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
        close_default_renderer()


if __name__ == "__main__":
    main()
