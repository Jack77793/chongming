from __future__ import annotations

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from chongming.calibration import (
    CalibrationHistoryStore,
    CalibrationStateMachine,
    CheckerboardGeometry,
    OpenCVCalibrationEstimator,
    OpenCVReferenceRecognizer,
)
from chongming.contracts import device, model
from chongming.modules.device import EventSequencerModule

IDENTITY: model.Matrix3x3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
ZERO_COVARIANCE: model.Matrix3x3 = ((0.0, 0.0, 0.0),) * 3
SCORE = model.ScoreDefinition(
    definition_id="test-score",
    kind=model.ScoreKind.RANKING,
    minimum=0.0,
    maximum=1.0,
    cross_station_comparison_group=None,
)


def _config(
    *,
    camera_matrix: model.Matrix3x3 = ((800.0, 0.0, 320.0), (0.0, 800.0, 240.0), (0.0, 0.0, 1.0)),
) -> model.StationConfig:
    station_id = model.StationId("station-test")
    digest = model.ConfigDigest("config-test")
    camera = model.CameraModel(
        intrinsics=model.CameraIntrinsics(
            image_width_px=640,
            image_height_px=480,
            matrix_px=camera_matrix,
        ),
        distortion=model.CameraDistortion(model="none", coefficients=()),
    )
    board_corners = (
        (-0.1485, -0.105, 10.0),
        (0.1485, -0.105, 10.0),
        (0.1485, 0.105, 10.0),
        (-0.1485, 0.105, 10.0),
    )
    landmarks = tuple(
        model.SurveyedLandmark(
            landmark_id=f"board-corner-{index}",
            position_enu_m=point,
            covariance_enu_m2=ZERO_COVARIANCE,
            visual_description="checkerboard corner",
        )
        for index, point in enumerate(board_corners)
    )
    return model.StationConfig(
        station_id=station_id,
        config_version="test-v1",
        config_digest=digest,
        world_frame_id=model.WorldFrameId("test-enu"),
        geodetic_anchor=model.GeodeticAnchor(
            anchor_id="test-anchor",
            datum="WGS84",
            datum_epoch="test",
            latitude_deg=0.0,
            longitude_deg=0.0,
            height_m=0.0,
            height_reference="ellipsoidal",
        ),
        main_camera_position_enu_m=(0.0, 0.0, 0.0),
        baseline_orientation_enu_from_main=IDENTITY,
        main_camera=camera,
        auxiliary_camera=camera,
        fixed_rotation_aux_from_main=IDENTITY,
        full_rigid_transform_aux_from_main=None,
        reference_anchor=model.SurveyedReference(
            reference_id="board",
            world_frame_id=model.WorldFrameId("test-enu"),
            landmarks=landmarks,
        ),
        reference_scene_version="test-v1",
        installation_uncertainty=model.InstallationUncertainty(
            state_ref=model.InstallationStateRef(
                station_id=station_id,
                config_digest=digest,
                state_version="test-v1",
            ),
            position_covariance_enu_m2=ZERO_COVARIANCE,
            orientation_covariance_enu_rad2=ZERO_COVARIANCE,
            fixed_extrinsic_covariance_aux_rad2=ZERO_COVARIANCE,
            measurement_source="test",
        ),
    )


def _capture(start: int = 0, end: int = 10) -> model.CaptureTime:
    return model.CaptureTime(
        clock_domain_id="test-clock",
        exposure_start_ns=start,
        exposure_end_ns=end,
        clock_uncertainty_ns=0,
        rolling_shutter_model="none",
    )


def _policy(**overrides: object) -> device.CalibrationPolicy:
    values: dict[str, object] = {
        "update_period_ns": 1,
        "stale_after_ns": 100,
        "recovery_success_count": 2,
        "minimum_reference_count": 4,
        "maximum_reprojection_rmse_px": 1.0,
        "maximum_rotation_only_parallax_px": None,
        "estimator_id": "opencv-ippe-test",
    }
    values.update(overrides)
    return device.CalibrationPolicy(**values)  # type: ignore[arg-type]


