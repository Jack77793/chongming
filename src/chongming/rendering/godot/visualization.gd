extends RefCounted

## Validated display copies only. A new worker owns a fresh instance per run.
var last_update: Dictionary = {}
var truth_by_time: Dictionary = {}
var rays: Dictionary = {}
var measurements: Dictionary = {}
var tracks: Dictionary = {}
var track_history: Dictionary = {}
var station_states: Dictionary = {}
var edge_diagnostics: Array = []
var update_count := 0
var error := ""
var _last_snapshots: Array = []


func receive(state: Dictionary) -> bool:
	error = ""
	if not state.has("algorithm_output") and not state.has("truth_snapshots"):
		return true
	# Validate a private copy before changing any accepted data, including on duplicates.
	var update = state.get("algorithm_output")
	var snapshots = state.get("truth_snapshots")
	if not update is Dictionary or not snapshots is Array:
		return _reject("algorithm_output and truth_snapshots must be supplied together")
	update = update.duplicate(true)
	snapshots = snapshots.duplicate(true)
	if not _update(update):
		return _reject("invalid algorithm_output")
	var times := _truth_times(update)
	if snapshots.size() != times.size():
		return _reject("truth snapshot count does not match reference times")
	for index in range(times.size()):
		if not _truth(snapshots[index], times[index]):
			return _reject("invalid truth snapshot at index %d" % index)
	if not last_update.is_empty():
		if update.update_id < last_update.update_id:
			return _reject("update_id regressed within this worker session")
		if update.update_id == last_update.update_id:
			if update != last_update or snapshots != _last_snapshots:
				return _reject("conflicting duplicate update_id")
			return true
	# Evaluation will consume this complete increment before display cache eviction.
	last_update = update
	_last_snapshots = snapshots
	truth_by_time.clear()
	for index in range(times.size()):
		truth_by_time[times[index]] = snapshots[index]
	edge_diagnostics.clear()
	if update.edge_events != null:
		for station in update.edge_events:
			for ray in station.rays:
				rays[JSON.stringify(ray.event_id)] = ray
			edge_diagnostics.append({"station_id": station.station_id,
				"station_session_id": station.station_session_id, "diagnostics": station.diagnostics})
	if update.center_events != null:
		for measurement in update.center_events.measurements:
			measurements[measurement.measurement_id] = measurement
		for track in update.center_events.tracks:
			tracks[track.track_id] = track
			if not track_history.has(track.track_id):
				track_history[track.track_id] = []
			if track.recent_position_enu_m != null:
				track_history[track.track_id].append({"time_ns": track.recent_time_ns,
					"position_enu_m": track.recent_position_enu_m})
	if update.station_states != null:
		for station in update.station_states:
			station_states[station.station_id] = station
	update_count += 1
	return true


func _reject(reason: String) -> bool:
	error = reason
	return false


func _record(value: Variant, fields: Array) -> bool:
	return value is Dictionary and value.size() == fields.size() and value.has_all(fields)


func _text(value: Variant) -> bool:
	return value is String and not value.is_empty()


func _number(value: Variant) -> bool:
	return (value is float or value is int) and is_finite(float(value))


func _optional_number(value: Variant) -> bool:
	return value == null or _number(value)


func _nonnegative(value: Variant) -> bool:
	return _number(value) and value >= 0


func _vector(value: Variant, nonnegative := false) -> bool:
	if not value is Array or value.size() != 3:
		return false
	for component in value:
		if not _number(component) or (nonnegative and component < 0):
			return false
	return true


func _optional_vector(value: Variant, nonnegative := false) -> bool:
	return value == null or _vector(value, nonnegative)


func _rotation(value: Variant) -> bool:
	return value is Array and value.size() == 3 and _vector(value[0]) \
		and _vector(value[1]) and _vector(value[2])


func _texts(value: Variant) -> bool:
	if not value is Array:
		return false
	for item in value:
		if not _text(item):
			return false
	return true


func _integer(value: Variant, signed := false) -> bool:
	# JSON numbers are doubles in Godot. Python sends contract integers as decimal strings.
	return value is String and value.is_valid_int() and str(value.to_int()) == value \
		and (signed or value.to_int() >= 0)


func _integers(record: Dictionary, fields: Array, signed := false) -> bool:
	for field in fields:
		if not _integer(record[field], signed):
			return false
		record[field] = record[field].to_int()
	return true


func _event_id(value: Variant) -> bool:
	if not value is Array or value.size() != 3 or not _text(value[0]) \
			or not _text(value[1]) or not _integer(value[2]):
		return false
	value[2] = value[2].to_int()
	return true


