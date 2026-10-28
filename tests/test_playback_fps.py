"""Test playback frame rate control functionality."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from chongming.scenarios.playback import RecordingReader, load_recording_source


def create_minimal_recording(directory: Path, frame_count: int = 10) -> None:
    """Create a minimal recording package for testing.

    Args:
        directory: Directory to create the recording in
        frame_count: Number of frames to create
    """
    directory.mkdir(parents=True, exist_ok=True)

    width, height = 64, 48
    ffmpeg = os.environ.get("CHONGMING_FFMPEG") or shutil.which("ffmpeg")
    if ffmpeg is None:
        raise unittest.SkipTest("FFmpeg is required for video playback tests")
    video_dir = directory / "video"
    video_dir.mkdir()
    video_path = video_dir / "camera-000.mp4"
    raw_buffer = bytearray()
    for i in range(frame_count):
        for row in range(height):
            for column in range(width):
                raw_buffer.extend(((i * 17) % 256, (row * 3) % 256, (column * 5) % 256))
    raw = bytes(raw_buffer)
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s:v",
            f"{width}x{height}",
            "-r",
            "5",
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "8",
            "-pix_fmt",
            "yuv444p",
            str(video_path),
        ],
        input=raw,
        check=True,
    )
    (directory / "video.json").write_text(
        json.dumps(
            {
                "format": "video-h264-v1",
                "streams": {
                    "camera-000": {
                        "path": "video/camera-000.mp4",
                        "width_px": width,
                        "height_px": height,
                        "pixel_format": "rgb8",
                        "frame_count": frame_count,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    # Create minimal online.jsonl
    online_records = []
    for i in range(frame_count):
        record = {
            "scenario_id": "test-scenario",
            "sample_time": {
                "clock_domain_id": "test-clock",
                "time_ns": i * 200_000_000,  # 200ms per frame (5fps)
                "step_no": i,
            },
            "frames": [
                {
                    "station_id": "station-01",
                    "session_id": "session-01",
                    "camera_role": "auxiliary",
                    "frame_id": f"frame-{i:04d}",
                    "capture_time": {
                        "$type": "CaptureTime",
                        "clock_domain_id": "test-clock",
                        "exposure_start_ns": i * 200_000_000,
                        "exposure_end_ns": i * 200_000_000 + 10_000_000,
                        "clock_uncertainty_ns": 1000,
                        "rolling_shutter_model": "none",
                    },
                    "camera_config_ref": "config-01",
                    "path": "video/camera-000.mp4",
                    "frame_index": i,
                    "width_px": width,
                    "height_px": height,
                }
            ],
            "rendered": True,
            "skipped_frame_ids": [],
        }
        online_records.append(json.dumps(record, ensure_ascii=False))

    (directory / "online.jsonl").write_text("\n".join(online_records) + "\n", encoding="utf-8")

    # Create minimal truth.jsonl
    truth_records = []
    for i in range(frame_count):
        record = {
            "scenario_id": "test-scenario",
            "sample_time": {
                "clock_domain_id": "test-clock",
                "time_ns": i * 200_000_000,
                "step_no": i,
            },
            "logical_scene": {
                "simulation_time_ns": i * 200_000_000,
            },
            "logical_scene_static_ref": "static-hash-placeholder",
            "experiment_perturbations": [],
        }
        truth_records.append(json.dumps(record, ensure_ascii=False))

    (directory / "truth.jsonl").write_text("\n".join(truth_records) + "\n", encoding="utf-8")

    # Create minimal truth-static.json
    static_truth = {
        "world_objects": [],
        "world_objects_sha256": "static-hash-placeholder",
    }
    (directory / "truth-static.json").write_text(
        json.dumps(static_truth, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Create minimal timing.jsonl
    timing_records = []
    for i in range(frame_count):
        record = {"step_no": i, "render_time_ms": 10.0}
        timing_records.append(json.dumps(record, ensure_ascii=False))

    (directory / "timing.jsonl").write_text("\n".join(timing_records) + "\n", encoding="utf-8")

    # Create minimal scenario spec in authoring format
    scenario_spec = {
        "format": "scenario-authoring-v2",
        "scenario_id": "test-scenario",
        "random_seed": 42,
        "duration_s": frame_count * 0.2,  # 200ms per frame
        "fps": 5,
        "stations": [{"id": "station-01", "position_enu_m": [0, 0, 0]}],
        "targets": [],
    }
    (directory / "authoring.json").write_text(
        json.dumps(scenario_spec, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class TestPlaybackFrameRate(unittest.TestCase):
    """Test frame rate control in playback mode."""

    def setUp(self) -> None:
        """Create a temporary recording for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.recording_path = Path(self.temp_dir.name) / "recording"

        # Create a minimal recording with 10 frames
        create_minimal_recording(self.recording_path, frame_count=10)

    def tearDown(self) -> None:
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_static_truth_requires_a_json_object(self) -> None:
        reader = RecordingReader(self.recording_path)
        self.addCleanup(reader.close)
        static_path = self.recording_path / "truth-static.json"
        self.assertEqual(
            reader.read_static_truth(),
            {"world_objects": [], "world_objects_sha256": "static-hash-placeholder"},
        )
        invalid_values: tuple[object, ...] = (None, [], "invalid", 1, True)
        for value in invalid_values:
            with self.subTest(value=value):
                static_path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(TypeError, "static truth JSON root must be an object"):
                    reader.read_static_truth()

    def test_playback_with_fps_limit(self) -> None:
        """Test that playback respects FPS limit."""
        target_fps = 10.0
        source = load_recording_source(self.recording_path, playback_fps=target_fps)

        # Measure time to play through recording
        start_time = time.perf_counter()
        step_count = 0

        for step_count, _ in enumerate(source.generate(source.spec), start=1):
            # Only test first few frames to keep test fast
            if step_count >= 5:
                break

        elapsed = time.perf_counter() - start_time

        # Expected duration: (frames - 1) / fps
        # We use frames-1 because sleep happens between frames
        expected_duration = (step_count - 1) / target_fps

        # Allow 20% tolerance for timing jitter
        tolerance = 0.2
        self.assertGreater(
            elapsed,
            expected_duration * (1 - tolerance),
            f"Playback too fast: {elapsed:.3f}s < {expected_duration:.3f}s",
        )
        self.assertLess(
            elapsed,
            expected_duration * (1 + tolerance),
            f"Playback too slow: {elapsed:.3f}s > {expected_duration:.3f}s",
        )

    def test_playback_without_fps_limit(self) -> None:
        """Test that playback without FPS limit runs at maximum speed."""
        source = load_recording_source(self.recording_path, playback_fps=None)

        # Measure time to play through recording
        start_time = time.perf_counter()
        for step_count, _ in enumerate(source.generate(source.spec), start=1):
            if step_count >= 5:
                break

        elapsed = time.perf_counter() - start_time

        # Without FPS limit, should complete very quickly (< 1 second for 5 frames)
        self.assertLess(
            elapsed,
            1.0,
            f"Playback without FPS limit too slow: {elapsed:.3f}s",
        )

    def test_playback_fps_accuracy(self) -> None:
        """Test frame rate accuracy over multiple frames."""
        target_fps = 20.0
        source = load_recording_source(self.recording_path, playback_fps=target_fps)

        frame_times = []
        for i, _ in enumerate(source.generate(source.spec)):
            frame_times.append(time.perf_counter())
            if i >= 10:  # Test 10 frames
                break

        if len(frame_times) < 2:
            self.skipTest("Recording too short")

        # Calculate intervals between frames
        intervals = [frame_times[i + 1] - frame_times[i] for i in range(len(frame_times) - 1)]

        # Expected interval
        expected_interval = 1.0 / target_fps

        # Check each interval is close to expected
        for interval in intervals:
            # Allow 30% tolerance per frame (timing jitter)
            self.assertAlmostEqual(
                interval,
                expected_interval,
                delta=expected_interval * 0.3,
                msg=f"Frame interval {interval:.4f}s not close to {expected_interval:.4f}s",
            )


if __name__ == "__main__":
    unittest.main()
