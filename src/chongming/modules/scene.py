"""Deterministic scene-generation primitives.

The first implementation deliberately keeps rendering and online algorithms
separate.  The classes in this module operate only on immutable scene
contracts and the experiment clock; no wall clock or global random state is
consulted.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import replace
from hashlib import blake2b

from chongming.contracts.model import (
    CameraFrame,
    CameraRole,
    CaptureTime,
    FrameId,
    ImagePlane,
    Matrix3x3,
    PixelFormat,
    StationSessionBinding,
    Vec3M,
)
from chongming.contracts.scene import (
    CameraFrameTruth,
    CameraStateTruth,
    EvaluationRecord,
    EvaluationReport,
    EvaluationSpec,
    ExperimentPerturbations,
    FlyingObjectTruth,
    FrameImagingInput,
    ImageGenerationRequest,
    ImagingBatch,
    ImagingTruthBatch,
    KinematicState,
    LandmarkVisibilityTruth,
    LogicalSceneSnapshot,
    MonitoringFrame,
    MonitoringSnapshot,
    MonitoringSpec,
    OnlineEvaluationData,
    ProjectedLandmarkTruth,
    ProjectedTargetTruth,
    ProjectionTruth,
    ReferenceVisibilityQuery,
    ScenarioPacketOutput,
    ScenarioSpec,
    ScenarioStep,
    ScenarioTruth,
    ScenarioTruthOutput,
    ScenarioVideoOutput,
    SceneCameraSpec,
    SceneDefinition,
    SegmentOcclusionTruth,
    SightSegment,
    SimulationTime,
    TargetVisibilityTruth,
    TimelineSpec,
    TrajectorySpec,
    VirtualObservationBatch,
    VirtualObservationRequest,
    VirtualObservationTruthBatch,
    VisibilityQuery,
)
from chongming.modules.base import ModuleShell
from chongming.ports.device import EventSequencer
from chongming.ports.scene import (
    CameraImager,
    CameraProjector,
    ExperimentClock,
    SceneLogic,
    TrajectorySampler,
    VirtualObservationGenerator,
    VisibilityResolver,
)

_EPS = 1e-9
_TODO = "TODO(stage-3): business logic is intentionally absent from the architecture skeleton"


def _group_exposures(
    exposures: list[tuple[int, SceneCameraSpec]],
) -> Iterator[tuple[int, list[tuple[int, SceneCameraSpec]]]]:
    index = 0
    while index < len(exposures):
        midpoint = exposures[index][0]
        end = index + 1
        while end < len(exposures) and exposures[end][0] == midpoint:
            end += 1
        yield midpoint, exposures[index:end]
        index = end


def _sub(a: Vec3M, b: Vec3M) -> Vec3M:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3M, b: Vec3M) -> Vec3M:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3M, value: float) -> Vec3M:
    return (a[0] * value, a[1] * value, a[2] * value)


def _dot(a: Vec3M, b: Vec3M) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _mat_vec(matrix: tuple[tuple[float, float, float], ...], vector: Vec3M) -> Vec3M:
    result: Vec3M = (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
    )
    return result


def _transpose(
    matrix: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    return (
        (matrix[0][0], matrix[1][0], matrix[2][0]),
        (matrix[0][1], matrix[1][1], matrix[2][1]),
        (matrix[0][2], matrix[1][2], matrix[2][2]),
    )


def _lerp(a: Vec3M, b: Vec3M, fraction: float) -> Vec3M:
    return _add(a, _scale(_sub(b, a), fraction))


def _rotation_from_small_angle(offset: Vec3M) -> Matrix3x3:
    """First-order camera-frame perturbation, sufficient for the baseline model."""
    x, y, z = offset
    return ((1.0, -z, y), (z, 1.0, -x), (-y, x, 1.0))


def _mat_mul(left: Matrix3x3, right: Matrix3x3) -> Matrix3x3:
    return (
        (
            sum(left[0][k] * right[k][0] for k in range(3)),
            sum(left[0][k] * right[k][1] for k in range(3)),
            sum(left[0][k] * right[k][2] for k in range(3)),
        ),
        (
            sum(left[1][k] * right[k][0] for k in range(3)),
            sum(left[1][k] * right[k][1] for k in range(3)),
            sum(left[1][k] * right[k][2] for k in range(3)),
        ),
        (
            sum(left[2][k] * right[k][0] for k in range(3)),
            sum(left[2][k] * right[k][1] for k in range(3)),
            sum(left[2][k] * right[k][2] for k in range(3)),
        ),
    )


class ExperimentClockModule(ModuleShell):
    def __init__(self) -> None:
        self._timeline: TimelineSpec | None = None
        self._time: SimulationTime | None = None

    def initialize(self, timeline: TimelineSpec) -> None:
        if timeline.start_time_ns > timeline.end_time_ns:
            raise ValueError("timeline start must not be after end")
        if timeline.logic_step_ns <= 0:
            raise ValueError("logic_step_ns must be positive")
        if timeline.clock_domain.time_scale.value != "experiment_elapsed" and (
            timeline.clock_domain.epoch_absolute_ns is None
        ):
            raise ValueError("absolute clock domains require epoch_absolute_ns")
        if timeline.clock_domain.time_scale.value == "experiment_elapsed" and (
            timeline.clock_domain.epoch_absolute_ns is not None
        ):
            raise ValueError("elapsed clock domains cannot define an absolute epoch")
        self._timeline = timeline
        self._time = SimulationTime(
            clock_domain_id=timeline.clock_domain.clock_domain_id,
            time_ns=timeline.start_time_ns,
            step_no=0,
        )

    def read(self) -> SimulationTime:
        if self._time is None:
            raise RuntimeError("experiment clock is not initialized")
        return self._time

    def advance_to(self, time_ns: int) -> SimulationTime:
        if self._timeline is None or self._time is None:
            raise RuntimeError("experiment clock is not initialized")
        if time_ns < self._time.time_ns:
            raise ValueError("experiment clock cannot move backwards")
        if time_ns > self._timeline.end_time_ns:
            raise ValueError("experiment clock cannot pass timeline end")
        if time_ns == self._time.time_ns:
            return self._time
        self._time = SimulationTime(
            clock_domain_id=self._time.clock_domain_id,
            time_ns=time_ns,
            step_no=self._time.step_no + 1,
        )
        return self._time


class TrajectoryModule(ModuleShell):
    def sample(self, trajectory: TrajectorySpec, time: SimulationTime) -> KinematicState:
        if not trajectory.knots:
            raise ValueError("trajectory must contain at least one knot")
        knots = trajectory.knots
        if any(knots[index].time_ns >= knots[index + 1].time_ns for index in range(len(knots) - 1)):
            raise ValueError("trajectory knot times must be strictly increasing")
        if time.time_ns <= knots[0].time_ns:
            return knots[0].state
        if time.time_ns >= knots[-1].time_ns:
            return knots[-1].state
        right_index = next(
            index for index, knot in enumerate(knots) if knot.time_ns >= time.time_ns
        )
        left = knots[right_index - 1]
        right = knots[right_index]
        if trajectory.interpolation.value == "hold":
            return right.state if time.time_ns == right.time_ns else left.state
        fraction = (time.time_ns - left.time_ns) / (right.time_ns - left.time_ns)
        if trajectory.interpolation.value == "linear":
            return KinematicState(
                position_enu_m=_lerp(
                    left.state.position_enu_m, right.state.position_enu_m, fraction
                ),
                velocity_enu_mps=_lerp(
                    left.state.velocity_enu_mps, right.state.velocity_enu_mps, fraction
                ),
                rotation_enu_from_body=(
                    (
                        left.state.rotation_enu_from_body[0][0]
                        + fraction
                        * (
                            right.state.rotation_enu_from_body[0][0]
                            - left.state.rotation_enu_from_body[0][0]
                        ),
                        left.state.rotation_enu_from_body[0][1]
                        + fraction
                        * (
                            right.state.rotation_enu_from_body[0][1]
                            - left.state.rotation_enu_from_body[0][1]
                        ),
                        left.state.rotation_enu_from_body[0][2]
                        + fraction
                        * (
                            right.state.rotation_enu_from_body[0][2]
                            - left.state.rotation_enu_from_body[0][2]
                        ),
                    ),
                    (
                        left.state.rotation_enu_from_body[1][0]
                        + fraction
                        * (
                            right.state.rotation_enu_from_body[1][0]
                            - left.state.rotation_enu_from_body[1][0]
                        ),
                        left.state.rotation_enu_from_body[1][1]
                        + fraction
                        * (
                            right.state.rotation_enu_from_body[1][1]
                            - left.state.rotation_enu_from_body[1][1]
                        ),
                        left.state.rotation_enu_from_body[1][2]
                        + fraction
                        * (
                            right.state.rotation_enu_from_body[1][2]
                            - left.state.rotation_enu_from_body[1][2]
                        ),
                    ),
                    (
                        left.state.rotation_enu_from_body[2][0]
                        + fraction
                        * (
                            right.state.rotation_enu_from_body[2][0]
                            - left.state.rotation_enu_from_body[2][0]
                        ),
                        left.state.rotation_enu_from_body[2][1]
                        + fraction
                        * (
                            right.state.rotation_enu_from_body[2][1]
                            - left.state.rotation_enu_from_body[2][1]
                        ),
                        left.state.rotation_enu_from_body[2][2]
                        + fraction
                        * (
                            right.state.rotation_enu_from_body[2][2]
                            - left.state.rotation_enu_from_body[2][2]
                        ),
                    ),
                ),
                angular_velocity_body_radps=_lerp(
                    left.state.angular_velocity_body_radps,
                    right.state.angular_velocity_body_radps,
                    fraction,
                ),
            )
        if trajectory.interpolation.value == "cubic_hermite":
            dt = (right.time_ns - left.time_ns) / 1_000_000_000.0
            t2 = fraction * fraction
            t3 = t2 * fraction
            h00 = 2 * t3 - 3 * t2 + 1
            h10 = t3 - 2 * t2 + fraction
            h01 = -2 * t3 + 3 * t2
            h11 = t3 - t2
            position: Vec3M = (
                h00 * left.state.position_enu_m[0]
                + h10 * dt * left.state.velocity_enu_mps[0]
                + h01 * right.state.position_enu_m[0]
                + h11 * dt * right.state.velocity_enu_mps[0],
                h00 * left.state.position_enu_m[1]
                + h10 * dt * left.state.velocity_enu_mps[1]
                + h01 * right.state.position_enu_m[1]
                + h11 * dt * right.state.velocity_enu_mps[1],
                h00 * left.state.position_enu_m[2]
                + h10 * dt * left.state.velocity_enu_mps[2]
                + h01 * right.state.position_enu_m[2]
                + h11 * dt * right.state.velocity_enu_mps[2],
            )
            return replace(
                left.state,
                position_enu_m=position,
                velocity_enu_mps=_lerp(
                    left.state.velocity_enu_mps, right.state.velocity_enu_mps, fraction
                ),
            )
        raise ValueError(f"unsupported trajectory interpolation: {trajectory.interpolation}")


class CameraProjectionModule(ModuleShell):
    def project(self, camera: CameraStateTruth, reference_point_enu_m: Vec3M) -> ProjectionTruth:
        displacement = _sub(reference_point_enu_m, camera.optical_state.position_enu_m)
        camera_from_enu = _transpose(camera.optical_state.rotation_enu_from_body)
        point = _mat_vec(camera_from_enu, displacement)
        in_front = point[2] > _EPS
        if not in_front:
            return ProjectionTruth(
                reference_point_enu_m=reference_point_enu_m,
                reference_point_camera_m=point,
                centroid_px=None,
                in_front_of_camera=False,
                center_in_field_of_view=False,
            )
        intrinsics = camera.actual_intrinsics
        normalized_x = point[0] / point[2]
        normalized_y = point[1] / point[2]
        distortion = camera.actual_distortion
        if distortion.model.value == "brown_conrady":
            k1, k2, k3 = distortion.radial_k1_k2_k3
            p1, p2 = distortion.tangential_p1_p2
            radius2 = normalized_x * normalized_x + normalized_y * normalized_y
            radial = 1.0 + k1 * radius2 + k2 * radius2 * radius2 + k3 * radius2 * radius2 * radius2
            x, y = normalized_x, normalized_y
            normalized_x = x * radial + 2 * p1 * x * y + p2 * (radius2 + 2 * x * x)
            normalized_y = y * radial + p1 * (radius2 + 2 * y * y) + 2 * p2 * x * y
        u = (
            intrinsics.focal_x_px * normalized_x
            + intrinsics.skew_px * normalized_y
            + intrinsics.principal_x_px
        )
        v = intrinsics.focal_y_px * normalized_y + intrinsics.principal_y_px
        in_view = 0.0 <= u < intrinsics.image_width_px and 0.0 <= v < intrinsics.image_height_px
        return ProjectionTruth(
            reference_point_enu_m=reference_point_enu_m,
            reference_point_camera_m=point,
            centroid_px=(u, v),
            in_front_of_camera=True,
            center_in_field_of_view=in_view,
        )


class VisibilityModule(ModuleShell):
    def resolve(self, query: VisibilityQuery) -> TargetVisibilityTruth:
        occlusions = self._occlusions(
            query.projection,
            query.camera.optical_state.position_enu_m,
            query.target.center_reference_point_enu_m,
            query.terrain,
            query.world_objects,
            query.vegetation,
        )
        return TargetVisibilityTruth(
            station_id=query.camera.station_id,
            camera_role=query.camera.camera_role,
            truth_object_id=query.target.truth_object_id,
            sample_time=query.sample_time,
            projection=query.projection,
            sight_segment=SightSegment(
                optical_center_enu_m=query.camera.optical_state.position_enu_m,
                reference_point_enu_m=query.target.center_reference_point_enu_m,
            ),
            occlusions=occlusions,
            capture_eligible=query.projection.center_in_field_of_view and not occlusions,
        )

    def resolve_reference(self, query: ReferenceVisibilityQuery) -> LandmarkVisibilityTruth:
        occlusions = self._occlusions(
            query.projection,
            query.camera.optical_state.position_enu_m,
            query.landmark.position_enu_m,
            query.terrain,
            query.world_objects,
            query.vegetation,
        )
        return LandmarkVisibilityTruth(
            station_id=query.camera.station_id,
            camera_role=query.camera.camera_role,
            landmark_id=query.landmark.landmark_id,
            sample_time=query.sample_time,
            projection=query.projection,
            sight_segment=SightSegment(
                optical_center_enu_m=query.camera.optical_state.position_enu_m,
                reference_point_enu_m=query.landmark.position_enu_m,
            ),
            occlusions=occlusions,
            capture_eligible=query.projection.center_in_field_of_view and not occlusions,
        )

    @staticmethod
    def _occlusions(
        projection: ProjectionTruth,
        origin: Vec3M,
        target: Vec3M,
        terrain: object,
        world_objects: tuple[object, ...],
        vegetation: tuple[object, ...],
    ) -> tuple[SegmentOcclusionTruth, ...]:
        if not projection.in_front_of_camera:
            return ()
        blockers: list[tuple[float, SegmentOcclusionTruth]] = []
        entities = [terrain, *world_objects, *vegetation]
        for entity in entities:
            if not getattr(entity, "occludes_sight_lines", False):
                continue
            hit = VisibilityModule._segment_hit(entity, origin, target)
            if hit is not None and hit[0] > _EPS and hit[0] < 1.0 - _EPS:
                blockers.append((hit[0], hit[1]))
        blockers.sort(key=lambda item: item[0])
        return tuple(item[1] for item in blockers)

    @staticmethod
    def _segment_hit(
        entity: object, origin: Vec3M, target: Vec3M
    ) -> tuple[float, SegmentOcclusionTruth] | None:
        segment = _sub(target, origin)
        length2 = _dot(segment, segment)
        if length2 <= _EPS:
            return None
        center = getattr(getattr(entity, "pose", entity), "position_enu_m", None)
        geometry = getattr(entity, "geometry", None)
        object_id = getattr(entity, "object_id", getattr(entity, "vegetation_id", None))
        if object_id is None:
            return None
        if center is None and hasattr(entity, "base_elevation_m"):
            if abs(segment[2]) <= _EPS:
                return None
            fraction = (entity.base_elevation_m - origin[2]) / segment[2]
            if 0.0 < fraction < 1.0:
                return fraction, SegmentOcclusionTruth(
                    object_id, _add(origin, _scale(segment, fraction)), fraction
                )
            return None
        if center is None or geometry is None:
            return None
        radius: float | None = getattr(geometry, "radius_m", None)
        if radius is not None:
            to_center = _sub(center, origin)
            projection = _dot(to_center, segment) / length2
            closest = _add(origin, _scale(segment, projection))
            distance2 = _dot(_sub(center, closest), _sub(center, closest))
            if 0.0 < projection < 1.0 and distance2 <= radius * radius:
                return projection, SegmentOcclusionTruth(object_id, closest, projection)
            return None
        half_extents = getattr(geometry, "half_extents_body_m", None)
        if half_extents is None:
            return None
        rotation = getattr(getattr(entity, "pose", entity), "rotation_enu_from_body", None)
        local_origin = (
            _mat_vec(_transpose(rotation), _sub(origin, center))
            if rotation
            else _sub(origin, center)
        )
        local_direction = _mat_vec(_transpose(rotation), segment) if rotation else segment
        t_min, t_max = 0.0, 1.0
        for coordinate in range(3):
            direction = local_direction[coordinate]
            minimum = -half_extents[coordinate]
            maximum = half_extents[coordinate]
            if abs(direction) <= _EPS:
                if local_origin[coordinate] < minimum or local_origin[coordinate] > maximum:
                    return None
                continue
            near = (minimum - local_origin[coordinate]) / direction
            far = (maximum - local_origin[coordinate]) / direction
            if near > far:
                near, far = far, near
            t_min, t_max = max(t_min, near), min(t_max, far)
            if t_min > t_max:
                return None
        if t_min > _EPS and t_min < 1.0 - _EPS:
            point = _add(origin, _scale(segment, t_min))
            return t_min, SegmentOcclusionTruth(object_id, point, t_min)
        return None


class SceneLogicModule(ModuleShell):
    def __init__(
        self,
        trajectory_sampler: TrajectorySampler,
        projector: CameraProjector,
        visibility: VisibilityResolver,
    ) -> None:
        self.trajectory_sampler = trajectory_sampler
        self.projector = projector
        self.visibility = visibility

    def initialize(
        self,
        scene: SceneDefinition,
        perturbations: ExperimentPerturbations,
        random_seed: int,
    ) -> None:
        if not scene.cameras:
            raise ValueError("scene must define at least one camera")
        camera_stations = {camera.station_id for camera in scene.cameras}
        device_stations = {device.station_id for device in scene.edge_devices}
        if camera_stations != device_stations:
            raise ValueError("camera and edge-device station sets must match")
        for station_id in camera_stations:
            roles = {
                camera.camera_role for camera in scene.cameras if camera.station_id == station_id
            }
            if roles != {CameraRole.MAIN, CameraRole.AUXILIARY}:
                raise ValueError(f"station {station_id} must define main and auxiliary cameras")
        self._scene = scene
        self._perturbations = perturbations
        self._random_seed = random_seed

    def sample(self, time: SimulationTime) -> LogicalSceneSnapshot:
        scene = getattr(self, "_scene", None)
        if scene is None:
            raise RuntimeError("scene logic is not initialized")
        perturbations: ExperimentPerturbations = self._perturbations
        camera_perturbations = {
            (item.station_id, item.camera_role): item for item in perturbations.camera_pose
        }
        cameras: list[CameraStateTruth] = []
        for camera_spec in scene.cameras:
            state = self.trajectory_sampler.sample(camera_spec.optical_pose_trajectory, time)
            perturbation = camera_perturbations.get(
                (camera_spec.station_id, camera_spec.camera_role)
            )
            if perturbation is not None:
                state = replace(
                    state,
                    position_enu_m=_add(
                        state.position_enu_m, perturbation.translation_offset_enu_m
                    ),
                    rotation_enu_from_body=_mat_mul(
                        state.rotation_enu_from_body,
                        _rotation_from_small_angle(perturbation.rotation_offset_camera_rad),
                    ),
                    angular_velocity_body_radps=_add(
                        state.angular_velocity_body_radps,
                        perturbation.drift_rotation_camera_radps,
                    ),
                )
            cameras.append(
                CameraStateTruth(
                    station_id=camera_spec.station_id,
                    camera_role=camera_spec.camera_role,
                    metadata=camera_spec.metadata,
                    sample_time=time,
                    optical_state=state,
                    actual_intrinsics=camera_spec.actual_intrinsics,
                    actual_distortion=camera_spec.actual_distortion,
                )
            )
        flying_objects: list[FlyingObjectTruth] = []
        for object_spec in scene.flying_objects:
            state = self.trajectory_sampler.sample(object_spec.trajectory, time)
            center = _add(
                state.position_enu_m,
                _mat_vec(state.rotation_enu_from_body, object_spec.center_reference_point_body_m),
            )
            flying_objects.append(
                FlyingObjectTruth(
                    truth_object_id=object_spec.truth_object_id,
                    display_name=object_spec.display_name,
                    model_name=object_spec.model_name,
                    sample_time=time,
                    state=state,
                    center_reference_point_enu_m=center,
                    geometry=object_spec.geometry,
                    appearance=object_spec.appearance,
                )
            )
        target_visibility: list[TargetVisibilityTruth] = []
        landmark_visibility: list[LandmarkVisibilityTruth] = []
        for camera in cameras:
            for target in flying_objects:
                projection = self.projector.project(camera, target.center_reference_point_enu_m)
                target_visibility.append(
                    self.visibility.resolve(
                        VisibilityQuery(
                            sample_time=time,
                            camera=camera,
                            target=target,
                            projection=projection,
                            terrain=scene.terrain,
                            world_objects=scene.world_objects,
                            vegetation=scene.vegetation,
                        )
                    )
                )
            for landmark in scene.reference_landmarks:
                projection = self.projector.project(camera, landmark.position_enu_m)
                landmark_visibility.append(
                    self.visibility.resolve_reference(
                        ReferenceVisibilityQuery(
                            sample_time=time,
                            camera=camera,
                            landmark=landmark,
                            projection=projection,
                            terrain=scene.terrain,
                            world_objects=scene.world_objects,
                            vegetation=scene.vegetation,
                        )
                    )
                )
        return LogicalSceneSnapshot(
            sample_time=time,
            world_frame_id=scene.world.world_frame_id,
            cameras=tuple(cameras),
            flying_objects=tuple(flying_objects),
            terrain=scene.terrain,
            world_objects=scene.world_objects,
            vegetation=scene.vegetation,
            reference_landmarks=scene.reference_landmarks,
            target_visibility=tuple(target_visibility),
            landmark_visibility=tuple(landmark_visibility),
            illumination=scene.illumination,
        )


class CameraImagingModule(ModuleShell):
    def render(self, request: ImageGenerationRequest) -> tuple[ImagingBatch, ImagingTruthBatch]:
        artifacts = request.imaging_spec.sensor_artifacts
        if artifacts.output_pixel_format is not PixelFormat.GRAY8:
            raise ValueError("the first scene imager supports only gray8")
        if request.imaging_spec.exposure_samples <= 0:
            raise ValueError("exposure_samples must be positive")
        frames: list[CameraFrame] = []
        truths: list[CameraFrameTruth] = []
        for frame in request.frames:
            if not frame.exposure_samples:
                raise ValueError("each frame requires at least one logical scene sample")
            midpoint = (
                frame.capture_time.exposure_start_ns + frame.capture_time.exposure_end_ns
            ) // 2
            scene = min(
                frame.exposure_samples,
                key=lambda sample: abs(sample.sample_time.time_ns - midpoint),
            )
            camera = next(
                (
                    item
                    for item in scene.cameras
                    if item.station_id == frame.station_id and item.camera_role is frame.camera_role
                ),
                None,
            )
            if camera is None:
                raise ValueError("frame has no matching actual camera state")
            width = camera.actual_intrinsics.image_width_px
            height = camera.actual_intrinsics.image_height_px
            if width <= 0 or height <= 0:
                raise ValueError("camera dimensions must be positive")
            base_intensity = max(
                artifacts.intensity_min,
                min(artifacts.intensity_max, sum(scene.illumination.sky_linear_rgb) / 3.0),
            )
            pixels = bytearray(
                self._pixel_value(
                    base_intensity,
                    artifacts.intensity_min,
                    artifacts.intensity_max,
                    artifacts.sensor_noise_std_intensity,
                    request.random_seed,
                    frame.frame_id,
                    x,
                    y,
                )
                for y in range(height)
                for x in range(width)
            )
            target_truth: list[ProjectedTargetTruth] = []
            for target_visibility in scene.target_visibility:
                if (
                    target_visibility.station_id != frame.station_id
                    or target_visibility.camera_role is not frame.camera_role
                ):
                    continue
                target = next(
                    item
                    for item in scene.flying_objects
                    if item.truth_object_id == target_visibility.truth_object_id
                )
                target_truth.append(
                    ProjectedTargetTruth(
                        truth_object_id=target.truth_object_id,
                        projection=target_visibility.projection,
                        visibility=target_visibility,
                        projected_width_px=None,
                        projected_height_px=None,
                    )
                )
                if (
                    target_visibility.capture_eligible
                    and target_visibility.projection.centroid_px is not None
                ):
                    self._draw_marker(
                        pixels, width, height, target_visibility.projection.centroid_px
                    )
            landmark_truth: list[ProjectedLandmarkTruth] = []
            for landmark_visibility in scene.landmark_visibility:
                if (
                    landmark_visibility.station_id != frame.station_id
                    or landmark_visibility.camera_role is not frame.camera_role
                ):
                    continue
                landmark = next(
                    item
                    for item in scene.reference_landmarks
                    if item.landmark_id == landmark_visibility.landmark_id
                )
                landmark_truth.append(
                    ProjectedLandmarkTruth(
                        landmark_id=landmark.landmark_id,
                        position_enu_m=landmark.position_enu_m,
                        projection=landmark_visibility.projection,
                        visibility=landmark_visibility,
                    )
                )
            frames.append(
                CameraFrame(
                    station_id=frame.station_id,
                    station_session_id=frame.station_session_id,
                    camera_role=frame.camera_role,
                    frame_id=frame.frame_id,
                    capture_time=frame.capture_time,
                    image=ImagePlane(
                        width_px=width,
                        height_px=height,
                        pixel_format=PixelFormat.GRAY8,
                        row_stride_bytes=width,
                        pixels=bytes(pixels),
                    ),
                    camera_config_ref=frame.online_camera_config_digest,
                )
            )
            truths.append(
                CameraFrameTruth(
                    station_id=frame.station_id,
                    station_session_id=frame.station_session_id,
                    camera_role=frame.camera_role,
                    frame_id=frame.frame_id,
                    capture_time=frame.capture_time,
                    actual_camera=camera,
                    targets=tuple(target_truth),
                    landmarks=tuple(landmark_truth),
                )
            )
        return ImagingBatch(frames=tuple(frames)), ImagingTruthBatch(frame_truth=tuple(truths))

    @staticmethod
    def _pixel_value(
        intensity: float,
        minimum: float,
        maximum: float,
        noise_std: float,
        seed: int,
        frame_id: FrameId,
        x: int,
        y: int,
    ) -> int:
        value = intensity
        if noise_std:
            digest = blake2b(f"{seed}:{frame_id}:{x}:{y}".encode(), digest_size=8).digest()
            unit = int.from_bytes(digest, "big") / ((1 << 64) - 1)
            value += noise_std * (2.0 * unit - 1.0)
        if maximum <= minimum:
            raise ValueError("intensity_max must be greater than intensity_min")
        normalized = (max(minimum, min(maximum, value)) - minimum) / (maximum - minimum)
        return max(0, min(255, round(normalized * 255.0)))

    @staticmethod
    def _draw_marker(
        pixels: bytearray, width: int, height: int, centroid: tuple[float, float]
    ) -> None:
        x, y = round(centroid[0]), round(centroid[1])
        if 0 <= x < width and 0 <= y < height:
            pixels[y * width + x] = 255


class VirtualObservationModule(ModuleShell):
    """Each sequencer owns one station's fresh virtual event session."""

    def __init__(self, sequencers: tuple[EventSequencer, ...]) -> None:
        self.sequencers = sequencers

    def generate(
        self, request: VirtualObservationRequest
    ) -> tuple[VirtualObservationBatch, VirtualObservationTruthBatch]:
        # The packet generator is intentionally conservative until calibration
        # snapshots and online detections are available.  An empty batch is a
        # valid result; it never invents a ray or placeholder calibration.
        if len(request.station_sessions) != len(self.sequencers):
            raise ValueError("station session bindings must match virtual sequencers")
        return (
            VirtualObservationBatch(
                calibration_states=(),
                calibration_solutions=(),
                calibration_events=(),
                rays=(),
                diagnostics=(),
            ),
            VirtualObservationTruthBatch(truth_correspondences=()),
        )