func _ray(ray: Variant, station: Dictionary) -> bool:
	if not _record(ray, ["event_id", "capture_time_ns", "origin_enu_m", "direction_enu_unit",
			"calibration_usage", "detection_score", "angular_std_rad"]):
		return false
	if not _event_id(ray.event_id) or ray.event_id[0] != station.station_id \
			or ray.event_id[1] != station.station_session_id:
		return false
	if not _integers(ray, ["capture_time_ns"], true) or not _vector(ray.origin_enu_m) \
			or not _vector(ray.direction_enu_unit):
		return false
	var direction: Array = ray.direction_enu_unit
	return is_equal_approx(Vector3(direction[0], direction[1], direction[2]).length_squared(), 1.0) \
		and ray.calibration_usage in ["valid", "frozen", "stale"] \
		and _nonnegative(ray.detection_score) and ray.detection_score <= 1 \
		and (ray.angular_std_rad == null or _nonnegative(ray.angular_std_rad))


func _edge(station: Variant) -> bool:
	if not _record(station, ["station_id", "station_session_id", "rays", "diagnostics"]) \
			or not _text(station.station_id) or not _text(station.station_session_id) \
			or not station.rays is Array or not station.diagnostics is Array:
		return false
	var ids := {}
	for ray in station.rays:
		if not _ray(ray, station):
			return false
		var key := JSON.stringify(ray.event_id)
		if ids.has(key):
			return false
		ids[key] = true
	for diagnostic in station.diagnostics:
		if not _record(diagnostic, ["capture_time_ns", "category", "reason_codes"]) \
				or not _integers(diagnostic, ["capture_time_ns"], true) \
				or not _text(diagnostic.category) or not _texts(diagnostic.reason_codes):
			return false
	return true


func _measurement(measurement: Variant) -> bool:
	if not _record(measurement, ["measurement_id", "reference_time_ns", "status", "position_enu_m",
			"position_std_enu_m", "supporting_ray_ids", "supporting_station_ids",
			"minimum_intersection_angle_deg", "geometry_condition"]):
		return false
	if not _text(measurement.measurement_id) or not _integers(measurement, ["reference_time_ns"], true) \
			or not measurement.status in ["located", "geometry_degenerate", "association_ambiguous",
				"insufficient_stations", "no_candidate_in_volume", "rejected_input"]:
		return false
	if not _optional_vector(measurement.position_enu_m) \
			or not _optional_vector(measurement.position_std_enu_m, true) \
			or not _texts(measurement.supporting_station_ids) or not measurement.supporting_ray_ids is Array \
			or not _optional_number(measurement.minimum_intersection_angle_deg) \
			or not _optional_number(measurement.geometry_condition):
		return false
	if measurement.status != "located" and (measurement.position_enu_m != null or measurement.position_std_enu_m != null):
		return false
	for event_id in measurement.supporting_ray_ids:
		if not _event_id(event_id):
			return false
	return true


func _track(track: Variant) -> bool:
	if not _record(track, ["track_id", "event_time_ns", "lifecycle", "position_enu_m", "velocity_enu_mps",
			"position_std_enu_m", "velocity_std_enu_mps", "recent_position_enu_m", "recent_time_ns"]):
		return false
	if not _text(track.track_id) or not _integers(track, ["event_time_ns"], true) \
			or not track.lifecycle in ["tentative", "confirmed", "coasting", "deleted"]:
		return false
	if not _optional_vector(track.position_enu_m) or not _optional_vector(track.velocity_enu_mps) \
			or not _optional_vector(track.position_std_enu_m, true) \
			or not _optional_vector(track.velocity_std_enu_mps, true) \
			or not _optional_vector(track.recent_position_enu_m):
		return false
	if (track.recent_position_enu_m == null) != (track.recent_time_ns == null):
		return false
	return track.recent_time_ns == null or _integers(track, ["recent_time_ns"], true)