def _inner_points(
    config: model.StationConfig, rotation_enu_from_aux: np.ndarray
) -> tuple[tuple[float, float], ...]:
    geometry = CheckerboardGeometry()
    assert isinstance(config.reference_anchor, model.SurveyedReference)
    corners = np.asarray(
        tuple(
            tuple(float(value) for value in landmark.position_enu_m)
            for landmark in config.reference_anchor.landmarks
        ),
        dtype=np.float64,
    )
    points: list[tuple[float, float]] = []
    rotation_camera_from_enu = rotation_enu_from_aux.T
    translation = np.zeros(3, dtype=np.float64)
    if config.full_rigid_transform_aux_from_main is not None:
        translation = -rotation_camera_from_enu @ np.asarray(
            config.main_camera_position_enu_m, dtype=np.float64
        ) + np.asarray(config.full_rigid_transform_aux_from_main.translation_a_m, dtype=np.float64)
    for row in range(4):
        for column in range(6):
            alpha = (
                0.5
                + (column - (geometry.columns - 1) * 0.5)
                * geometry.square_pitch_x_m
                / geometry.board_width_m
            )
            beta = (
                0.5
                + (row - (geometry.rows - 1) * 0.5)
                * geometry.square_pitch_y_m
                / geometry.board_height_m
            )
            world = (
                (1.0 - alpha) * (1.0 - beta) * corners[0]
                + alpha * (1.0 - beta) * corners[1]
                + alpha * beta * corners[2]
                + (1.0 - alpha) * beta * corners[3]
            )
            camera = rotation_camera_from_enu @ world + translation
            points.append(
                (800.0 * camera[0] / camera[2] + 320.0, 800.0 * camera[1] / camera[2] + 240.0)
            )
    return tuple(points)


def _references(
    config: model.StationConfig,
    rotation_enu_from_aux: np.ndarray,
    *,
    start: int = 0,
) -> device.ReferenceObservationBatch:
    observations = tuple(
        device.ReferenceObservation(
            reference_id=f"board:inner-r{row}-c{column}",
            centroid_px=point,
            centroid_covariance_px2=None,
            match_score=1.0,
            match_score_definition=SCORE,
        )
        for (row, column), point in zip(
            ((row, column) for row in range(4) for column in range(6)),
            _inner_points(config, rotation_enu_from_aux),
        )
    )
    return device.ReferenceObservationBatch(
        auxiliary_frame_ref=model.FrameRef(
            station_id=config.station_id,
            station_session_id=model.SessionId("session-test"),
            camera_role=model.CameraRole.AUXILIARY,
            frame_id=model.FrameId("aux-1"),
        ),
        capture_time=_capture(start, start + 10),
        processing_status=model.ProcessingStatus.PROCESSED,
        observations=observations,
        reason_codes=(),
    )


