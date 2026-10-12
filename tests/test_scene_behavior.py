from __future__ import annotations

import unittest
from dataclasses import replace

from chongming.contracts.model import CameraRole
from chongming.contracts.scene import (
    CameraMetadata,
    CameraStateTruth,
    ClockDomainSpec,
    FlyingObjectTruth,
    KinematicState,
    SceneCameraDistortion,
    SceneCameraIntrinsics,
    SceneDistortionModel,
    SegmentOcclusionTruth,
    SimulationTime,
    SphereGeometry,
    SurfaceAppearance,
    TerrainModel,
    TerrainSpec,
    TimelineSpec,
    TimeScale,
    TrajectoryInterpolation,
    TrajectoryKnot,
    TrajectorySpec,
    VisibilityQuery,
    WorldObjectId,
    WorldObjectKind,
    WorldObjectSpec,
)
from chongming.modules.scene import (
    CameraProjectionModule,
    ExperimentClockModule,
    SceneLogicModule,
    TrajectoryModule,
    VisibilityModule,
)
from tests.fixtures import architecture_specs

IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def state(position: tuple[float, float, float], velocity=(0.0, 0.0, 0.0)) -> KinematicState:
    return KinematicState(
        position_enu_m=position,
        velocity_enu_mps=velocity,
        rotation_enu_from_body=IDENTITY,
        angular_velocity_body_radps=(0.0, 0.0, 0.0),
    )


def camera() -> CameraStateTruth:
    return CameraStateTruth(
        station_id="station-1",
        camera_role=CameraRole.MAIN,
        metadata=CameraMetadata(
            camera_id="camera-1",
            display_name="main",
            profile_id="test",
            sensor_format=None,
            lens_focal_length_mm=None,
            nominal_diagonal_fov_deg=60.0,
            nominal_frame_rate_hz=30.0,
        ),
        sample_time=SimulationTime("clock", 0, 0),
        optical_state=state((0.0, 0.0, 0.0)),
        actual_intrinsics=SceneCameraIntrinsics(100, 100, 100.0, 100.0, 50.0, 50.0, 0.0),
        actual_distortion=SceneCameraDistortion(
            SceneDistortionModel.NONE, (0.0, 0.0, 0.0), (0.0, 0.0)
        ),
    )


def terrain() -> TerrainSpec:
    return TerrainSpec(
        terrain_id=WorldObjectId("ground"),
        model=TerrainModel.FLAT,
        base_elevation_m=-100.0,
        maximum_relief_m=0.0,
        heightfield_asset_uri=None,
        heightfield_asset_digest=None,
        appearance=SurfaceAppearance((0.2, 0.2, 0.2), 0.5),
        occludes_sight_lines=True,
    )