class ScenarioModule(ModuleShell):
    def __init__(
        self,
        clock: ExperimentClock,
        logic: SceneLogic,
        imager: CameraImager,
        virtual_generator: VirtualObservationGenerator,
        station_sessions: tuple[StationSessionBinding, ...],
    ) -> None:
        self.clock = clock
        self.logic = logic
        self.imager = imager
        self.virtual_generator = virtual_generator
        self.station_sessions = station_sessions

    def generate(self, spec: ScenarioSpec) -> Iterable[ScenarioStep]:
        self.clock.initialize(spec.timeline)
        self.logic.initialize(
            spec.scene_definition,
            spec.perturbations,
            spec.random_seed,
        )
        station_configs = {config.station_id: config for config in spec.station_configs}
        camera_specs = spec.scene_definition.cameras
        exposures: list[tuple[int, SceneCameraSpec]] = []
        for camera in camera_specs:
            if camera.exposure.frame_period_ns <= 0:
                raise ValueError("camera frame period must be positive")
            if camera.exposure.exposure_duration_ns < 0:
                raise ValueError("exposure duration cannot be negative")
            start = camera.exposure.first_exposure_start_ns
            while start <= spec.timeline.end_time_ns:
                midpoint = start + camera.exposure.exposure_duration_ns // 2
                if (
                    midpoint >= spec.timeline.start_time_ns
                    and midpoint <= spec.timeline.end_time_ns
                ):
                    exposures.append((midpoint, camera))
                start += camera.exposure.frame_period_ns
        exposures.sort(
            key=lambda item: (item[0], str(item[1].station_id), item[1].camera_role.value)
        )
        for _, group in _group_exposures(exposures):
            midpoint = group[0][0]
            time = self.clock.advance_to(midpoint)
            logical_scene = self.logic.sample(time)
            frame_inputs: list[FrameImagingInput] = []
            for index, (_, camera) in enumerate(group):
                session = next(
                    item for item in self.station_sessions if item.station_id == camera.station_id
                )
                config = station_configs.get(camera.station_id)
                if config is None:
                    raise ValueError(f"missing online station config: {camera.station_id}")
                capture = CaptureTime(
                    clock_domain_id=time.clock_domain_id,
                    exposure_start_ns=midpoint - camera.exposure.exposure_duration_ns // 2,
                    exposure_end_ns=midpoint + camera.exposure.exposure_duration_ns // 2,
                    clock_uncertainty_ns=0,
                    rolling_shutter_model=(
                        "none"
                        if camera.exposure.shutter_model.value == "global"
                        else camera.exposure.shutter_model.value
                    ),
                )
                frame_inputs.append(
                    FrameImagingInput(
                        station_id=camera.station_id,
                        station_session_id=session.image_session_id,
                        camera_role=camera.camera_role,
                        frame_id=FrameId(
                            f"{spec.scenario_id}:{camera.station_id}:{camera.camera_role.value}:{midpoint}:{index}"
                        ),
                        capture_time=capture,
                        online_camera_config_digest=config.config_digest,
                        exposure_samples=(logical_scene,),
                    )
                )
            imaging, imaging_truth = self.imager.render(
                ImageGenerationRequest(
                    scenario_id=spec.scenario_id,
                    random_seed=spec.random_seed,
                    imaging_spec=spec.imaging,
                    frames=tuple(frame_inputs),
                )
            )
            virtual, virtual_truth = self.virtual_generator.generate(
                VirtualObservationRequest(
                    scenario_id=spec.scenario_id,
                    random_seed=spec.random_seed,
                    generation_spec=spec.virtual_observations,
                    online_station_configs=spec.station_configs,
                    frames=imaging_truth.frame_truth,
                    station_sessions=self.station_sessions,
                )
            )
            yield ScenarioStep(
                video=ScenarioVideoOutput(
                    scenario_id=spec.scenario_id,
                    sample_time=time,
                    frames=imaging.frames,
                ),
                packets=ScenarioPacketOutput(
                    scenario_id=spec.scenario_id,
                    sample_time=time,
                    calibration_states=virtual.calibration_states,
                    calibration_solutions=virtual.calibration_solutions,
                    calibration_events=virtual.calibration_events,
                    rays=virtual.rays,
                    diagnostics=virtual.diagnostics,
                ),
                truth=ScenarioTruthOutput(
                    scenario_id=spec.scenario_id,
                    sample_time=time,
                    logical_scene=logical_scene,
                    camera_frames=imaging_truth.frame_truth,
                    online_correspondences=virtual_truth.truth_correspondences,
                    experiment_perturbations=spec.perturbations,
                ),
            )


class EvaluationModule(ModuleShell):
    def __init__(self, spec: EvaluationSpec) -> None:
        self.spec = spec

    def observe(
        self, truth: ScenarioTruth, online: OnlineEvaluationData
    ) -> tuple[EvaluationRecord, ...]:
        raise NotImplementedError(_TODO)

    def finalize(self, scenario_id: str, random_seed: int) -> EvaluationReport:
        raise NotImplementedError(_TODO)


class MonitoringModule(ModuleShell):
    def __init__(self, spec: MonitoringSpec) -> None:
        self.spec = spec

    def render(self, snapshot: MonitoringSnapshot) -> MonitoringFrame:
        raise NotImplementedError(_TODO)
