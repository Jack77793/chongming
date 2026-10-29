"""Complete development assembly policies; the volunteer supplies fusion logic."""

from pathlib import Path
from uuid import uuid4

from chongming.composition.center import reference_policy
from chongming.contracts import device as d
from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.contracts import runtime as r
from chongming.contracts import scene as s


def development_run(spec: s.ScenarioSpec, output: Path) -> r.RunSpec:
    clock = spec.timeline.clock_domain.clock_domain_id
    score = spec.virtual_observations.detection_score_definition
    period = min(c.exposure.frame_period_ns for c in spec.scene_definition.cameras)
    device = d.DevicePolicy(
        detection=d.DetectionPolicy(
            algorithm_id="unimplemented",
            score_definition=score,
            minimum_component_area_px2=1,
            maximum_component_area_px2=None,
        ),
        tracking=d.Tracking2DPolicy(
            algorithm_id="unimplemented",
            association_gate_px=10,
            confirmation_frame_count=2,
            maximum_missing_duration_ns=10**9,
            quality_definition=score,
        ),
        calibration=d.CalibrationPolicy(
            update_period_ns=10**9,
            stale_after_ns=3 * 10**9,
            recovery_success_count=2,
            minimum_reference_count=3,
            maximum_reprojection_rmse_px=1,
            maximum_rotation_only_parallax_px=None,
            estimator_id="unimplemented",
        ),
        rays=d.RayGenerationPolicy(
            uncertainty_model_id="tangent_gaussian_v1",
            fallback_centroid_covariance_px2=None,
            unmodelled_high_frequency_jitter_covariance_rad2=None,
            unmodelled_relative_flex_covariance_rad2=None,
        ),
    )
    policy = reference_policy(
        search=spec.voxel_search, clock_domain_id=clock, window_width_ns=period
    )
    return r.RunSpec(
        run_id=str(uuid4()),
        center_session_id=m.SessionId(str(uuid4())),
        stations=tuple(
            r.StationRuntimeSpec(
                sessions=m.StationSessionBinding(
                    station_id=config.station_id,
                    image_session_id=m.SessionId(str(uuid4())),
                    virtual_session_id=m.SessionId(str(uuid4())),
                ),
                device_policy=device,
            )
            for config in spec.station_configs
        ),
        schema_version="event-v1",
        center_input=r.EventPath.VIRTUAL_RAYS,
        fusion_policy=policy,
        processing_to_capture_clock=f.EventClockMappingSpec(
            processing_clock_domain_id=clock,
            capture_clock_domain_id=clock,
            scale_numerator=1,
            scale_denominator=1,
            offset_ns=0,
            mapping_uncertainty_ns=0,
        ),
        transport_policy=r.TransportPolicy(
            maximum_pending_events=100_000,
            overflow_action=r.OverflowAction.REJECT_AND_REPORT,
            drain_timeout_ns=10**9,
        ),
        evaluation=s.EvaluationSpec(
            metrics=(),
            strata=(),
            association_distance_gate_m=10,
            observation_angle_gate_rad=0.01,
            report_conditional_uncertainty=True,
        ),
        monitoring=s.MonitoringSpec(
            refresh_hz=10,
            layers=tuple(s.MonitoringLayer),
            window_width_px=1280,
            window_height_px=720,
        ),
        artifacts_directory=output,
    )
