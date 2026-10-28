## CLI entry point for Godot-native scenario execution.
##
## This replaces Python's scenario runner for headless operation. It can:
## - Load and validate scenario JSON files
## - Run finite scenarios with deterministic time-stepping
## - Output truth data in JSONL format
## - Perform --check validation without rendering
##
## Usage:
##   godot --headless --script main.gd -- --scenario path.json --output ./results/ --mode finite
##   godot --headless --script main.gd -- --scenario path.json --mode check

extends Node

const SceneController = preload("res://scene_controller.gd")

## Command-line arguments
var args := {
	"scenario": "",
	"output": "",
	"mode": "finite",
	"no_video": false,
	"render_every": 1,
}

## Scene controller
var controller = null

## Worker node for rendering
var worker: Node = null

## Output viewport for capturing frames
var viewport: SubViewport = null


func _ready() -> void:
	# Disable default rendering loop
	get_tree().auto_accept_quit = false

	if not _parse_args():
		print_rich("[color=red]Error:[/color] Invalid command-line arguments")
		print_usage()
		get_tree().quit(1)
		return

	# Validate required arguments
	if args.scenario.is_empty():
		print_rich("[color=red]Error:[/color] --scenario is required")
		print_usage()
		get_tree().quit(1)
		return

	# Load scenario
	controller = SceneController.new()
	var load_error: Error = controller.load_scenario(args.scenario)
	if load_error != OK:
		print_rich("[color=red]Error:[/color] Failed to load scenario: %s" % controller.get_error())
		get_tree().quit(1)
		return

	# Initialize rendering if needed for finite mode
	if args.mode == "finite" and not args.no_video:
		_setup_rendering()

	# Execute requested mode
	match args.mode:
		"check":
			_mode_check()
		"finite":
			await _mode_finite()
		_:
			print_rich("[color=red]Error:[/color] Unknown mode: %s" % args.mode)
			print_usage()
			get_tree().quit(1)


func _parse_args() -> bool:
	var user_args := OS.get_cmdline_user_args()
	var i := 0
	while i < user_args.size():
		match user_args[i]:
			"--scenario":
				if i + 1 >= user_args.size():
					return false
				args.scenario = user_args[i + 1]
				i += 2
			"--output":
				if i + 1 >= user_args.size():
					return false
				args.output = user_args[i + 1]
				i += 2
			"--mode":
				if i + 1 >= user_args.size():
					return false
				args.mode = user_args[i + 1]
				i += 2
			"--no-video":
				args.no_video = true
				i += 1
			"--render-every":
				if i + 1 >= user_args.size():
					return false
				args.render_every = int(user_args[i + 1])
				i += 2
			_:
				# Unknown argument, skip
				i += 1
	return true


func print_usage() -> void:
	print("""
Usage: godot --headless --script main.gd -- [OPTIONS]

Options:
  --scenario PATH     Scenario JSON file (required)
  --output PATH       Output directory for results
  --mode MODE         Execution mode: check, finite (default: finite)
  --no-video          Skip video rendering (truth data only)
  --render-every N    Render every Nth frame (default: 1)

Modes:
  check    Validate scenario without executing
  finite   Run complete scenario and output results

Examples:
  # Validate a scenario
  godot --headless --script main.gd -- --scenario scenarios/empty.json --mode check

  # Run finite scenario with output
  godot --headless --script main.gd -- --scenario scenarios/empty.json --output ./results/ --mode finite
""")


## Mode: check - validate scenario without execution
func _mode_check() -> void:
	var scenario_id: String = controller.scenario.get("scenario_id", "unknown")
	var station_count: int = controller.scenario.get("stations", []).size()
	var target_count: int = controller.scenario.get("targets", []).size()

	print_rich("[color=green]✓[/color] Valid scenario: %s" % scenario_id)
	print("  Stations: %d" % station_count)
	print("  Targets: %d" % target_count)
	print("  Duration: %.1f s" % controller.duration_s)
	print("  FPS: %.1f" % (1.0 / controller.frame_period_s))
	print("  Total steps: %d" % controller.get_total_steps())

	get_tree().quit(0)