class ScenePrimitiveTests(unittest.TestCase):
    def test_clock_is_explicit_and_monotonic(self) -> None:
        clock = ExperimentClockModule()
        timeline = TimelineSpec(
            ClockDomainSpec("clock", TimeScale.EXPERIMENT_ELAPSED, "test", None), 10, 30, 5
        )
        clock.initialize(timeline)
        self.assertEqual(clock.read(), SimulationTime("clock", 10, 0))
        self.assertEqual(clock.advance_to(20), SimulationTime("clock", 20, 1))
        with self.assertRaises(ValueError):
            clock.advance_to(19)
        with self.assertRaises(ValueError):
            clock.advance_to(31)

    def test_linear_trajectory_samples_position_and_velocity(self) -> None:
        trajectory = TrajectorySpec(
            TrajectoryInterpolation.LINEAR,
            (
                TrajectoryKnot(0, state((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))),
                TrajectoryKnot(10, state((10.0, 2.0, 0.0), (3.0, 2.0, 0.0))),
            ),
        )
        result = TrajectoryModule().sample(trajectory, SimulationTime("clock", 5, 0))
        self.assertEqual(result.position_enu_m, (5.0, 1.0, 0.0))
        self.assertEqual(result.velocity_enu_mps, (2.0, 1.0, 0.0))

    def test_hold_trajectory_switches_at_the_knot(self) -> None:
        trajectory = TrajectorySpec(
            TrajectoryInterpolation.HOLD,
            (
                TrajectoryKnot(0, state((0.0, 0.0, 0.0))),
                TrajectoryKnot(10, state((10.0, 0.0, 0.0))),
            ),
        )
        result = TrajectoryModule().sample(trajectory, SimulationTime("clock", 10, 0))
        self.assertEqual(result.position_enu_m, (10.0, 0.0, 0.0))

    def test_projection_uses_actual_intrinsics_and_rejects_behind_camera(self) -> None:
        projector = CameraProjectionModule()
        visible = projector.project(camera(), (0.0, 0.0, 10.0))
        self.assertEqual(visible.centroid_px, (50.0, 50.0))
        self.assertTrue(visible.center_in_field_of_view)
        behind = projector.project(camera(), (0.0, 0.0, -1.0))
        self.assertIsNone(behind.centroid_px)
        self.assertFalse(behind.in_front_of_camera)

    def test_visibility_only_blocks_the_finite_segment(self) -> None:
        obstacle = WorldObjectSpec(
            object_id=WorldObjectId("obstacle"),
            display_name="obstacle",
            kind=WorldObjectKind.OBSTACLE,
            category="test",
            pose=state((0.0, 0.0, 5.0)),
            geometry=SphereGeometry(1.0),
            appearance=SurfaceAppearance((0.5, 0.5, 0.5), 0.5),
            occludes_sight_lines=True,
        )
        target = WorldObjectSpec(
            object_id=WorldObjectId("target"),
            display_name="target",
            kind=WorldObjectKind.FIXED_REFERENCE,
            category="test",
            pose=state((0.0, 0.0, 10.0)),
            geometry=SphereGeometry(0.5),
            appearance=SurfaceAppearance((0.5, 0.5, 0.5), 0.5),
            occludes_sight_lines=False,
        )
        projection = CameraProjectionModule().project(camera(), target.pose.position_enu_m)
        query = VisibilityQuery(
            sample_time=SimulationTime("clock", 0, 0),
            camera=camera(),
            target=FlyingObjectTruth(
                truth_object_id="truth-1",
                display_name="target",
                model_name="sphere",
                sample_time=SimulationTime("clock", 0, 0),
                state=target.pose,
                center_reference_point_enu_m=target.pose.position_enu_m,
                geometry=target.geometry,
                appearance=target.appearance,
            ),
            projection=projection,
            terrain=terrain(),
            world_objects=(obstacle,),
            vegetation=(),
        )
        result = VisibilityModule().resolve(query)
        self.assertFalse(result.capture_eligible)
        self.assertEqual(len(result.occlusions), 1)
        self.assertIsInstance(result.occlusions[0], SegmentOcclusionTruth)

        beyond = replace(
            query,
            target=replace(query.target, center_reference_point_enu_m=(0.0, 0.0, 3.0)),
            projection=CameraProjectionModule().project(camera(), (0.0, 0.0, 3.0)),
        )
        self.assertTrue(VisibilityModule().resolve(beyond).capture_eligible)

    def test_scene_logic_samples_one_immutable_snapshot(self) -> None:
        spec, _ = architecture_specs()
        trajectory = object.__new__(TrajectoryModule)
        projector = object.__new__(CameraProjectionModule)
        visibility = object.__new__(VisibilityModule)
        logic = object.__new__(SceneLogicModule)
        SceneLogicModule.__init__(logic, trajectory, projector, visibility)
        logic.initialize(spec.scene_definition, spec.perturbations, spec.random_seed)
        snapshot = logic.sample(SimulationTime("structure-clock", 0, 0))
        self.assertEqual(snapshot.sample_time.time_ns, 0)
        self.assertEqual(len(snapshot.cameras), len(spec.scene_definition.cameras))
        self.assertEqual(snapshot.flying_objects, ())
        self.assertEqual(snapshot.target_visibility, ())


if __name__ == "__main__":
    unittest.main()
