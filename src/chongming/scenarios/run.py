"""Finite virtual-subsystem runs with separate online, truth and timing evidence.

This entry point does not pretend that the unfinished device/fusion runner is
implemented. Every configured exposure is visited; default runs render every
frame. PNG sequences plus capture metadata represent each finite video stream.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
from collections import Counter, deque
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from chongming.contracts.scene import ScenarioSpec, ScenarioStep
from chongming.rendering.metal import close_default_renderer
from chongming.scenarios.demo import demo_source
from chongming.scenarios.serialization import (
    FORMAT_VERSION,
    _encode,
    load_compiled_scenario,
    load_scenario,
    scenario_to_dict,
    validate_supported_features,
)


def _json(value: Any) -> str:
    return json.dumps(_encode(value), ensure_ascii=False, allow_nan=False, sort_keys=True)


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _provenance() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=root,
                text=True,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    sources = {
        str(p.relative_to(root)): _digest(p)
        for p in sorted((root / "src").rglob("*"))
        if p.is_file() and p.suffix in {".py", ".swift", ".metal"}
    }
    return {
        "code_revision": revision,
        "worktree_dirty": dirty,
        "source_digests": sources,
        "source_digest": hashlib.sha256(_json(sources).encode()).hexdigest(),
        "dependency_lock_digest": _digest(root / "uv.lock")
        if (root / "uv.lock").exists()
        else None,
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "dependencies": {name: version(name) for name in ("numpy", "pillow", "chongming")},
        "renderer": "Metal/first-party Swift renderer",
    }


class SceneEvidenceWriter:
    """Streaming evidence with one record per step and no accumulated pixels."""

    def __init__(self, directory: Path, spec: ScenarioSpec) -> None:
        self.spec = spec
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self._handles = {}
        try:
            for name in ("online", "truth", "timing"):
                self._handles[name] = (directory / f"{name}.jsonl").open("x", encoding="utf-8")
        except BaseException:
            self.close()
            raise
        self.streams: Counter[str] = Counter()

    def document(self, name: str, value: Any) -> None:
        with (self.directory / name).open("x", encoding="utf-8") as handle:
            handle.write(_json(value) + "\n")

    def record(self, step: ScenarioStep) -> float:
        began = time.perf_counter()
        frame_records = []
        for frame in step.video.frames:
            # Never use user-provided IDs as path components.
            camera_index = next(
                i
                for i, c in enumerate(self.spec.scene_definition.cameras)
                if c.station_id == frame.station_id and c.camera_role == frame.camera_role
            )
            stream = f"camera-{camera_index:03d}"
            self.streams[stream] += 1
            relative = Path("video") / stream / f"{self.streams[stream]:08d}.png"
            path = self.directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.frombytes(
                "RGB",
                (frame.image.width_px, frame.image.height_px),
                frame.image.pixels,
                "raw",
                "RGB",
                frame.image.row_stride_bytes,
            ).save(path)
            frame_records.append(
                {
                    "station_id": frame.station_id,
                    "camera_role": frame.camera_role,
                    "frame_id": frame.frame_id,
                    "session_id": frame.station_session_id,
                    "capture_time": frame.capture_time,
                    "camera_config_ref": frame.camera_config_ref,
                    "width_px": frame.image.width_px,
                    "height_px": frame.image.height_px,
                    "pixel_format": frame.image.pixel_format,
                    "path": str(relative),
                    "sha256": _digest(path),
                }
            )
        self._handles["online"].write(
            _json(
                {
                    "sample_time": step.video.sample_time,
                    "frames": frame_records,
                    "rendered": step.video.rendered,
                    "skipped_frame_ids": step.video.skipped_frame_ids,
                    "render_skip_reason": step.video.render_skip_reason,
                    "packets": step.packets,
                }
            )
            + "\n"
        )
        self._handles["truth"].write(_json(replace(step.truth, timing=None)) + "\n")
        evidence_ms = (time.perf_counter() - began) * 1000
        self._handles["timing"].write(
            _json(
                {
                    "sample_time": step.video.sample_time,
                    "rendered": step.video.rendered,
                    "stages": step.truth.timing,
                    "evidence_ms": evidence_ms,
                }
            )
            + "\n"
        )
        return evidence_ms

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()


def run_scene(
    path: Path, output: Path, *, render_every: int = 1, video_enabled: bool = True
) -> dict[str, Any]:
    if type(render_every) is not int or render_every <= 0:
        raise ValueError("render_every must be a positive integer")
    document = json.loads(path.read_text())
    spec = (
        load_compiled_scenario(path)
        if isinstance(document, dict) and document.get("format") == FORMAT_VERSION
        else load_scenario(path)
    )
    validate_supported_features(spec)
    authoring_document = document
    source = demo_source(spec, render_every=render_every, video_enabled=video_enabled)
    writer = SceneEvidenceWriter(output, spec)
    counts: Counter[str] = Counter()
    samples: deque[float] = deque(maxlen=512)
    rendered_samples: deque[float] = deque(maxlen=512)
    stage_totals: dict[str, float] = {}
    maximum = 0.0
    render_maximum = 0.0
    summary: dict[str, Any] = {"status": "failed", "scope": "virtual-subsystem"}
    try:
        scenario = scenario_to_dict(spec)
        if (
            isinstance(authoring_document, dict)
            and authoring_document.get("format") == "scenario-authoring-v2"
        ):
            writer.document("authoring.json", authoring_document)
        writer.document("scenario.json", scenario)
        writer.document(
            "manifest.json",
            {
                "scope": "virtual-subsystem",
                "run_id": str(uuid4()),
                "scenario": scenario,
                "scenario_content_digest": hashlib.sha256(_json(scenario).encode()).hexdigest(),
                "authoring_format": authoring_document.get("format")
                if isinstance(authoring_document, dict)
                else None,
                "authoring_content_digest": hashlib.sha256(
                    _json(authoring_document).encode()
                ).hexdigest(),
                "station_config_content_digests": {
                    str(c.station_id): hashlib.sha256(_json(c).encode()).hexdigest()
                    for c in spec.station_configs
                },
                "station_sessions": source.station_sessions,
                "render_policy": {
                    "video_enabled": video_enabled,
                    "every_camera_exposures": render_every,
                    "backpressure": "serial-no-catch-up",
                },
                "stream_mapping": [
                    {
                        "path": f"video/camera-{i:03d}",
                        "station_id": c.station_id,
                        "camera_role": c.camera_role,
                        "camera_id": c.metadata.camera_id,
                    }
                    for i, c in enumerate(spec.scene_definition.cameras)
                ],
                **_provenance(),
                "renderer": "Metal" if video_enabled else None,
            },
        )
        for step in source.generate(spec):
            counts["logical_steps"] += 1
            counts["logical_frames"] += len(step.truth.camera_frames)
            counts["rendered_steps"] += int(step.video.rendered)
            counts["skipped_steps"] += int(not step.video.rendered)
            counts["rendered_frames"] += len(step.video.frames)
            counts["skipped_frames"] += len(step.video.skipped_frame_ids)
            counts["ray_events"] += len(step.packets.rays)
            timing = step.truth.timing
            assert timing is not None
            samples.append(timing.total_ms)
            maximum = max(maximum, timing.total_ms)
            if step.video.rendered:
                rendered_samples.append(timing.total_ms)
                render_maximum = max(render_maximum, timing.total_ms)
            counts["gpu_submissions"] += timing.gpu_submissions
            for name in ("logic_ms", "frame_truth_ms", "render_ms", "virtual_ms", "gpu_ms"):
                stage_totals[name] = stage_totals.get(name, 0.0) + getattr(timing, name)
            stage_totals["evidence_ms"] = stage_totals.get("evidence_ms", 0.0) + writer.record(step)
        summary["status"] = "complete"
    except BaseException as error:
        summary["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:

        def stats(values: deque[float], max_ms: float) -> dict[str, Any]:
            ordered = sorted(values)
            return {
                "window_sample_count": len(values),
                "window_capacity": values.maxlen,
                "p50_ms": statistics.median(values) if values else None,
                "p95_ms": ordered[max(0, math_ceil_95(len(values)) - 1)] if values else None,
                "max_ms_whole_run": max_ms,
            }

        summary.update(
            counts=dict(counts),
            streams=dict(writer.streams),
            stage_totals_ms=dict(stage_totals),
            generation=stats(samples, maximum),
            rendered_generation=stats(rendered_samples, render_maximum),
        )
        try:
            writer.document("summary.json", summary)
        finally:
            writer.close()
    return summary


def math_ceil_95(count: int) -> int:
    return (95 * count + 99) // 100


def main() -> None:
    parser = argparse.ArgumentParser(description="从 JSON 生成完整有限虚拟视频流与证据")
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="必须是尚不存在的目录")
    parser.add_argument(
        "--no-video", action="store_true", help="完全停用像素；保留逻辑、普通事件和真值"
    )
    parser.add_argument(
        "--render-every", type=int, default=1, help="每条相机流的像素采样间隔，默认完整输出"
    )
    args = parser.parse_args()
    try:
        print(
            _json(
                run_scene(
                    args.scenario,
                    args.output,
                    render_every=args.render_every,
                    video_enabled=not args.no_video,
                )
            )
        )
    finally:
        close_default_renderer()


if __name__ == "__main__":
    main()
