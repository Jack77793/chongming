"""Reference plugin, sealed-time orchestration, and real CLI delivery."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from chongming.composition.center import declare_center
from chongming.composition.development import development_run
from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.contracts import scene as s
from chongming.contracts.development import (
    CenterInputStep,
    CenterRegistration,
)
from chongming.contracts.runtime import ModuleState
from chongming.development import InputInspector
from chongming.reference import FusionEngineAdapter, create_algorithm
from chongming.scenarios.authoring import compile_authoring
from chongming.scenarios.demo import demo_source
from chongming.scenarios.serialization import load_scenario

ROOT = Path(__file__).resolve().parents[1]


class ReferenceFusionTests(unittest.TestCase):
    def setUp(self) -> None:
        original = load_scenario(ROOT / "scenarios/center_clean.json")
        self.spec = replace(original, timeline=replace(original.timeline, end_time_ns=120_000_000))
        run = development_run(self.spec, Path("unused-reference-test-output"))
        self.registration = CenterRegistration(
            stations=self.spec.station_configs,
            center_session_id=str(run.center_session_id),
            policy=run.fusion_policy,
            clock_mapping=run.processing_to_capture_clock,
            started_at=m.ProcessingTime(
                clock_domain_id=run.processing_to_capture_clock.processing_clock_domain_id,
                time_ns=0,
            ),
        )
        self.center, _ = declare_center(
            center_session_id=run.center_session_id,
            policy=run.fusion_policy,
            clock_mapping=run.processing_to_capture_clock,
        )
        self.adapter = FusionEngineAdapter(self.center.fusion)
        self.adapter.start(self.registration)

    def steps(self, spec: s.ScenarioSpec | None = None) -> list[CenterInputStep]:
        spec = self.spec if spec is None else spec
        source = demo_source(spec, video_enabled=False)
        last: dict[m.StationId, m.CaptureTime] = {}
        configs = {config.station_id: config.config_digest for config in spec.station_configs}
        rows = []
        for number, generated in enumerate(source.generate(spec)):
            now = m.ProcessingTime(
                clock_domain_id=generated.packets.sample_time.clock_domain_id,
                time_ns=generated.packets.sample_time.time_ns,
            )
            events: tuple[m.StationEvent, ...] = (
                *generated.packets.calibration_events,
                *generated.packets.rays,
                *generated.packets.diagnostics,
            )
            sampled = set()
            # The source harness reports completed MAIN processing even when no
            # observation event exists. Only capture metadata enters the plugin.
            for frame in generated.truth.camera_frames:
                if frame.camera_role is m.CameraRole.MAIN:
                    last[frame.station_id] = frame.capture_time
                    sampled.add(frame.station_id)
            progress = tuple(
                f.StationEventTimeProgress(
                    station_id=binding.station_id,
                    station_session_id=binding.virtual_session_id,
                    station_config_digest=configs[binding.station_id],
                    completed_capture_time=last[binding.station_id],
                    received_at=now,
                    stream_ended=False,
                )
                for binding in source.station_sessions
                if binding.station_id in sampled
            )
            rows.append(
                CenterInputStep(
                    step_no=number,
                    time=now,
                    solutions=generated.packets.calibration_solutions,
                    events=tuple(
                        m.ReceivedEvent(event=event, arrival_time=now)
                        for event in sorted(
                            events,
                            key=lambda event: (
                                event.envelope.station_id,
                                event.envelope.sequence_no,
                            ),
                        )
                    ),
                    progress=progress,
                    stream_ended=False,
                )
            )
        now = m.ProcessingTime(
            clock_domain_id=spec.timeline.clock_domain.clock_domain_id,
            time_ns=spec.timeline.end_time_ns + 1,
        )
        progress = tuple(
            f.StationEventTimeProgress(
                station_id=binding.station_id,
                station_session_id=binding.virtual_session_id,
                station_config_digest=configs[binding.station_id],
                completed_capture_time=last.get(binding.station_id),
                received_at=now,
                stream_ended=True,
            )
            for binding in source.station_sessions
        )
        rows.append(
            CenterInputStep(
                step_no=len(rows),
                time=now,
                solutions=(),
                events=(),
                progress=progress,
                stream_ended=True,
            )
        )
        return rows

    def test_reference_plugin_has_online_only_registration_and_full_diagnostics(self) -> None:
        algorithm = create_algorithm()
        algorithm.start(self.registration)
        bindings = tuple(algorithm._started)
        try:
            results = [algorithm.process(step) for step in self.steps()]
            measurements = [
                measurement for result in results for measurement in result.measurements
            ]
            tracks = [track for result in results for track in result.tracks]
            self.assertGreater(len(measurements), 2)
            self.assertEqual(
                len({measurement.measurement_id for measurement in measurements}), len(measurements)
            )
            self.assertTrue(
                all(measurement.status is m.FusionStatus.LOCATED for measurement in measurements)
            )
            self.assertIs(tracks[0].lifecycle, m.TrackLifecycle.TENTATIVE)
            confirmed = [track for track in tracks if track.lifecycle is m.TrackLifecycle.CONFIRMED]
            self.assertTrue(confirmed)
            self.assertEqual(len({track.track_id for track in tracks}), 1)
            for track in confirmed:
                np.testing.assert_allclose(
                    np.asarray(track.velocity_enu_mps),
                    np.asarray((1.0, 0.0, 0.0)),
                    rtol=0,
                    atol=1e-8,
                )
            batches = [batch for result in results for batch in result.batches]
            self.assertTrue(any(batch.admissions for batch in batches))
            self.assertTrue(any(batch.window_update.sealed_windows for batch in batches))
            self.assertTrue(
                any(batch.candidates and batch.associations and batch.geometry for batch in batches)
            )
            encoded = json.dumps([asdict(result) for result in results], allow_nan=False)
            self.assertNotIn("truth_object_id", encoded)
            self.assertNotIn("scene_definition", encoded)
            self.assertNotIn("experiment_perturbations", encoded)
            last = self.steps()[-1]
            future = replace(last, time=replace(last.time, time_ns=10**12), progress=())
            result = algorithm.process(future)
            self.assertFalse(result.measurements)
            self.assertFalse(result.tracks)
            self.assertTrue(all(not batch.window_update.sealed_windows for batch in result.batches))
        finally:
            algorithm.close()
            algorithm.close()
        self.assertTrue(
            all(
                getattr(binding.module, "_lifecycle_state", None) is ModuleState.STOPPED
                for binding in bindings
            )
        )
        with self.assertRaisesRegex(RuntimeError, "not running"):
            algorithm.process(self.steps()[0])

    def test_open_windows_prevent_wall_clock_ticks_from_advancing_tracks(self) -> None:
        steps = self.steps()
        first = self.adapter.process(steps[0])
        self.assertFalse(first.measurements)
        late_now = replace(steps[0].time, time_ns=10**12)
        tick = self.center.fusion.tick(late_now)
        self.assertFalse(tick.window_update.sealed_windows)
        self.assertFalse(tick.tracks)
        second = replace(
            steps[1],
            time=replace(late_now, time_ns=late_now.time_ns + 1),
            events=tuple(
                replace(event, arrival_time=replace(late_now, time_ns=late_now.time_ns + 1))
                for event in steps[1].events
            ),
            progress=tuple(
                replace(progress, received_at=replace(late_now, time_ns=late_now.time_ns + 1))
                for progress in steps[1].progress
            ),
        )
        result = self.adapter.process(second)
        self.assertEqual(len(result.measurements), 1)
        self.assertIs(result.tracks[0].lifecycle, m.TrackLifecycle.TENTATIVE)
        self.assertLess(result.tracks[0].event_time_ns, 10**9)
        future = self.center.fusion.tick(replace(late_now, time_ns=10**13))
        self.assertFalse(future.tracks)
        self.assertFalse(future.window_update.sealed_windows)

    def test_quarantined_control_events_unlock_rays_before_progress_seals(self) -> None:
        center, _ = declare_center(
            center_session_id=m.SessionId(self.registration.center_session_id),
            policy=self.registration.policy,
            clock_mapping=self.registration.clock_mapping,
        )
        first = self.steps()[0]
        rays = tuple(
            received for received in first.events if isinstance(received.event, m.RayEvent)
        )
        controls = tuple(
            received
            for received in first.events
            if isinstance(received.event, m.CalibrationStateEvent)
        )
        for received in (*rays, *controls):
            batch = center.fusion.process(received)
            self.assertIs(batch.admissions[-1].decision, f.AdmissionDecision.QUARANTINED)
            self.assertFalse(batch.measurements)
        for config in self.registration.stations:
            center.fusion.register_station(config)
        retried = center.fusion.tick(replace(first.time, time_ns=first.time.time_ns + 1))
        self.assertEqual(len(retried.admissions), 4)
        self.assertTrue(
            all(record.decision is f.AdmissionDecision.ACCEPTED for record in retried.admissions)
        )
        self.assertFalse(center.journal.quarantined())
        self.assertTrue(
            all(record.received.arrival_time == first.time for record in retried.admissions)
        )
        self.assertEqual(
            sum(
                receipt.disposition is f.WindowDisposition.BUFFERED
                for receipt in retried.window_update.receipts
            ),
            2,
        )
        final = self.steps()[-1]
        # End the sessions actually represented by these rays, not a new source run.
        sessions = {
            received.event.envelope.station_id: received.event.envelope.station_session_id
            for received in rays
        }
        batches = [
            center.fusion.report_progress(
                replace(
                    progress,
                    station_session_id=sessions[progress.station_id],
                    completed_capture_time=next(
                        received.event.capture_time
                        for received in rays
                        if isinstance(received.event, m.RayEvent)
                        and received.event.envelope.station_id == progress.station_id
                    ),
                )
            )
            for progress in final.progress
        ]
        self.assertEqual(sum(len(batch.measurements) for batch in batches), 1)

    def test_duplicate_conflict_and_late_events_cannot_republish_evidence(self) -> None:
        steps = self.steps()
        for step in steps:
            self.adapter.process(step)
        ray = next(
            received for received in steps[0].events if isinstance(received.event, m.RayEvent)
        )
        now = replace(steps[-1].time, time_ns=steps[-1].time.time_ns + 1)
        duplicate = self.center.fusion.process(replace(ray, arrival_time=now))
        self.assertIs(duplicate.admissions[-1].decision, f.AdmissionDecision.DUPLICATE)
        self.assertFalse(duplicate.measurements or duplicate.tracks)
        assert isinstance(ray.event, m.RayEvent)
        conflict = self.center.fusion.process(
            replace(ray, event=replace(ray.event, detection_score=0.123), arrival_time=now)
        )
        self.assertEqual(
            conflict.admissions[-1].reasons, (f.AdmissionReason.EVENT_ID_PAYLOAD_CONFLICT,)
        )
        late = self.center.fusion.process(
            replace(
                ray,
                event=replace(ray.event, envelope=replace(ray.event.envelope, sequence_no=999)),
                arrival_time=now,
            )
        )
        self.assertIs(
            late.window_update.receipts[-1].disposition, f.WindowDisposition.LATE_AFTER_SEAL
        )
        self.assertFalse(late.measurements or late.tracks)

    def test_ambiguous_window_keeps_diagnostics_without_creating_a_track(self) -> None:
        first = self.steps()[0]
        self.adapter.process(first)
        ray = next(received for received in first.events if isinstance(received.event, m.RayEvent))
        alternate = replace(
            ray, event=replace(ray.event, envelope=replace(ray.event.envelope, sequence_no=999))
        )
        self.center.fusion.process(alternate)
        batches = [
            self.center.fusion.report_progress(replace(progress, stream_ended=True))
            for progress in first.progress
        ]
        self.assertTrue(
            any(
                batch.associations
                and batch.associations[0].status is f.AssociationBatchStatus.ASSOCIATION_AMBIGUOUS
                for batch in batches
            )
        )
        self.assertFalse(any(batch.measurements or batch.tracks for batch in batches))

    def test_empty_and_zero_sample_inputs_never_create_tracks(self) -> None:
        for start, duration in ((0, 0.07), (1, 0.001)):
            with self.subTest(start=start):
                document = json.loads((ROOT / "scenarios/center_clean.json").read_text())
                document.update(duration_s=duration, targets=[])
                for station in document["stations"]:
                    station["start_s"] = start
                spec = compile_authoring(document)
                run = development_run(spec, Path("unused-reference-test-output"))
                registration = replace(
                    self.registration,
                    stations=spec.station_configs,
                    policy=run.fusion_policy,
                    clock_mapping=run.processing_to_capture_clock,
                )
                algorithm = create_algorithm()
                algorithm.start(registration)
                try:
                    results = [algorithm.process(step) for step in self.steps(spec)]
                    self.assertFalse(
                        any(result.measurements or result.tracks for result in results)
                    )
                    windows = [
                        window
                        for result in results
                        for batch in result.batches
                        for window in batch.window_update.sealed_windows
                    ]
                    if start:
                        self.assertFalse(windows)
                    else:
                        self.assertTrue(windows)
                        self.assertTrue(all(not window.rays for window in windows))
                finally:
                    algorithm.close()

    def test_empty_sealed_windows_drive_coasting_and_deletion(self) -> None:
        steps = self.steps()
        for step in steps[:3]:
            self.adapter.process(step)
        events: list[m.TrackEvent] = []
        for time_ns in (1_500_000_000, 4_000_000_000):
            for original in steps[2].progress:
                capture = original.completed_capture_time
                assert capture is not None
                progress = replace(
                    original,
                    completed_capture_time=replace(
                        capture, exposure_start_ns=time_ns, exposure_end_ns=time_ns
                    ),
                    received_at=replace(original.received_at, time_ns=time_ns),
                )
                batch = self.center.fusion.report_progress(progress)
                events.extend(batch.tracks)
        self.assertTrue(any(event.lifecycle is m.TrackLifecycle.COASTING for event in events))
        deleted = [event for event in events if event.lifecycle is m.TrackLifecycle.DELETED]
        self.assertEqual(len(deleted), 1)
        self.assertLessEqual(deleted[0].event_time_ns, 4_000_000_000)

    def test_default_inspector_remains_input_only(self) -> None:
        inspector = InputInspector()
        inspector.start(self.registration)
        try:
            result = inspector.process(self.steps()[0])
            self.assertFalse(result.measurements)
            self.assertFalse(result.tracks)
            self.assertFalse(result.batches)
        finally:
            inspector.close()

    def test_online_composition_rejects_inconsistent_clock_and_frame_policy(self) -> None:
        for policy, mapping in (
            (
                replace(
                    self.registration.policy,
                    admission=replace(
                        self.registration.policy.admission, world_frame_id=m.WorldFrameId("other")
                    ),
                ),
                self.registration.clock_mapping,
            ),
            (
                self.registration.policy,
                replace(self.registration.clock_mapping, capture_clock_domain_id="other"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "coordinate/clock"):
                declare_center(
                    center_session_id=m.SessionId(self.registration.center_session_id),
                    policy=policy,
                    clock_mapping=mapping,
                )

    def test_reference_plugin_cli_emits_clean_measurements_tracks_and_stage_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = json.loads((ROOT / "scenarios/center_clean.json").read_text())
            document["duration_s"] = 0.12
            scenario = root / "scene.json"
            scenario.write_text(json.dumps(document), encoding="utf-8")
            output = root / "run"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "chongming.development",
                    "--scenario",
                    str(scenario),
                    "--output",
                    str(output),
                    "--run",
                    "--tick-hz",
                    "240",
                    "--algorithm",
                    "chongming.reference:create_algorithm",
                ],
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = [
                json.loads(line)
                for line in (output / "center-output.jsonl").read_text().splitlines()
            ]
            self.assertTrue(rows[-1]["stream_ended"])
            measurements = [measurement for row in rows for measurement in row["measurements"]]
            tracks = [track for row in rows for track in row["tracks"]]
            self.assertGreater(len(measurements), 2)
            self.assertTrue(all(measurement["status"] == "located" for measurement in measurements))
            self.assertTrue(any(track["lifecycle"] == "confirmed" for track in tracks))
            for measurement in measurements:
                expected = np.asarray((-15 + measurement["reference_time_ns"] / 1e9, 62, 20))
                np.testing.assert_allclose(
                    np.asarray(measurement["position_enu_m"]), expected, rtol=0, atol=1e-9
                )
            for track in tracks:
                if track["lifecycle"] == "confirmed":
                    np.testing.assert_allclose(
                        np.asarray(track["velocity_enu_mps"]),
                        np.asarray((1.0, 0.0, 0.0)),
                        rtol=0,
                        atol=1e-8,
                    )
            self.assertTrue(
                any(
                    batch["geometry"] and batch["associations"] and batch["candidates"]
                    for row in rows
                    for batch in row["batches"]
                )
            )
            self.assertNotIn("truth_object_id", (output / "center-output.jsonl").read_text())
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["algorithm"], "ReferenceFusionAlgorithm")
            self.assertFalse(manifest["algorithms_complete"])
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["state"], "complete")
            self.assertFalse(summary["shutdown_errors"])
            self.assertFalse((output / "video").exists())


if __name__ == "__main__":
    unittest.main()
