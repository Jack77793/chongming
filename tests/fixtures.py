"""Typed construction fixtures, never simulated inputs or algorithm expectations.

Every numeric policy value below is arbitrary and used only to construct the
dependency graph. These values are not tuned settings or performance promises.
No fixture invokes a business method or creates substitute frames/results.
"""

from __future__ import annotations

from pathlib import Path

from chongming.contracts import device, fusion, model, runtime, scene

_IDENTITY: model.Matrix3x3 = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)
_CLOCK = "structure-clock"
_WORLD = model.WorldFrameId("structure-enu")
_SCORE = model.ScoreDefinition(
    definition_id="structural-ranking",
    kind=model.ScoreKind.RANKING,
    minimum=0.0,
    maximum=1.0,
    cross_station_comparison_group=None,
)


def _station_config(station_id: model.StationId, index: int) -> model.StationConfig:
    camera = model.CameraModel(
        intrinsics=model.CameraIntrinsics(
            image_width_px=64,
            image_height_px=48,
            matrix_px=((100.0, 0.0, 32.0), (0.0, 100.0, 24.0), (0.0, 0.0, 1.0)),
        ),
        distortion=model.CameraDistortion(model="none", coefficients=()),
    )
    digest = model.ConfigDigest(f"structure-config-{index}")
    return model.StationConfig(
        station_id=station_id,
        config_version="structural-only",
        config_digest=digest,
        world_frame_id=_WORLD,
        geodetic_anchor=model.GeodeticAnchor(
            anchor_id="structure-anchor",
            datum="WGS84",
            datum_epoch="structural-only",
            latitude_deg=0.0,
            longitude_deg=0.0,
            height_m=0.0,
            height_reference="ellipsoidal",
        ),
        main_camera_position_enu_m=(float(index * 10), 0.0, 0.0),
        baseline_orientation_enu_from_main=_IDENTITY,
        main_camera=camera,
        auxiliary_camera=camera,
        fixed_rotation_aux_from_main=_IDENTITY,
        full_rigid_transform_aux_from_main=None,
        reference_anchor=model.InitialImageReference(
            reference_id=f"structure-reference-{index}",
            capture_time=model.CaptureTime(
                clock_domain_id=_CLOCK,
                exposure_start_ns=0,
                exposure_end_ns=1,
                clock_uncertainty_ns=0,
                rolling_shutter_model="none",
            ),
            image=None,
            features=(),
        ),
        reference_scene_version="structural-only",
        installation_uncertainty=model.InstallationUncertainty(
            state_ref=model.InstallationStateRef(
                station_id=station_id,
                config_digest=digest,
                state_version="structural-only",
            ),
            position_covariance_enu_m2=_IDENTITY,
            orientation_covariance_enu_rad2=_IDENTITY,
            fixed_extrinsic_covariance_aux_rad2=_IDENTITY,
            measurement_source="structural-only",
        ),
    )


def _device_policy() -> device.DevicePolicy:
    return device.DevicePolicy(
        detection=device.DetectionPolicy(
            algorithm_id="unimplemented-structural-detector",
            score_definition=_SCORE,
            minimum_component_area_px2=1.0,
            maximum_component_area_px2=None,
        ),
        tracking=device.Tracking2DPolicy(
            algorithm_id="unimplemented-structural-tracker",
            association_gate_px=1.0,
            confirmation_frame_count=2,
            maximum_missing_duration_ns=1_000_000_000,
            quality_definition=_SCORE,
        ),
        calibration=device.CalibrationPolicy(
            update_period_ns=1_000_000_000,
            stale_after_ns=2_000_000_000,
            recovery_success_count=2,
            minimum_reference_count=3,
            maximum_reprojection_rmse_px=1.0,
            maximum_rotation_only_parallax_px=None,
            estimator_id="unimplemented-structural-calibrator",
        ),
        rays=device.RayGenerationPolicy(
            uncertainty_model_id="tangent_gaussian_v1",
            fallback_centroid_covariance_px2=None,
            unmodelled_high_frequency_jitter_covariance_rad2=None,
            unmodelled_relative_flex_covariance_rad2=None,
        ),
    )