func _voxels(voxels: Variant) -> bool:
	if voxels == null:
		return true
	if not _record(voxels, ["search_box_min_enu_m", "search_box_max_enu_m", "voxel_step_enu_m", "scored_voxels", "max_score"]) \
			or not _vector(voxels.search_box_min_enu_m) or not _vector(voxels.search_box_max_enu_m) \
			or not _vector(voxels.voxel_step_enu_m) or not _integers(voxels, ["max_score"]) \
			or not voxels.scored_voxels is Array:
		return false
	for axis in range(3):
		if voxels.voxel_step_enu_m[axis] <= 0 or voxels.search_box_min_enu_m[axis] >= voxels.search_box_max_enu_m[axis]:
			return false
	for voxel in voxels.scored_voxels:
		if not _record(voxel, ["grid_index", "score", "supporting_station_count"]) \
				or not _integers(voxel, ["score", "supporting_station_count"]) \
				or voxel.score <= 0 or voxel.score > voxels.max_score \
				or not voxel.grid_index is Array or voxel.grid_index.size() != 3:
			return false
		for axis in range(3):
			if not _integer(voxel.grid_index[axis]):
				return false
			voxel.grid_index[axis] = voxel.grid_index[axis].to_int()
	return true


func _center(center: Variant) -> bool:
	if not _record(center, ["measurements", "tracks", "window_count", "candidate_count", "association_count", "voxel_space"]) \
			or not center.measurements is Array or not center.tracks is Array \
			or not _integers(center, ["window_count", "candidate_count", "association_count"]) \
			or not _voxels(center.voxel_space):
		return false
	var ids := {}
	for measurement in center.measurements:
		if not _measurement(measurement) or ids.has(measurement.measurement_id):
			return false
		ids[measurement.measurement_id] = true
	for track in center.tracks:
		if not _track(track):
			return false
	return true


func _station(station: Variant) -> bool:
	if not _record(station, ["station_id", "configured_orientation_enu_from_main", "actual_orientation_enu_from_main",
			"calibrated_orientation_enu_from_main", "calibration_usage", "truth_orientation_error_rad", "calibration_orientation_error_rad"]):
		return false
	return _text(station.station_id) and _rotation(station.configured_orientation_enu_from_main) \
		and (station.actual_orientation_enu_from_main == null or _rotation(station.actual_orientation_enu_from_main)) \
		and (station.calibrated_orientation_enu_from_main == null or _rotation(station.calibrated_orientation_enu_from_main)) \
		and station.calibration_usage in [null, "valid", "frozen", "stale"] \
		and _optional_vector(station.truth_orientation_error_rad) and _optional_vector(station.calibration_orientation_error_rad)


func _update(update: Dictionary) -> bool:
	if not _record(update, ["update_id", "scenario_time_ns", "step_no", "edge_events", "center_events", "station_states"]) \
			or not _integers(update, ["update_id", "step_no"]) or not _integers(update, ["scenario_time_ns"], true):
		return false
	if update.edge_events != null:
		if not update.edge_events is Array:
			return false
		var stations := {}
		for station in update.edge_events:
			if not _edge(station):
				return false
			var key := JSON.stringify([station.station_id, station.station_session_id])
			if stations.has(key):
				return false
			stations[key] = true
	if update.center_events != null and not _center(update.center_events):
		return false
	if update.station_states != null:
		if not update.station_states is Array:
			return false
		var stations := {}
		for station in update.station_states:
			if not _station(station) or stations.has(station.station_id):
				return false
			stations[station.station_id] = true
	return true


func _truth_times(update: Dictionary) -> Array:
	var times := {update.scenario_time_ns: true}
	if update.edge_events != null:
		for station in update.edge_events:
			for ray in station.rays:
				times[ray.capture_time_ns] = true
	if update.center_events != null:
		for measurement in update.center_events.measurements:
			times[measurement.reference_time_ns] = true
	var ordered := times.keys()
	ordered.sort()
	return ordered


func _truth(snapshot: Variant, time_ns: int) -> bool:
	if not _record(snapshot, ["targets"]) or not snapshot.targets is Array:
		return false
	var ids := {}
	for target in snapshot.targets:
		if not _record(target, ["target_id", "time_ns", "position_enu_m", "velocity_enu_mps",
				"geometry_type", "bounding_radius_m", "visibility_by_station"]) \
				or not _text(target.target_id) or ids.has(target.target_id) \
				or not _integers(target, ["time_ns"], true) or target.time_ns != time_ns \
				or not _vector(target.position_enu_m) or not _vector(target.velocity_enu_mps) \
				or not target.geometry_type in ["sphere", "box", "aircraft"] \
				or not _nonnegative(target.bounding_radius_m) or not target.visibility_by_station is Array:
			return false
		ids[target.target_id] = true
		var stations := {}
		for visibility in target.visibility_by_station:
			if not _record(visibility, ["station_id", "in_field_of_view", "capture_eligible"]) \
					or not _text(visibility.station_id) or stations.has(visibility.station_id) \
					or not visibility.in_field_of_view is bool or not visibility.capture_eligible is bool:
				return false
			stations[visibility.station_id] = true
	return true
