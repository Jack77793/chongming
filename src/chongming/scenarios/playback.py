"""Playback of pre-recorded scenario evidence as a scenario source.

This module reads recording packages produced by SceneEvidenceWriter and
reconstructs ScenarioStep objects at high frame rates without rendering.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from PIL import Image

from chongming.contracts.model import (
    CameraFrame,
    CameraRole,
    CaptureTime,
    ConfigDigest,
    FrameId,
    ImagePlane,
    PixelFormat,
    SessionId,
    StationId,
)
from chongming.contracts.scene import (
    CameraFrameTruth,
    LogicalSceneSnapshot,
    ScenarioPacketOutput,
    ScenarioSpec,
    ScenarioStep,
    ScenarioStepTiming,
    ScenarioTruthOutput,
    ScenarioVideoOutput,
    SimulationTime,
)


class RecordingReader:
    """Read a recording package and validate its format."""

    def __init__(self, directory: Path) -> None:
        if not directory.is_dir():
            raise ValueError(f"recording directory does not exist: {directory}")
        self.directory = directory
        self._validate_structure()
        self._load_manifest()

    def _validate_structure(self) -> None:
        """Ensure all required files exist."""
        required = ["online.jsonl", "truth.jsonl", "truth-static.json", "timing.jsonl"]
        for name in required:
            path = self.directory / name
            if not path.exists():
                raise ValueError(f"recording missing required file: {name}")
        video_dir = self.directory / "video"
        if not video_dir.is_dir():
            raise ValueError("recording missing video directory")

    def _load_manifest(self) -> None:
        """Load manifest if present, otherwise infer metadata from evidence."""
        manifest_path = self.directory / "manifest.json"
        if manifest_path.exists():
            with manifest_path.open("r", encoding="utf-8") as handle:
                self.manifest = json.load(handle)
                # Accept both recording-v1 and virtual-subsystem formats
                scope = self.manifest.get("scope")
                if scope not in ("recording-v1", "virtual-subsystem", None):
                    raise ValueError(f"unsupported recording scope: {scope}")
        else:
            # Infer from evidence files
            self.manifest = self._infer_manifest()

    def _infer_manifest(self) -> dict[str, Any]:
        """Infer manifest from evidence files when not explicitly provided."""
        online_path = self.directory / "online.jsonl"
        lines = online_path.read_text().splitlines()
        return {
            "format": "recording-v1-inferred",
            "total_steps": len(lines),
            "inferred": True,
        }

    def read_static_truth(self) -> dict[str, Any]:
        """Read the static truth scene geometry."""
        static_path = self.directory / "truth-static.json"
        with static_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def iter_steps(self) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
        """Yield (online, truth, timing) record triples in temporal order."""
        online_path = self.directory / "online.jsonl"
        truth_path = self.directory / "truth.jsonl"
        timing_path = self.directory / "timing.jsonl"

        with (
            online_path.open("r", encoding="utf-8") as online_file,
            truth_path.open("r", encoding="utf-8") as truth_file,
            timing_path.open("r", encoding="utf-8") as timing_file,
        ):
            for online_line, truth_line, timing_line in zip(
                online_file, truth_file, timing_file, strict=True
            ):
                online = json.loads(online_line)
                truth = json.loads(truth_line)
                timing = json.loads(timing_line)
                yield online, truth, timing

    def read_frame_image(self, relative_path: str) -> ImagePlane:
        """Load a frame image from the recording."""
        path = self.directory / relative_path
        if not path.exists():
            raise ValueError(f"frame image not found: {relative_path}")
        image = Image.open(path)
        if image.mode != "RGB":
            image = image.convert("RGB")
        width, height = image.size
        pixels = image.tobytes()
        return ImagePlane(
            width_px=width,
            height_px=height,
            row_stride_bytes=width * 3,
            pixel_format=PixelFormat.RGB8,
            pixels=pixels,
        )


class RecordedScenarioSource:
    """Scenario source that replays pre-recorded evidence."""

    def __init__(
        self,
        reader: RecordingReader,
        spec: ScenarioSpec,
        *,
        playback_fps: float | None = None,
    ) -> None:
        """Initialize recorded scenario source.

        Args:
            reader: Recording reader instance
            spec: Scenario specification
            playback_fps: Target playback frame rate (Hz). If None, plays back
                at original recording speed. If specified, adds delays between
                steps to match target fps.
        """
        self.reader = reader
        self.spec = spec
        self._static_truth = reader.read_static_truth()
        self.playback_fps = playback_fps
        self._last_step_time: float | None = None

    def get_spec(self) -> ScenarioSpec:
        return self.spec

    def generate(self, spec: ScenarioSpec) -> Iterator[ScenarioStep]:
        """Yield reconstructed scenario steps from the recording.

        Args:
            spec: Ignored for recorded sources; recording has its own spec.
        """
        import time

        self._last_step_time = None
        static_world_objects = self._static_truth.get("world_objects")
        static_digest = self._static_truth.get("world_objects_sha256")

        for online, truth, timing in self.reader.iter_steps():
            # Reconstruct video output
            frames = []
            for frame_record in online.get("frames", []):
                image = self.reader.read_frame_image(frame_record["path"])

                # Filter out $type field from capture_time
                capture_time_data = {
                    k: v for k, v in frame_record["capture_time"].items() if k != "$type"
                }

                frames.append(
                    CameraFrame(
                        station_id=StationId(frame_record["station_id"]),
                        station_session_id=SessionId(frame_record["session_id"]),
                        camera_role=CameraRole(frame_record["camera_role"]),
                        frame_id=FrameId(frame_record["frame_id"]),
                        capture_time=CaptureTime(**capture_time_data),
                        camera_config_ref=ConfigDigest(frame_record["camera_config_ref"]),
                        image=image,
                    )
                )

            sample_time_data = online["sample_time"]
            sample_time = SimulationTime(
                clock_domain_id=sample_time_data["clock_domain_id"],
                time_ns=sample_time_data["time_ns"],
                step_no=sample_time_data["step_no"],
            )

            video = ScenarioVideoOutput(
                scenario_id=online.get("scenario_id", self.spec.scenario_id),
                sample_time=sample_time,
                frames=tuple(frames),
                rendered=online.get("rendered", True),
                render_skip_reason=online.get("render_skip_reason"),
                skipped_frame_ids=tuple(
                    FrameId(fid) for fid in online.get("skipped_frame_ids", [])
                ),
            )

            # Reconstruct packet output (simplified - only what's needed for preview)
            packets = ScenarioPacketOutput(
                scenario_id=self.spec.scenario_id,
                sample_time=sample_time,
                calibration_states=(),
                calibration_solutions=(),
                calibration_events=(),
                rays=(),
                diagnostics=(),
            )

            # Reconstruct truth output by merging static world_objects back
            logical_scene_data = truth["logical_scene"]
            if static_digest != truth.get("logical_scene_static_ref"):
                raise ValueError("truth record static reference mismatch")
            # Restore world_objects into logical_scene
            logical_scene_data["world_objects"] = static_world_objects

            # Restore the per-camera truth needed by preview overlays and
            # downstream evaluators. The logical scene remains a JSON mapping
            # because playback does not need to rehydrate every scene subtype.
            from chongming.scenarios.serialization import _decode

            logical_scene = (
                _decode(logical_scene_data, LogicalSceneSnapshot)
                if logical_scene_data.get("$type") == "LogicalSceneSnapshot"
                else logical_scene_data
            )
            camera_frames = tuple(
                _decode(frame, CameraFrameTruth)
                for frame in truth.get("camera_frames", [])
            )
            truth_output = ScenarioTruthOutput(
                scenario_id=self.spec.scenario_id,
                sample_time=sample_time,
                logical_scene=logical_scene,
                camera_frames=camera_frames,
                online_correspondences=(),
                experiment_perturbations=truth["experiment_perturbations"],  # type: ignore[arg-type]
                timing=(
                    ScenarioStepTiming(
                        **{
                            key: value
                            for key, value in timing["stages"].items()
                            if key != "$type"
                        }
                    )
                    if isinstance(timing.get("stages"), dict)
                    else None
                ),
            )

            # Frame rate control: add delay to match target playback fps
            if self.playback_fps is not None:
                current_time = time.perf_counter()
                if self._last_step_time is not None:
                    target_interval = 1.0 / self.playback_fps
                    elapsed = current_time - self._last_step_time
                    sleep_time = target_interval - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                self._last_step_time = time.perf_counter()

            yield ScenarioStep(video=video, packets=packets, truth=truth_output)


def load_recording_source(
    directory: Path, *, playback_fps: float | None = None
) -> RecordedScenarioSource:
    """Load a recorded scenario source from a recording directory.

    This is the main entry point for playback functionality.

    Args:
        directory: Path to the recording directory
        playback_fps: Target playback frame rate (Hz). If None, plays back at
            original recording speed with no artificial delays.
    """
    reader = RecordingReader(directory)

    # Load the scenario spec from authoring.json (the original user-provided config)
    # Note: scenario.json contains the compiled spec which is too large for editing
    authoring_path = reader.directory / "authoring.json"
    if authoring_path.exists():
        from chongming.scenarios.serialization import load_scenario

        spec = load_scenario(authoring_path)
    else:
        # Fallback: try to load from scenario.json if authoring.json is not available
        scenario_path = reader.directory / "scenario.json"
        if scenario_path.exists():
            from chongming.scenarios.serialization import load_scenario

            spec = load_scenario(scenario_path)
        else:
            raise FileNotFoundError(
                f"Neither authoring.json nor scenario.json found in {directory}"
            )

    return RecordedScenarioSource(reader, spec, playback_fps=playback_fps)