def architecture_specs(
    *,
    center_input: runtime.EventPath = runtime.EventPath.IMAGE_PIPELINE,
    candidate_method: fusion.CandidateMethod = fusion.CandidateMethod.COARSE_VOXEL,
) -> tuple[scene.ScenarioSpec, runtime.RunSpec]:
    """Two stations give structural coverage of independent session/sequencer owners."""

    configs = tuple(
        _station_config(model.StationId(f"station-{index}"), index) for index in range(2)
    )
    search = model.VoxelSearchSpec(
        world_frame_id=_WORLD,
        min_enu_m=(-100.0, -100.0, 0.0),
        max_enu_m=(100.0, 100.0, 100.0),
        step_enu_m=(10.0, 10.0, 10.0),
        forward_range_m=None,
    )
    cameras = tuple(
        scene.SceneCameraSpec(
            station_id=config.station_id,
            camera_role=role,
            metadata=scene.CameraMetadata(
                camera_id=scene.CameraId(f"{config.station_id}-{role.value}-camera"),
                display_name=f"{config.station_id}-{role.value}",
                profile_id="experimental-structural",
                sensor_format="structural",
                lens_focal_length_mm=3.0,
                nominal_diagonal_fov_deg=60.0 if role is model.CameraRole.MAIN else 20.0,
                nominal_frame_rate_hz=30.0,
            ),
            actual_intrinsics=scene.SceneCameraIntrinsics(
                image_width_px=64,
                image_height_px=48,
                focal_x_px=100.0,
                focal_y_px=100.0,
                principal_x_px=32.0,
                principal_y_px=24.0,
                skew_px=0.0,
            ),
            actual_distortion=scene.SceneCameraDistortion(
                model=scene.SceneDistortionModel.NONE,
                radial_k1_k2_k3=(0.0, 0.0, 0.0),
                tangential_p1_p2=(0.0, 0.0),
            ),
            optical_pose_trajectory=scene.TrajectorySpec(
                interpolation=scene.TrajectoryInterpolation.HOLD,
                knots=(
                    scene.TrajectoryKnot(
                        time_ns=0,
                        state=scene.KinematicState(
                            position_enu_m=config.main_camera_position_enu_m,
                            velocity_enu_mps=(0.0, 0.0, 0.0),
                            rotation_enu_from_body=_IDENTITY,
                            angular_velocity_body_radps=(0.0, 0.0, 0.0),
                        ),
                    ),
                ),
            ),
            exposure=scene.CameraExposureSpec(
                first_exposure_start_ns=0,
                frame_period_ns=1_000_000_000,
                exposure_duration_ns=1_000_000,
                shutter_model=scene.ShutterModel.GLOBAL,
                rolling_readout_ns=None,
            ),
        )
        for config in configs
        for role in model.CameraRole
    )
    scenario = scene.ScenarioSpec(
        scenario_id="structure-only",
        schema_version="structure-scenario-v1",
        config_digest=model.ConfigDigest("structure-scenario-digest"),
        random_seed=0,
        world_frame_id=_WORLD,
        voxel_search=search,
        station_configs=configs,
        scene_definition=scene.SceneDefinition(
            world=scene.ENUWorldSpec(
                world_frame_id=_WORLD, geodetic_anchor=configs[0].geodetic_anchor
            ),
            terrain=scene.TerrainSpec(
                terrain_id=scene.WorldObjectId("structure-terrain"),
                model=scene.TerrainModel.FLAT,
                base_elevation_m=0.0,
                maximum_relief_m=0.0,
                heightfield_asset_uri=None,
                heightfield_asset_digest=None,
                appearance=scene.SurfaceAppearance(
                    linear_rgb=(0.2, 0.2, 0.2), diffuse_reflectance=0.5
                ),
                occludes_sight_lines=True,
            ),
            edge_devices=tuple(
                scene.EdgeDeviceMetadata(
                    station_id=config.station_id,
                    display_name=f"{config.station_id}-device",
                    hardware_model="structural-device",
                    main_camera_id=scene.CameraId(f"{config.station_id}-main-camera"),
                    auxiliary_camera_id=scene.CameraId(f"{config.station_id}-auxiliary-camera"),
                )
                for config in configs
            ),
            cameras=cameras,
            world_objects=(),
            flying_objects=(),
            reference_landmarks=(),
            illumination=scene.IlluminationSpec(
                sun_direction_enu_unit=(0.0, 0.0, 1.0),
                illuminance_lux=1.0,
                sky_linear_rgb=(0.5, 0.5, 0.5),
            ),
        ),
        timeline=scene.TimelineSpec(
            clock_domain=scene.ClockDomainSpec(
                clock_domain_id=_CLOCK,
                time_scale=scene.TimeScale.EXPERIMENT_ELAPSED,
                epoch_definition="structural-only",
                epoch_absolute_ns=None,
            ),
            start_time_ns=0,
            end_time_ns=2_000_000_000,
            logic_step_ns=1_000_000,
        ),
        imaging=scene.ImagingSpec(
            color_space="linear-rgb",
            exposure_samples=1,
            sensor_artifacts=scene.SensorArtifactSpec(
                output_pixel_format=model.PixelFormat.RGB8,
                sensor_noise_std_intensity=0.0,
                intensity_min=0.0,
                intensity_max=1.0,
                auto_exposure_enabled=False,
                dynamic_range_stops=None,
                saturation_level=None,
                glare_enabled=False,
                bloom_strength=None,
            ),
        ),
        virtual_observations=scene.VirtualObservationSpec(
            detection_probability=1.0,
            false_alarms_per_camera_hour=0.0,
            independent_angular_covariance_rad2=((1.0, 0.0), (0.0, 1.0)),
            independent_origin_covariance_m2=_IDENTITY,
            detection_score_definition=_SCORE,
            calibration_intervals=(),
        ),
        perturbations=scene.ExperimentPerturbations(
            camera_pose=(),
            camera_jitter=(),
            wind=None,
            clouds=(),
            fog=None,
            rain=None,
            vegetation=(),
            communication=(),
        ),
    )
    policy = fusion.FusionPolicy(
        admission=fusion.AdmissionPolicy(
            accepted_schema_versions=("structure-v1",),
            world_frame_id=_WORLD,
            capture_clock_domain_id=_CLOCK,
            direction_unit_tolerance=0.01,
            tangent_basis_orthogonality_tolerance=0.01,
            calibration_usage_rules=tuple(
                fusion.CalibrationUsageRule(
                    usage=usage, action=fusion.CalibrationAction.ACCEPT, geometry_weight_scale=1.0
                )
                for usage in model.CalibrationUsage
            ),
        ),
        windows=fusion.EventWindowPolicy(
            capture_clock_domain_id=_CLOCK,
            processing_clock_domain_id=_CLOCK,
            origin_ns=0,
            width_ns=1_000_000,
            allowed_lateness_ns=1_000_000,
            awaiting_first_event_timeout_ns=1_000_000_000,
            idle_after_ns=1_000_000_000,
            offline_after_ns=2_000_000_000,
            exclude_idle_stations=True,
            exclude_offline_stations=True,
            watermark_strategy=fusion.WatermarkStrategy.ACTIVE_STATION_MINIMUM,
            no_active_station_policy=fusion.EmptyActiveStationPolicy.ADVANCE_WITH_MAPPED_EVENT_CLOCK,
        ),
        candidates=fusion.CandidatePolicy(
            search=search,
            primary_method=candidate_method,
            minimum_independent_stations=2,
            minimum_candidate_score=0.0,
            maximum_candidates=None,
        ),
        association=fusion.AssociationPolicy(
            minimum_independent_stations=2,
            temporal_gate_ns=1_000_000,
            spatial_gate_m=1.0,
            ambiguity_score_margin=1.0,
        ),
        geometry=fusion.GeometryPolicy(
            minimum_independent_stations=2,
            minimum_intersection_angle_rad=0.01,
            maximum_condition_number=1000.0,
            maximum_residual_m=1.0,
            robust_loss_definition="unimplemented-structural-loss",
            robust_loss_scale_m=1.0,
            motion_alignment_model=fusion.MotionAlignmentModel.NARROW_WINDOW_WITHOUT_PRIOR,
            conservative_speed_bound_mps=None,
        ),
        tracking=fusion.TrackingPolicy(
            confirmation_measurement_count=2,
            confirmation_max_gap_ns=1_000_000_000,
            coast_after_ns=1_000_000_000,
            delete_after_ns=2_000_000_000,
            association_gate_mahalanobis_squared=1.0,
            process_acceleration_covariance_enu_m2ps4=_IDENTITY,
        ),
    )
    run = runtime.RunSpec(
        run_id="structure-only",
        center_session_id=model.SessionId("structure-center-session"),
        stations=tuple(
            runtime.StationRuntimeSpec(
                sessions=model.StationSessionBinding(
                    station_id=config.station_id,
                    image_session_id=model.SessionId(f"{config.station_id}-image-session"),
                    virtual_session_id=model.SessionId(f"{config.station_id}-virtual-session"),
                ),
                device_policy=_device_policy(),
            )
            for config in configs
        ),
        schema_version="structure-v1",
        center_input=center_input,
        fusion_policy=policy,
        processing_to_capture_clock=fusion.EventClockMappingSpec(
            processing_clock_domain_id=_CLOCK,
            capture_clock_domain_id=_CLOCK,
            scale_numerator=1,
            scale_denominator=1,
            offset_ns=0,
            mapping_uncertainty_ns=0,
        ),
        transport_policy=runtime.TransportPolicy(
            maximum_pending_events=10,
            overflow_action=runtime.OverflowAction.REJECT_AND_REPORT,
            drain_timeout_ns=1_000_000_000,
        ),
        evaluation=scene.EvaluationSpec(
            metrics=(),
            strata=(),
            association_distance_gate_m=1.0,
            observation_angle_gate_rad=0.01,
            report_conditional_uncertainty=True,
        ),
        monitoring=scene.MonitoringSpec(
            refresh_hz=1.0,
            layers=tuple(scene.MonitoringLayer),
            window_width_px=320,
            window_height_px=240,
        ),
        artifacts_directory=Path("unused-structure-output"),
    )
    return scenario, run
