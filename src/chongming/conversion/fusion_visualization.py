"""Convert FusionBatchResult to visualization data contracts."""

import math

from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.contracts import visualization as vis


def build_center_fusion_event(batch: f.FusionBatchResult) -> vis.CenterFusionEvent:
    """Convert a FusionBatchResult into a CenterFusionEvent for visualization.

    Extracts measurements and tracks, converting covariances to standard deviations
    for simpler display. Aggregates counts from batch components.
    """
    vis_measurements = tuple(_convert_measurement(meas) for meas in batch.measurements)
    vis_tracks = tuple(_convert_track(track) for track in batch.tracks)

    window_count = len(batch.window_update.sealed_windows)
    candidate_count = sum(len(cb.candidates) for cb in batch.candidates)
    association_count = sum(len(ab.selected_groups) for ab in batch.associations)

    return vis.CenterFusionEvent(
        measurements=vis_measurements,
        tracks=vis_tracks,
        window_count=window_count,
        candidate_count=candidate_count,
        association_count=association_count,
        voxel_space=None,
    )


def _convert_measurement(meas: m.FusionMeasurement) -> vis.MeasurementVisualization:
    """Convert a fusion measurement to visualization format."""
    position_std_enu_m: tuple[float, float, float] | None = None
    minimum_intersection_angle_deg: float | None = None

    if meas.position_enu_m is not None and meas.conditional_position_covariance_enu_m2 is not None:
        position_std_enu_m = _extract_position_std(meas.conditional_position_covariance_enu_m2)

    if meas.minimum_intersection_angle_rad is not None:
        minimum_intersection_angle_deg = math.degrees(meas.minimum_intersection_angle_rad)

    # Build supporting ray event IDs tuple
    supporting_ray_ids = tuple(event_id for event_id in meas.supporting_ray_event_ids)

    return vis.MeasurementVisualization(
        measurement_id=meas.measurement_id,
        reference_time_ns=meas.reference_time_ns,
        status=meas.status,
        position_enu_m=meas.position_enu_m,
        position_std_enu_m=position_std_enu_m,
        supporting_ray_ids=supporting_ray_ids,
        supporting_station_ids=meas.supporting_station_ids,
        minimum_intersection_angle_deg=minimum_intersection_angle_deg,
        geometry_condition=meas.geometry_condition,
    )


def _convert_track(track: m.TrackEvent) -> vis.TrackVisualization:
    """Convert a track event to visualization format."""
    position_std_enu_m: tuple[float, float, float] | None = None
    velocity_std_enu_mps: tuple[float, float, float] | None = None

    if track.conditional_state_covariance is not None:
        position_std_enu_m = _extract_position_std(track.conditional_state_covariance)
        if track.velocity_enu_mps is not None:
            velocity_std_enu_mps = _extract_velocity_std(track.conditional_state_covariance)

    return vis.TrackVisualization(
        track_id=track.track_id,
        event_time_ns=track.event_time_ns,
        lifecycle=track.lifecycle,
        position_enu_m=track.position_enu_m,
        velocity_enu_mps=track.velocity_enu_mps,
        position_std_enu_m=position_std_enu_m,
        velocity_std_enu_mps=velocity_std_enu_mps,
        recent_position_enu_m=track.position_enu_m,
        recent_time_ns=track.event_time_ns,
    )


def _extract_position_std(covariance: m.Matrix6x6 | m.Matrix3x3) -> tuple[float, float, float]:
    """Extract position standard deviations from covariance matrix."""
    return (
        math.sqrt(covariance[0][0]),
        math.sqrt(covariance[1][1]),
        math.sqrt(covariance[2][2]),
    )


def _extract_velocity_std(covariance: m.Matrix6x6) -> tuple[float, float, float]:
    """Extract velocity standard deviations from 6x6 state covariance matrix."""
    return (
        math.sqrt(covariance[3][3]),
        math.sqrt(covariance[4][4]),
        math.sqrt(covariance[5][5]),
    )
