## Scene controller: owns experiment time, motion logic and truth generation.
##
## Python previously owned these responsibilities. This controller implements the
## same deterministic physics and time-stepping as the original Python code, but
## runs entirely within Godot. It can operate headless without rendering.

class_name SceneController extends RefCounted

## Loaded scenario definition (from JSON)
var scenario: Dictionary = {}

## Current experiment time in seconds
var current_time_s := 0.0

## Current step counter
var current_step := 0

## Random seed for deterministic behavior
var random_seed := 42

## Frame period in seconds
var frame_period_s := 1.0 / 30.0

## Total duration in seconds
var duration_s := 10.0

## Error state
var _error_message := ""


## Load and validate a scenario from JSON file.
## Returns OK on success, FAILED on error (check get_error()).
func load_scenario(json_path: String) -> Error:
	var file := FileAccess.open(json_path, FileAccess.READ)
	if file == null:
		_error_message = "Failed to open scenario file: %s" % json_path
		return FAILED

	var content := file.get_as_text()
	file.close()

	var json := JSON.new()
	var parse_error := json.parse(content)
	if parse_error != OK:
		_error_message = "JSON parse error at line %d: %s" % [json.get_error_line(), json.get_error_message()]
		return FAILED

	var data = json.data
	if not data is Dictionary:
		_error_message = "Scenario root must be a JSON object"
		return FAILED

	# Validate format
	var format_version := str(data.get("format", ""))
	if format_version != "scenario-authoring-v2":
		_error_message = "Unsupported scenario format: %s (expected scenario-authoring-v2)" % format_version
		return FAILED

	# Extract basic parameters
	scenario = data
	random_seed = int(data.get("random_seed", 42))
	duration_s = float(data.get("duration_s", 10.0))
	var fps: float = float(data.get("fps", 30.0))
	if fps <= 0.0:
		_error_message = "FPS must be positive"
		return FAILED
	frame_period_s = 1.0 / fps

	# Validate stations
	if not data.has("stations") or not data.stations is Array:
		_error_message = "Scenario must have 'stations' array"
		return FAILED

	# Validate targets (can be empty)
	if not data.has("targets"):
		scenario["targets"] = []
	elif not data.targets is Array:
		_error_message = "'targets' must be an array"
		return FAILED

	# Validate each target's motion
	for target in scenario.targets:
		if not target is Dictionary:
			_error_message = "Each target must be an object"
			return FAILED
		if not target.has("motion"):
			_error_message = "Target '%s' missing 'motion'" % target.get("id", "?")
			return FAILED
		var motion: Dictionary = target.motion
		if not motion.has("type"):
			_error_message = "Motion missing 'type' for target '%s'" % target.get("id", "?")
			return FAILED

	current_time_s = 0.0
	current_step = 0
	_error_message = ""
	return OK


## Get the last error message
func get_error() -> String:
	return _error_message


## Advance simulation by one frame period and return a snapshot.
## The snapshot contains evaluated positions for all stations and targets.
func step() -> Dictionary:
	current_time_s += frame_period_s
	current_step += 1
	return build_snapshot()


## Build a complete scene snapshot at current_time_s.
## This is called by step() but can also be used independently for preview.
func build_snapshot() -> Dictionary:
	return {
		"time_s": current_time_s,
		"time_ns": int(current_time_s * 1e9),
		"step": current_step,
		"frame_period_s": frame_period_s,
		"stations": _evaluate_stations(),
		"targets": _evaluate_targets(),
		"scenario_id": scenario.get("scenario_id", "unknown"),
	}


## Evaluate all stations at current time.
## Stations are static in current implementation.
func _evaluate_stations() -> Array:
	var result := []
	for station_data in scenario.get("stations", []):
		var station: Dictionary = station_data
		result.append({
			"id": station.get("id", ""),
			"position_enu_m": station.get("position_enu_m", [0.0, 0.0, 0.0]),
		})
	return result


## Evaluate all targets at current time using their motion models.
func _evaluate_targets() -> Array:
	var result := []
	for target_data in scenario.get("targets", []):
		var target: Dictionary = target_data
		var position := _evaluate_motion(target, current_time_s)
		var velocity := _evaluate_velocity(target, current_time_s)

		result.append({
			"id": target.get("id", ""),
			"display_name": target.get("display_name", ""),
			"position_enu_m": [position.x, position.y, position.z],
			"velocity_enu_mps": [velocity.x, velocity.y, velocity.z],
			"geometry": target.get("geometry", {}),
		})
	return result


