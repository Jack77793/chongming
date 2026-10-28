extends SceneTree

const SceneController = preload("res://scene_controller.gd")

func _init():
	# Quick syntax check
	var controller := SceneController.new()
	var result := controller.load_scenario("../../../../scenarios/empty.json")

	if result == OK:
		print("✓ Scenario loaded successfully")
		print("  Duration: %.1fs, FPS: %.0f" % [controller.duration_s, 1.0 / controller.frame_period_s])
		print("  Stations: %d, Targets: %d" % [controller.scenario.stations.size(), controller.scenario.targets.size()])
		quit(0)
	else:
		print("✗ Failed to load scenario: %s" % controller.get_error())
		quit(1)