class CalibrationTests(unittest.TestCase):
    def test_recognizer_returns_subpixel_corners_from_gray_stride(self) -> None:
        config = _config()
        image = np.zeros((480, 640), dtype=np.uint8)
        square = 40
        start_x, start_y = 200, 140
        for row in range(5):
            for column in range(7):
                if (row + column) % 2 == 0:
                    image[
                        start_y + row * square : start_y + (row + 1) * square,
                        start_x + column * square : start_x + (column + 1) * square,
                    ] = 255
        frame = model.CameraFrame(
            station_id=config.station_id,
            station_session_id=model.SessionId("session-test"),
            camera_role=model.CameraRole.AUXILIARY,
            frame_id=model.FrameId("aux-image"),
            capture_time=_capture(),
            image=model.ImagePlane(
                width_px=640,
                height_px=480,
                pixel_format=model.PixelFormat.GRAY8,
                row_stride_bytes=672,
                pixels=np.pad(image, ((0, 0), (0, 32)), constant_values=0).tobytes(),
            ),
            camera_config_ref=config.config_digest,
        )
        batch = OpenCVReferenceRecognizer().recognize(frame, config)
        self.assertEqual(batch.processing_status, model.ProcessingStatus.PROCESSED)
        self.assertEqual(len(batch.observations), 24)
        self.assertTrue(
            {"opencv_find_chessboard_corners_classic", "opencv_find_chessboard_corners_sb"}
            & set(batch.reason_codes)
        )
        self.assertIn("subpixel_corners", batch.reason_codes)

        with patch("chongming.calibration.cv2.findChessboardCorners", return_value=(False, None)):
            fallback = OpenCVReferenceRecognizer().recognize(frame, config)
        self.assertEqual(fallback.processing_status, model.ProcessingStatus.PROCESSED)
        self.assertEqual(len(fallback.observations), 24)
        self.assertIn("opencv_find_chessboard_corners_sb", fallback.reason_codes)

    def test_estimator_uses_installation_prior_for_small_rotation(self) -> None:
        config = _config()
        angle = math.radians(0.5)
        observed = np.asarray(
            (
                (math.cos(angle), -math.sin(angle), 0.0),
                (math.sin(angle), math.cos(angle), 0.0),
                (0.0, 0.0, 1.0),
            )
        )
        attempt = OpenCVCalibrationEstimator(_policy()).estimate(
            _references(config, observed),
            config,
            model.ProcessingTime(clock_domain_id="test-clock", time_ns=20),
        )
        self.assertEqual(attempt.status, device.CalibrationAttemptStatus.ESTIMATED)
        assert attempt.estimate is not None
        estimated = np.asarray(attempt.estimate.rotation_enu_from_main)
        self.assertLess(np.linalg.norm(estimated - observed), 1e-5)

    def test_estimator_holds_known_auxiliary_center_during_visual_fit(self) -> None:
        config = replace(
            _config(),
            full_rigid_transform_aux_from_main=model.RigidTransform(
                rotation_a_from_b=IDENTITY,
                translation_a_m=(0.0, 0.0, 0.0),
            ),
        )
        references = _references(config, np.eye(3))
        noisy = tuple(
            device.ReferenceObservation(
                reference_id=observation.reference_id,
                centroid_px=(
                    observation.centroid_px[0] + (0.25 if index % 3 == 0 else -0.25),
                    observation.centroid_px[1] + (0.15 if index % 4 == 0 else -0.15),
                ),
                centroid_covariance_px2=None,
                match_score=observation.match_score,
                match_score_definition=observation.match_score_definition,
            )
            for index, observation in enumerate(references.observations)
        )
        references = replace(references, observations=noisy)
        attempt = OpenCVCalibrationEstimator(_policy()).estimate(
            references,
            config,
            model.ProcessingTime(clock_domain_id="test-clock", time_ns=20),
        )
        self.assertEqual(attempt.status, device.CalibrationAttemptStatus.ESTIMATED)
        assert attempt.estimate is not None
        reprojection_rmse = attempt.estimate.quality.reprojection_rmse_px
        assert reprojection_rmse is not None
        self.assertLess(reprojection_rmse, 0.5)
        angular_residual = attempt.estimate.quality.angular_residual_rad
        assert angular_residual is not None
        self.assertLess(angular_residual, 1e-6)

    def test_estimator_rotates_auxiliary_center_around_fixed_main_center(self) -> None:
        config = replace(
            _config(),
            main_camera_position_enu_m=(0.2, -0.15, 0.35),
            full_rigid_transform_aux_from_main=model.RigidTransform(
                rotation_a_from_b=IDENTITY,
                translation_a_m=(0.35, 0.1, -0.05),
            ),
        )
        angle = math.radians(0.8)
        observed = np.asarray(
            (
                (math.cos(angle), -math.sin(angle), 0.0),
                (math.sin(angle), math.cos(angle), 0.0),
                (0.0, 0.0, 1.0),
            )
        )
        attempt = OpenCVCalibrationEstimator(_policy()).estimate(
            _references(config, observed),
            config,
            model.ProcessingTime(clock_domain_id="test-clock", time_ns=20),
        )
        self.assertEqual(attempt.status, device.CalibrationAttemptStatus.ESTIMATED)
        assert attempt.estimate is not None
        estimated = np.asarray(attempt.estimate.rotation_enu_from_main)
        self.assertLess(np.linalg.norm(estimated - observed), 1e-5)
        reprojection_rmse = attempt.estimate.quality.reprojection_rmse_px
        assert reprojection_rmse is not None
        self.assertLess(reprojection_rmse, 1e-5)

    def test_estimator_rejects_rotation_outside_one_degree_prior(self) -> None:
        config = _config()
        angle = math.radians(2.0)
        observed = np.asarray(
            (
                (math.cos(angle), -math.sin(angle), 0.0),
                (math.sin(angle), math.cos(angle), 0.0),
                (0.0, 0.0, 1.0),
            )
        )
        attempt = OpenCVCalibrationEstimator(_policy()).estimate(
            _references(config, observed),
            config,
            model.ProcessingTime(clock_domain_id="test-clock", time_ns=20),
        )
        self.assertEqual(attempt.status, device.CalibrationAttemptStatus.GEOMETRY_INVALID)

    def test_estimator_rejects_large_reprojection_residual(self) -> None:
        config = _config()
        observations = list(_references(config, np.eye(3)).observations)
        observations[0] = device.ReferenceObservation(
            reference_id=observations[0].reference_id,
            centroid_px=(observations[0].centroid_px[0] + 2.0, observations[0].centroid_px[1]),
            centroid_covariance_px2=None,
            match_score=1.0,
            match_score_definition=SCORE,
        )
        references = _references(config, np.eye(3))
        references = device.ReferenceObservationBatch(
            auxiliary_frame_ref=references.auxiliary_frame_ref,
            capture_time=references.capture_time,
            processing_status=references.processing_status,
            observations=tuple(observations),
            reason_codes=(),
        )
        attempt = OpenCVCalibrationEstimator(
            _policy(maximum_reprojection_rmse_px=0.1),
            maximum_correction_rad=math.radians(20.0),
        ).estimate(
            references,
            config,
            model.ProcessingTime(clock_domain_id="test-clock", time_ns=20),
        )
        self.assertEqual(attempt.status, device.CalibrationAttemptStatus.FAILED)
        self.assertIn("reprojection_rmse_exceeded", attempt.reason_codes)

    def test_estimator_rejects_insufficient_points(self) -> None:
        config = _config()
        references = _references(config, np.eye(3))
        attempt = OpenCVCalibrationEstimator(_policy()).estimate(
            device.ReferenceObservationBatch(
                auxiliary_frame_ref=references.auxiliary_frame_ref,
                capture_time=references.capture_time,
                processing_status=references.processing_status,
                observations=references.observations[:3],
                reason_codes=(),
            ),
            config,
            model.ProcessingTime(clock_domain_id="test-clock", time_ns=20),
        )
        self.assertEqual(attempt.status, device.CalibrationAttemptStatus.REFERENCE_UNAVAILABLE)

    def test_history_does_not_use_future_solution_for_earlier_main_frame(self) -> None:
        config = _config()
        history = CalibrationHistoryStore(
            config.station_id,
            model.SessionId("session-test"),
            config.config_digest,
        )
        solution_ref = model.CalibrationSolutionRef(
            station_id=config.station_id,
            station_session_id=model.SessionId("session-test"),
            solution_id=model.CalibrationSolutionId("solution-1"),
        )
        solution = model.CalibrationSolution(
            solution_ref=solution_ref,
            station_config_digest=config.config_digest,
            world_frame_id=config.world_frame_id,
            reference_capture_time=_capture(100, 110),
            accepted_time=model.ProcessingTime(clock_domain_id="test-clock", time_ns=120),
            valid_interval=model.TimeInterval(
                clock_domain_id="test-clock", start_ns=105, end_ns=None
            ),
            main_camera_position_enu_m=config.main_camera_position_enu_m,
            rotation_enu_from_main=IDENTITY,
            correction_covariance_enu_rad2=ZERO_COVARIANCE,
            quality=model.CalibrationQuality(
                source="test",
                matched_reference_count=24,
                reprojection_rmse_px=0.1,
                angular_residual_rad=0.0,
                reason_codes=(),
            ),
        )
        history.record_solution(solution)
        history.record_state(
            model.CalibrationState(
                station_id=config.station_id,
                station_session_id=model.SessionId("session-test"),
                station_config_digest=config.config_digest,
                state_version=1,
                health=model.CalibrationHealth.VALID,
                solution_ref=solution_ref,
                observed_at=model.ProcessingTime(clock_domain_id="test-clock", time_ns=120),
                last_trusted_time=model.ProcessingTime(clock_domain_id="test-clock", time_ns=120),
                usable_interval=solution.valid_interval,
                reason_codes=(),
            )
        )
        before = history.snapshot_at(_capture(0, 10))
        after = history.snapshot_at(_capture(130, 140))
        self.assertIsNone(before.solution)
        self.assertEqual(after.solution, solution)

    def test_state_machine_freezes_and_then_stales_last_solution(self) -> None:
        config = _config()
        policy = _policy(recovery_success_count=1, stale_after_ns=50)
        history = CalibrationHistoryStore(
            config.station_id, model.SessionId("session-test"), config.config_digest
        )
        sequencer = EventSequencerModule(
            station_id=config.station_id,
            station_session_id=model.SessionId("session-test"),
            schema_version="event-v1",
        )
        machine = CalibrationStateMachine(policy, history, sequencer)
        estimator = OpenCVCalibrationEstimator(policy)
        successful = estimator.estimate(
            _references(config, np.eye(3)),
            config,
            model.ProcessingTime(clock_domain_id="test-clock", time_ns=20),
        )
        valid = machine.consider(successful, config)
        self.assertEqual(valid.state_event.state.health, model.CalibrationHealth.VALID)
        failed = device.CalibrationAttempt(
            status=device.CalibrationAttemptStatus.REFERENCE_UNAVAILABLE,
            estimate=None,
            reference_frame_ref=successful.reference_frame_ref,
            reference_capture_time=_capture(20, 30),
            completed_at=model.ProcessingTime(clock_domain_id="test-clock", time_ns=30),
            reason_codes=("checkerboard_not_found",),
        )
        frozen = machine.consider(failed, config)
        self.assertEqual(frozen.state_event.state.health, model.CalibrationHealth.FROZEN)
        stale = machine.tick(model.ProcessingTime(clock_domain_id="test-clock", time_ns=71), config)
        assert stale is not None
        self.assertEqual(stale.state_event.state.health, model.CalibrationHealth.STALE)

    def test_state_machine_requires_consecutive_recovery_samples(self) -> None:
        config = _config()
        policy = _policy(recovery_success_count=2)
        history = CalibrationHistoryStore(
            config.station_id, model.SessionId("session-test"), config.config_digest
        )
        sequencer = EventSequencerModule(
            station_id=config.station_id,
            station_session_id=model.SessionId("session-test"),
            schema_version="event-v1",
        )
        machine = CalibrationStateMachine(policy, history, sequencer)
        estimator = OpenCVCalibrationEstimator(policy)
        initial = estimator.estimate(
            _references(config, np.eye(3), start=0),
            config,
            model.ProcessingTime(clock_domain_id="test-clock", time_ns=20),
        )
        machine.consider(initial, config)
        failed = device.CalibrationAttempt(
            status=device.CalibrationAttemptStatus.REFERENCE_UNAVAILABLE,
            estimate=None,
            reference_frame_ref=initial.reference_frame_ref,
            reference_capture_time=_capture(20, 30),
            completed_at=model.ProcessingTime(clock_domain_id="test-clock", time_ns=30),
            reason_codes=("checkerboard_not_found",),
        )
        machine.consider(failed, config)
        first = estimator.estimate(
            _references(config, np.eye(3), start=40),
            config,
            model.ProcessingTime(clock_domain_id="test-clock", time_ns=50),
        )
        recovering = machine.consider(first, config)
        self.assertEqual(recovering.state_event.state.health, model.CalibrationHealth.RECOVERING)
        self.assertIsNone(recovering.solution)
        second = estimator.estimate(
            _references(config, np.eye(3), start=60),
            config,
            model.ProcessingTime(clock_domain_id="test-clock", time_ns=70),
        )
        recovered = machine.consider(second, config)
        self.assertEqual(recovered.state_event.state.health, model.CalibrationHealth.VALID)
        self.assertIsNotNone(recovered.solution)


if __name__ == "__main__":
    unittest.main()
