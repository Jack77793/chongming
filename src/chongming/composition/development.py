"""Complete development assembly policies; the volunteer supplies fusion logic."""

from pathlib import Path
from uuid import uuid4

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
    policy = f.FusionPolicy(
        admission=f.AdmissionPolicy(
            accepted_schema_versions=("event-v1",),
            world_frame_id=spec.world_frame_id,
            capture_clock_domain_id=clock,
            direction_unit_tolerance=1e-6,
            tangent_basis_orthogonality_tolerance=1e-6,
            calibration_usage_rules=tuple(
                f.CalibrationUsageRule(
                    usage=usage, action=f.CalibrationAction.ACCEPT, geometry_weight_scale=1
                )
                for usage in m.CalibrationUsage
            ),
        ),
        windows=f.EventWindowPolicy(
            capture_clock_domain_id=clock,
            processing_clock_domain_id=clock,
            origin_ns=0,
            width_ns=period,
            allowed_lateness_ns=0,
            awaiting_first_event_timeout_ns=10**9,
            idle_after_ns=10**9,
            offline_after_ns=3 * 10**9,
            exclude_idle_stations=False,
            exclude_offline_stations=False,
            watermark_strategy=f.WatermarkStrategy.ACTIVE_STATION_MINIMUM,
            no_active_station_policy=f.EmptyActiveStationPolicy.HOLD,
        ),
        candidates=f.CandidatePolicy(
            search=spec.voxel_search,
            primary_method=f.CandidateMethod.COARSE_VOXEL,
            minimum_independent_stations=2,
            minimum_candidate_score=2,
            maximum_candidates=None,
        ),
        association=f.AssociationPolicy(
            minimum_independent_stations=2,
            temporal_gate_ns=period,
            spatial_gate_m=10,
            ambiguity_score_margin=0.1,
        ),
        geometry=f.GeometryPolicy(
            minimum_independent_stations=2,
            minimum_intersection_angle_rad=0.01,
            maximum_condition_number=1e6,
            maximum_residual_m=1,
            robust_loss_definition="huber",
            robust_loss_scale_m=1,
            motion_alignment_model=f.MotionAlignmentModel.NARROW_WINDOW_WITHOUT_PRIOR,
            conservative_speed_bound_mps=None,
        ),
        tracking=f.TrackingPolicy(
            confirmation_measurement_count=2,
            confirmation_max_gap_ns=10**9,
            coast_after_ns=10**9,
            delete_after_ns=3 * 10**9,
            association_gate_mahalanobis_squared=9,
            process_acceleration_covariance_enu_m2ps4=(
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.0, 0.0, 1.0),
            ),
        ),
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
