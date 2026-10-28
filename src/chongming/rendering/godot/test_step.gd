extends SceneTree

const SceneController = preload("res://scene_controller.gd")

func _init():
	print("=== Testing Scenario Loading ===")

	# Quick syntax check
	var controller := SceneController.new()
	var result := controller.load_scenario("../../../../scenarios/empty.json")

	if result == OK:
		print("✓ Scenario loaded successfully")
		print("  Duration: %.1fs, FPS: %.0f" % [controller.duration_s, 1.0 / controller.frame_period_s])
		print("  Stations: %d, Targets: %d" % [controller.scenario.stations.size(), controller.scenario.targets.size()])
		print("  Total steps: %d" % controller.get_total_steps())

		print("\n=== Testing Step Execution ===")
		for i in range(3):
			var snapshot: Dictionary = controller.step()
			print("Step %d: time=%.3fs, step=%d" % [i, snapshot.time_s, snapshot.step])

		print("\n✓ All tests passed")
		quit(0)
	else:
		print("✗ Failed to load scenario: %s" % controller.get_error())
		quit(1)
