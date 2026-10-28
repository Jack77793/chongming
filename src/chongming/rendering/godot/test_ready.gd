extends Node

const SceneController = preload("res://scene_controller.gd")

func _ready() -> void:
	print("TEST: _ready() called")

	var controller := SceneController.new()
	var result := controller.load_scenario("../../../../scenarios/empty.json")

	if result == OK:
		print("✓ Scenario loaded")
		print("  Duration: %.1fs" % controller.duration_s)
		print("  Stations: %d" % controller.scenario.stations.size())
	else:
		print("✗ Failed: %s" % controller.get_error())

	print("TEST: Calling quit()")
	get_tree().quit(0)
	print("TEST: After quit()")