## Mode: finite - run complete scenario
func _mode_finite() -> void:
	if args.output.is_empty():
		print_rich("[color=red]Error:[/color] --output is required for finite mode")
		get_tree().quit(1)
		return

	# Create output directory
	var dir := DirAccess.open(".")
	if dir == null:
		print_rich("[color=red]Error:[/color] Failed to access filesystem")
		get_tree().quit(1)
		return

	if dir.dir_exists(args.output):
		print_rich("[color=red]Error:[/color] Output directory already exists: %s" % args.output)
		get_tree().quit(1)
		return

	var mk_error := dir.make_dir_recursive(args.output)
	if mk_error != OK:
		print_rich("[color=red]Error:[/color] Failed to create output directory: %s" % args.output)
		get_tree().quit(1)
		return

	print("Running scenario: %s" % controller.scenario.get("scenario_id", "unknown"))
	print("Output directory: %s" % args.output)
	print("Total steps: %d" % controller.get_total_steps())

	# Open output files
	var truth_file := FileAccess.open(args.output.path_join("truth.jsonl"), FileAccess.WRITE)
	if truth_file == null:
		print_rich("[color=red]Error:[/color] Failed to create truth.jsonl")
		get_tree().quit(1)
		return

	var timing_file := FileAccess.open(args.output.path_join("timing.jsonl"), FileAccess.WRITE)
	if timing_file == null:
		truth_file.close()
		print_rich("[color=red]Error:[/color] Failed to create timing.jsonl")
		get_tree().quit(1)
		return

	# Run simulation
	var start_time := Time.get_ticks_usec()
	var step_count := 0
	var frame_files: Array[String] = []

	while not controller.is_finished():
		var step_start := Time.get_ticks_usec()
		var snapshot: Dictionary = controller.step()

		# Render frame if video is enabled and render interval matches
		var should_render := not args.no_video and (step_count % args.render_every == 0)
		var frame_path := ""

		if should_render:
			frame_path = await _render_frame(snapshot, step_count)
			if not frame_path.is_empty():
				frame_files.append(frame_path)

		var step_end := Time.get_ticks_usec()

		# Build truth record
		var truth := _build_truth_record(snapshot, frame_path)
		truth_file.store_line(JSON.stringify(truth))

		# Write timing data
		var timing := {
			"step": snapshot.step,
			"time_s": snapshot.time_s,
			"elapsed_us": step_end - step_start,
		}
		timing_file.store_line(JSON.stringify(timing))

		step_count += 1

		# Progress indicator every 100 steps
		if step_count % 100 == 0:
			var progress: float = (snapshot.time_s / controller.duration_s) * 100.0
			print("  Step %d / %d (%.1f%%)" % [step_count, controller.get_total_steps(), progress])

	var total_time := Time.get_ticks_usec() - start_time

	truth_file.close()
	timing_file.close()

	# Write manifest
	var manifest := {
		"format": "godot-scenario-output-v1",
		"scenario_id": controller.scenario.get("scenario_id", "unknown"),
		"scenario_path": args.scenario,
		"duration_s": controller.duration_s,
		"fps": 1.0 / controller.frame_period_s,
		"total_steps": step_count,
		"total_time_us": total_time,
		"no_video": args.no_video,
		"render_every": args.render_every,
		"frame_files": frame_files,
	}

	var manifest_file := FileAccess.open(args.output.path_join("manifest.json"), FileAccess.WRITE)
	if manifest_file != null:
		manifest_file.store_string(JSON.stringify(manifest, "  "))
		manifest_file.close()

	# Write authoring source (for reference)
	var authoring_file := FileAccess.open(args.output.path_join("authoring.json"), FileAccess.WRITE)
	if authoring_file != null:
		authoring_file.store_string(JSON.stringify(controller.scenario, "  "))
		authoring_file.close()

	print_rich("[color=green]✓[/color] Completed %d steps in %.3f seconds" % [step_count, total_time / 1e6])
	print("Results written to: %s" % args.output)

	get_tree().quit(0)


## Setup rendering infrastructure
func _setup_rendering() -> void:
	# Create a viewport for rendering
	viewport = SubViewport.new()
	viewport.size = Vector2i(640, 480)  # Default size, will be overridden per frame
	viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	viewport.transparent_bg = false
	add_child(viewport)

	# Load and instantiate worker scene
	var worker_script := load("res://worker.gd")
	if worker_script == null:
		push_error("Failed to load worker.gd")
		get_tree().quit(2)
		return

	worker = Node.new()
	worker.set_script(worker_script)
	add_child(worker)

	print("Rendering infrastructure initialized")


## Render a single frame and save as PNG
func _render_frame(snapshot: Dictionary, frame_number: int) -> String:
	if viewport == null or worker == null:
		return ""

	# TODO: Build render request from snapshot
	# For now, this is a placeholder - full integration requires:
	# 1. Convert snapshot to render request format
	# 2. Call worker's render method
	# 3. Capture viewport texture
	# 4. Save as PNG

	# Placeholder: create empty frame file
	var frame_filename := "%06d.png" % frame_number
	var frame_path := args.output.path_join(frame_filename)

	# For now, just return the path without actual rendering
	# Full implementation will happen in phase 1.3
	return frame_filename


## Build truth record from snapshot
func _build_truth_record(snapshot: Dictionary, frame_path: String) -> Dictionary:
	var truth := {
		"scenario_id": snapshot.get("scenario_id", "unknown"),
		"time_s": snapshot.get("time_s", 0.0),
		"time_ns": snapshot.get("time_ns", 0),
		"step": snapshot.get("step", 0),
		"frame_period_s": snapshot.get("frame_period_s", 0.0),
		"logical_scene": {
			"stations": snapshot.get("stations", []),
			"targets": snapshot.get("targets", []),
			"world_objects": [],  # Will be populated when we have geometry
		},
		"rays": [],  # Will be populated with raycasting results
		"camera_frames": [],  # Will be populated with camera frame data
	}

	# Add frame reference if rendered
	if not frame_path.is_empty():
		truth["frame_path"] = frame_path

	return truth