## Evaluate target position using its motion model.
func _evaluate_motion(target: Dictionary, time_s: float) -> Vector3:
	var motion: Dictionary = target.get("motion", {})
	var motion_type := str(motion.get("type", ""))

	match motion_type:
		"constant_velocity":
			return _motion_constant_velocity(motion, time_s)
		"orbit":
			return _motion_orbit(motion, time_s)
		_:
			push_error("Unknown motion type: %s" % motion_type)
			return Vector3.ZERO


## Evaluate target velocity using its motion model.
func _evaluate_velocity(target: Dictionary, time_s: float) -> Vector3:
	var motion: Dictionary = target.get("motion", {})
	var motion_type := str(motion.get("type", ""))

	match motion_type:
		"constant_velocity":
			return _velocity_constant_velocity(motion)
		"orbit":
			return _velocity_orbit(motion, time_s)
		_:
			return Vector3.ZERO


## Constant velocity motion: position = initial + velocity * time
func _motion_constant_velocity(motion: Dictionary, time_s: float) -> Vector3:
	var pos0 := _vec3(motion.get("position_enu_m", [0.0, 0.0, 0.0]))
	var vel := _vec3(motion.get("velocity_enu_mps", [0.0, 0.0, 0.0]))
	return pos0 + vel * time_s


## Constant velocity: velocity is constant
func _velocity_constant_velocity(motion: Dictionary) -> Vector3:
	return _vec3(motion.get("velocity_enu_mps", [0.0, 0.0, 0.0]))


## Orbital motion: elliptical path with vertical oscillation
## Python reference: behaviors.py, lines 67-79
func _motion_orbit(motion: Dictionary, time_s: float) -> Vector3:
	var center_e := float(motion.get("center_e", 0.0))
	var center_n := float(motion.get("center_n", 0.0))
	var center_u := float(motion.get("center_u", 0.0))
	var radius_e := float(motion.get("radius_e", 10.0))
	var radius_n := float(motion.get("radius_n", 10.0))
	var period_s := float(motion.get("period_s", 10.0))
	var phase_rad := float(motion.get("phase_rad", 0.0))
	var amplitude := float(motion.get("vertical_amplitude_m", 0.0))

	if period_s <= 0.0:
		push_error("Orbit period must be positive")
		return Vector3(center_e, center_n, center_u)

	var omega := TAU / period_s
	var phase := phase_rad + omega * time_s

	var pos_e := center_e + radius_e * sin(phase)
	var pos_n := center_n + radius_n * cos(phase)
	var pos_u := center_u + amplitude * sin(2.0 * phase)

	return Vector3(pos_e, pos_n, pos_u)


## Orbital velocity: derivative of position
func _velocity_orbit(motion: Dictionary, time_s: float) -> Vector3:
	var radius_e := float(motion.get("radius_e", 10.0))
	var radius_n := float(motion.get("radius_n", 10.0))
	var period_s := float(motion.get("period_s", 10.0))
	var phase_rad := float(motion.get("phase_rad", 0.0))
	var amplitude := float(motion.get("vertical_amplitude_m", 0.0))

	if period_s <= 0.0:
		return Vector3.ZERO

	var omega := TAU / period_s
	var phase := phase_rad + omega * time_s

	var vel_e := radius_e * omega * cos(phase)
	var vel_n := -radius_n * omega * sin(phase)
	var vel_u := 2.0 * amplitude * omega * cos(2.0 * phase)

	return Vector3(vel_e, vel_n, vel_u)


## Convert array [x, y, z] to Vector3
func _vec3(array: Variant) -> Vector3:
	if not array is Array or array.size() < 3:
		return Vector3.ZERO
	return Vector3(float(array[0]), float(array[1]), float(array[2]))


## Check if scenario has finished
func is_finished() -> bool:
	return current_time_s >= duration_s


## Get total number of steps for this scenario
func get_total_steps() -> int:
	return int(ceil(duration_s / frame_period_s))


## Reset controller to initial state
func reset() -> void:
	current_time_s = 0.0
	current_step = 0
