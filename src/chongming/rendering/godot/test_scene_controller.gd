## Test scene controller logic without full Godot integration.
## This can be run with: godot --headless --script test_scene_controller.gd

extends SceneTree

const SceneController = preload("res://scene_controller.gd")


func _init() -> void:
	print("Testing SceneController...")
	var separator := "============================================================"
	print(separator)

	var passed := 0
	var failed := 0

	# Test 1: Load empty scenario
	if test_load_empty():
		print("✓ Test 1: Load empty scenario - PASSED")
		passed += 1
	else:
		print("✗ Test 1: Load empty scenario - FAILED")
		failed += 1

	# Test 2: Constant velocity motion
	if test_constant_velocity():
		print("✓ Test 2: Constant velocity motion - PASSED")
		passed += 1
	else:
		print("✗ Test 2: Constant velocity motion - FAILED")
		failed += 1

	# Test 3: Orbit motion
	if test_orbit_motion():
		print("✓ Test 3: Orbit motion - PASSED")
		passed += 1
	else:
		print("✗ Test 3: Orbit motion - FAILED")
		failed += 1

	var sep := "============================================================"
	print(sep)
	print("Results: %d passed, %d failed" % [passed, failed])

	quit(0 if failed == 0 else 1)


func test_load_empty() -> bool:
	var controller = SceneController.new()
	var scenario_path := "test_empty.json"

	# Create test scenario file
	var test_json := {
		"format": "scenario-authoring-v2",
		"scenario_id": "test-empty",
		"random_seed": 42,
		"duration_s": 2.0,
		"fps": 30.0,
		"stations": [
			{"id": "station-01", "position_enu_m": [0, 0, 4]},
		],
		"targets": []
	}

	var file := FileAccess.open(scenario_path, FileAccess.WRITE)
	if file == null:
		print("  Error: Failed to create test file")
		return false
	file.store_string(JSON.stringify(test_json, "  "))
	file.close()

	var err := controller.load_scenario(scenario_path)
	DirAccess.remove_absolute(scenario_path)

	if err != OK:
		print("  Error: %s" % controller.get_error())
		return false

	if controller.scenario.scenario_id != "test-empty":
		print("  Error: Wrong scenario_id: %s" % controller.scenario.scenario_id)
		return false

	if controller.duration_s != 2.0:
		print("  Error: Wrong duration: %f" % controller.duration_s)
		return false

	return true


func test_constant_velocity() -> bool:
	var controller = SceneController.new()

	# Inline test scenario
	controller.scenario = {
		"format": "scenario-authoring-v2",
		"scenario_id": "test-velocity",
		"random_seed": 42,
		"duration_s": 10.0,
		"fps": 1.0,
		"stations": [],
		"targets": [
			{
				"id": "target-01",
				"geometry": {"type": "sphere", "radius_m": 1.0},
				"motion": {
					"type": "constant_velocity",
					"position_enu_m": [0, 0, 10],
					"velocity_enu_mps": [3, 0, 0]
				}
			}
		]
	}
	controller.duration_s = 10.0
	controller.frame_period_s = 1.0

	# Step 1 second
	controller.step()
	var targets := controller._evaluate_targets()

	if targets.size() != 1:
		print("  Error: Expected 1 target, got %d" % targets.size())
		return false

	var pos: Array = targets[0].position_enu_m
	# After 1 second: x = 0 + 3*1 = 3
	if abs(pos[0] - 3.0) > 0.001:
		print("  Error: Wrong position: %v (expected [3, 0, 10])" % [pos])
		return false

	# Step another 2 seconds
	controller.step()
	controller.step()
	targets = controller._evaluate_targets()
	pos = targets[0].position_enu_m
	# After 3 seconds: x = 0 + 3*3 = 9
	if abs(pos[0] - 9.0) > 0.001:
		print("  Error: Wrong position after 3 steps: %v (expected [9, 0, 10])" % [pos])
		return false

	return true


func test_orbit_motion() -> bool:
	var controller = SceneController.new()

	controller.scenario = {
		"format": "scenario-authoring-v2",
		"scenario_id": "test-orbit",
		"duration_s": 20.0,
		"fps": 1.0,
		"stations": [],
		"targets": [
			{
				"id": "orbiter",
				"motion": {
					"type": "orbit",
					"center_e": 0.0,
					"center_n": 0.0,
					"center_u": 20.0,
					"radius_e": 10.0,
					"radius_n": 10.0,
					"period_s": 10.0,
					"phase_rad": 0.0,
					"vertical_amplitude_m": 2.0
				}
			}
		]
	}
	controller.duration_s = 20.0
	controller.frame_period_s = 1.0

	# At t=0, phase=0: x = center_e + radius_e * sin(0) = 0
	#                  y = center_n + radius_n * cos(0) = 10
	controller.current_time_s = 0.0
	var targets := controller._evaluate_targets()
	var pos: Array = targets[0].position_enu_m

	if abs(pos[0] - 0.0) > 0.001 or abs(pos[1] - 10.0) > 0.001:
		print("  Error: Wrong initial position: %v (expected [0, 10, 20])" % [pos])
		return false

	# At t=2.5s (quarter period), phase=π/2: x = 10, y = 0
	controller.current_time_s = 2.5
	targets = controller._evaluate_targets()
	pos = targets[0].position_enu_m

	if abs(pos[0] - 10.0) > 0.001 or abs(pos[1] - 0.0) > 0.001:
		print("  Error: Wrong position at T/4: %v (expected [10, 0, ~20])" % [pos])
		return false

	return true
