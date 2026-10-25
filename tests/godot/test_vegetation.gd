extends SceneTree

const Vegetation = preload("res://vegetation.gd")
const Worker = preload("res://worker.gd")
const Assets = preload("res://vegetation_assets.gd")

class PreviewWorker extends Worker:
	func _ready() -> void:
		pass

var failures: Array[String] = []


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	# Dummy RenderingServer discards MultiMesh buffers; validate the real resource backend.
	DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_MINIMIZED)
	_test_generator()
	_test_worker_cache()
	_test_screen_size_lod()
	_test_understory_wind()
	_test_mesh_normals()
	_test_heightfield_ground_selection()
	await _test_freeview_world()
	if failures.is_empty():
		print("vegetation tests passed")
		quit(0)
	else:
		for failure in failures:
			push_error(failure)
		quit(1)


func _test_freeview_world() -> void:
	var worker := PreviewWorker.new()
	root.add_child(worker)
	worker.set_process(false)
	worker._setup_world()
	var box := MeshInstance3D.new()
	box.mesh = BoxMesh.new()
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.albedo_color = Color.RED
	box.material_override = material
	box.position.y = 1.0
	worker._world.add_child(box)
	worker._free_camera.position = Vector3(0.0, 1.0, 4.0)
	worker._free_camera.look_at(Vector3(0.0, 1.0, 0.0))
	worker._free_camera.make_current()
	worker._free_viewport.size = Vector2i(64, 64)
	await process_frame
	worker._free_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
	RenderingServer.force_draw(false)
	var image := worker._free_viewport.get_texture().get_image()
	var center := image.get_pixel(32, 32)
	_check(center.r > 0.8 and center.g < 0.1 and center.b < 0.1,
		"freeview must render geometry from the sensor's shared 3D world")
	worker.free()


func _test_generator() -> void:
	var tree_visual := {"kind": "tree", "height": 9.0, "radius": 3.0, "seed": 12345,
		"lod_near": 120.0, "lod_far": 640.0}
	var first := Vegetation.create(tree_visual, [0.12, 0.35, 0.08], 0.8)
	var replay := Vegetation.create(tree_visual, [0.12, 0.35, 0.08], 0.8)
	var different := Vegetation.create(
		tree_visual.merged({"seed": 54321}, true), [0.12, 0.35, 0.08], 0.8
	)
	var first_trunk := first.get_node("Model/Trunk") as MeshInstance3D
	var first_crown := first.get_node("Model/Crown") as MeshInstance3D
	var replay_trunk := replay.get_node("Model/Trunk") as MeshInstance3D
	var replay_crown := replay.get_node("Model/Crown") as MeshInstance3D
	var different_crown := different.get_node("Model/Crown") as MeshInstance3D
	_check(first_trunk.mesh.get_surface_count() > 0, "tree trunk should contain at least one surface")
	_check(first_crown.mesh.get_surface_count() > 0, "tree crown should contain at least one surface")
	_check(first_trunk.mesh == replay_trunk.mesh, "the same tree seed must share the same trunk mesh")
	_check(first_crown.mesh == replay_crown.mesh, "the same tree seed must share the same crown mesh")
	_check(first_trunk.visibility_range_end == 640.0, "tree trunk should use the far LOD distance")
	_check(first_crown.visibility_range_end == 640.0, "tree crown should use the far LOD distance")
	var first_far := first.get_node_or_null("FarModel") as Node3D
	_check(first_far != null and not first_far.visible, "trees need a hidden far LOD model")
	_check(first_far != null and first_far.get_node("FarTree") is MeshInstance3D,
		"the far LOD model should contain a mesh")
	_check(first_crown.get_meta("native_height", 0.0) > 0.0, "crown needs its native height for wind")
	var crown_material := first_crown.mesh.surface_get_material(0)
	_check(crown_material is BaseMaterial3D, "crown surfaces must have a material")
	if crown_material is BaseMaterial3D:
		_check(crown_material.transparency == BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR,
			"crown cards should use alpha scissor, not alpha blending")
		_check(crown_material.cull_mode == BaseMaterial3D.CULL_DISABLED,
			"crown cards should remain visible from both sides")
	_check(str(first.get_meta("variant")) != str(different.get_meta("variant")),
		"different seeds should select different tree variants")
	_check(first_crown.mesh != different_crown.mesh, "different tree variants should use different crowns")

	var shrub := Vegetation.create({"kind": "shrub", "height": 2.2, "radius": 2.8,
		"seed": 91, "lod_near": 120.0, "lod_far": 640.0}, [0.1, 0.3, 0.07], 0.8)
	var shrub_foliage := shrub.get_node("Model/Foliage") as MeshInstance3D
	_check(shrub_foliage.mesh.get_surface_count() > 0, "shrubs should use an authored card mesh")
	_check(shrub_foliage.visibility_range_end == 640.0, "shrubs should use the far LOD distance")
	_check(not str(shrub.get_meta("variant", "")).is_empty(), "shrubs need a deterministic variant")

	var grass_visual := {"kind": "grass", "width": 10.0, "depth": 6.0, "height": 0.5,
		"density": 12.0, "seed": 92, "lod_near": 120.0, "lod_far": 640.0}
	var grass := Vegetation.create(grass_visual, [0.16, 0.34, 0.08], 0.75)
	var grass_replay := Vegetation.create(grass_visual, [0.16, 0.34, 0.08], 0.75)
	var clumps := grass.get_node("Clumps") as MultiMeshInstance3D
	var replay_clumps := grass_replay.get_node("Clumps") as MultiMeshInstance3D
	_check(clumps.multimesh.instance_count > 0, "grass should contain deterministic clumps")
	_check(clumps.multimesh.mesh == replay_clumps.multimesh.mesh, "grass patches should share one clump mesh")
	_check(clumps.multimesh.get_instance_transform(0) == replay_clumps.multimesh.get_instance_transform(0),
		"grass placement should replay from its stable seed")
	_check(clumps.multimesh.custom_aabb.has_volume(), "grass needs bounds covering all clumps")
	_check(is_zero_approx(clumps.multimesh.get_instance_transform(0).origin.z),
		"grass clumps must sit on the body-space ground plane")
	_check(is_zero_approx(clumps.multimesh.custom_aabb.position.z),
		"grass bounds must start on the body-space ground plane")
	_check(clumps.visibility_range_end == 120.0, "grass should be culled after the near LOD")
	_check(clumps.cast_shadow == GeometryInstance3D.SHADOW_CASTING_SETTING_OFF,
		"grass clump shadows should stay off for now")

	first.free()
	replay.free()
	different.free()
	shrub.free()
	grass.free()
	grass_replay.free()


func _test_screen_size_lod() -> void:
	_check(Assets.tree_count() == 12, "all 12 vendored tree variants should be available")
	_check(Assets.shrub_count() == 6, "all 6 shrub/hedge variants should be available")
	_check(Assets.grass_count() == 12, "all 12 grass/clover/flower variants should be available")
	var worker := Worker.new()
	worker._world = Node3D.new()
	root.add_child(worker._world)
	worker._camera = Camera3D.new()
	worker._world.add_child(worker._camera)
	worker._lod_focal_y = 800.0
	var pair := _tree_pair(12345)
	worker._camera.position = Vector3(0.0, 0.0, 4.0)
	worker._sync_entities(pair)
	var tree := worker._instances[0]
	var model := tree.get_node("Model") as Node3D
	var far_model := tree.get_node("FarModel") as Node3D
	var crown := tree.get_node("Model/Crown") as Node3D
	var trunk := tree.get_node("Model/Trunk") as GeometryInstance3D
	_check(model.visible and not far_model.visible, "a near tree should use the detailed LOD")
	_check(trunk.cast_shadow == GeometryInstance3D.SHADOW_CASTING_SETTING_ON,
		"a near tree should cast shadows")
	worker._camera.position = Vector3(0.0, 0.0, 1000.0)
	worker._sync_entities(pair)
	_check(not model.visible and far_model.visible, "a small on-screen tree should use the billboard LOD")
	_check(trunk.cast_shadow == GeometryInstance3D.SHADOW_CASTING_SETTING_OFF,
		"a far tree should stop casting shadows")
	_check(crown.position == Vector3.ZERO and crown.scale == Vector3.ONE,
		"a far tree should stop animating its crown")
	_check(worker._instances[0] == tree, "LOD changes must not rebuild tree resources")
	worker._world.free()
	worker.free()


func _test_understory_wind() -> void:
	var worker := Worker.new()
	worker._world = Node3D.new()
	worker._wind_active = true
	worker._wind_mean = Vector2(3.0, 0.5)
	worker._wind_turbulence = 0.4
	worker._wind_time = 1.25
	var shrub := Vegetation.create(
		{"kind": "shrub", "height": 1.6, "radius": 1.6, "seed": 7,
		 "lod_near": 120.0, "lod_far": 640.0},
		[0.1, 0.3, 0.07], 0.8
	)
	worker._world.add_child(shrub)
	var shrub_model := shrub.get_node("Model") as Node3D
	worker._apply_understory_wind(shrub, 200.0, "shrub")
	_check(shrub_model.position.length() > 0.001, "shrubs should respond to the wind field")
	_check(shrub_model.scale != Vector3.ONE, "shrub wind should include a small scale change")
	worker._wind_active = false
	worker._apply_understory_wind(shrub, 200.0, "shrub")
	_check(shrub_model.position == Vector3.ZERO, "shrubs should return to rest without wind")
	var grass := Vegetation.create(
		{"kind": "grass", "width": 10.0, "depth": 8.0, "height": 0.4, "density": 5.0,
		 "seed": 9, "lod_near": 120.0, "lod_far": 640.0},
		[0.1, 0.3, 0.07], 0.8
	)
	worker._world.add_child(grass)
	worker._wind_active = true
	var clumps := grass.get_node("Clumps") as Node3D
	worker._apply_understory_wind(grass, 20.0, "grass")
	_check(clumps.position.length() > 0.0001, "grass patches should respond to the wind field")
	worker._wind_active = false
	worker._apply_understory_wind(grass, 20.0, "grass")
	_check(clumps.position == Vector3.ZERO, "grass should return to rest without wind")
	worker._world.free()
	worker.free()


func _test_mesh_normals() -> void:
	var worker := Worker.new()
	var mesh := worker._mesh({
		"type": 2,
		"parameters": [],
		"vertices": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
		"triangles": [[0, 1, 2]],
	})
	var positions: PackedVector3Array = mesh.surface_get_arrays(0)[Mesh.ARRAY_VERTEX]
	_check(positions[0] == Vector3(0.0, 1.0, 0.0)
		and positions[1] == Vector3(1.0, 0.0, 0.0)
		and positions[2] == Vector3(0.0, 0.0, 0.0),
		"mesh submission must use Godot's clockwise front-face winding")
	var normals: PackedVector3Array = mesh.surface_get_arrays(0)[Mesh.ARRAY_NORMAL]
	_check(normals.size() == 3, "generated mesh should carry per-vertex normals")
	for normal in normals:
		_check(normal.z > 0.9, "a counter-clockwise ENU ground triangle should face up in body space")
	worker.free()


func _test_heightfield_ground_selection() -> void:
	var worker := Worker.new()
	worker._world = Node3D.new()
	worker._ground = MeshInstance3D.new()
	worker._ground.visible = true
	var terrain := {
		"type": 2,
		"parameters": [],
		"vertices": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
		"triangles": [[0, 1, 2]],
		"surface": "terrain",
		"color": [0.2, 0.3, 0.1],
		"reflectance": 0.8,
		"object_id": "terrain",
		"visual": null,
		"surface_detail": 0.0,
		"category": "terrain",
		"position": [0.0, 0.0, 0.0],
		"rotation": _identity_rotation(),
	}
	worker._sync_entities([terrain])
	_check(not worker._ground.visible,
		"heightfield terrain must disable the fallback ground and its shadows")
	worker._sync_entities([])
	_check(worker._ground.visible,
		"flat terrain must restore the fallback ground")
	worker._world.free()
	worker._ground.free()
	worker.free()


func _test_worker_cache() -> void:
	var worker := Worker.new()
	worker._world = Node3D.new()
	var pair := _tree_pair(12345)
	worker._sync_entities(pair)
	_check(worker._instances.size() == 2, "a tree trunk/canopy pair keeps both entity slots")
	var original := worker._instances[0]
	_check(original.has_node("Model/Crown"), "the trunk entity should carry the whole card tree")
	_check(worker._instances[1] is Node3D and not worker._instances[1].has_node("Model"),
		"the canopy entity should be a pose-only anchor")
	var crown := original.get_node("Model/Crown") as Node3D
	_check(crown.position == Vector3.ZERO, "an upright tree should have no crown displacement")

	var moved := pair.duplicate(true)
	moved[0].position = [2.0, 3.0, 4.0]
	moved[1].position = [2.0, 3.0, 6.94]
	worker._sync_entities(moved)
	_check(worker._instances[0] == original, "pose-only updates must reuse generated tree resources")
	_check(worker._instances[0].position == Vector3(2.0, 4.0, -3.0), "pose updates must still reach the tree")
	_check(crown.position == Vector3.ZERO, "changing position without wind must not move the crown")

	var windy := moved.duplicate(true)
	windy[1].rotation = _tilt_rotation(0.2)
	worker._sync_entities(windy)
	_check(worker._instances[0] == original, "wind changes must not rebuild tree resources")
	_check(crown.position.length() > 0.01, "a sampled canopy rotation should deform the crown")
	_check(crown.scale != Vector3.ONE, "crown wind should include a small scale change")
	windy[1].rotation = _identity_rotation()
	worker._sync_entities(windy)
	_check(crown.position == Vector3.ZERO and crown.scale == Vector3.ONE,
		"the crown must return to rest when the wind pose is upright")

	var changed := windy.duplicate(true)
	changed[1].visual = changed[1].visual.merged({"radius": 3.5}, true)
	worker._sync_entities(changed)
	_check(worker._instances[0] != original, "crown dimension changes must rebuild the card tree")
	worker._sync_entities([])
	_check(worker._instances.is_empty() and worker._signatures.is_empty(), "removed entities must leave no cached nodes")
	# The production protocol uploads immutable definitions once and then sends
	# only a resource token plus pose. This path must preserve the same cache rule.
	var definition := _tree_pair(54321)
	definition[0]["resource"] = "tree-trunk-resource"
	definition[1]["resource"] = "tree-canopy-resource"
	worker._sync_resources(definition)
	var light: Array = [
		{"resource": "tree-trunk-resource", "position": [1.0, 2.0, 3.0],
		 "rotation": _identity_rotation()},
		{"resource": "tree-canopy-resource", "position": [1.0, 2.0, 5.94],
		 "rotation": _identity_rotation()},
	]
	worker._sync_entities(light)
	var resource_instance := worker._instances[0]
	light[0]["position"] = [4.0, 5.0, 6.0]
	light[1]["position"] = [4.0, 5.0, 8.94]
	worker._sync_entities(light)
	_check(worker._instances[0] == resource_instance, "resource-token pose updates must reuse generated resources")
	_check(worker._instances[0].position == Vector3(4.0, 6.0, -5.0), "resource-token pose must reach cached tree")
	worker._sync_entities([])
	_check(worker._resources.is_empty(), "unused resource definitions must be pruned")
	worker._world.free()
	worker.free()


func _tree_pair(seed_value: int) -> Array:
	var trunk := {
		"type": 2,
		"parameters": [2.0, 7.0],
		"vertices": null,
		"triangles": null,
		"visual": {"kind": "tree_trunk", "height": 4.06, "radius": 0.24,
			"seed": seed_value, "lod_near": 120.0, "lod_far": 640.0},
		"color": [0.12, 0.35, 0.08],
		"reflectance": 0.8,
		"position": [0.0, 0.0, 0.0],
		"rotation": _identity_rotation(),
		"object_id": "%d/trunk" % seed_value,
	}
	var canopy := {
		"type": 2,
		"parameters": [2.0, 3.5],
		"vertices": null,
		"triangles": null,
		"visual": {"kind": "tree_canopy", "height": 3.5, "radius": 2.0,
			"seed": seed_value + 1, "lod_near": 120.0, "lod_far": 640.0},
		"color": [0.12, 0.35, 0.08],
		"reflectance": 0.9,
		"position": [0.0, 0.0, 2.94],
		"rotation": _identity_rotation(),
		"object_id": "%d/canopy" % seed_value,
	}
	return [trunk, canopy]


func _identity_rotation() -> Array:
	return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


func _tilt_rotation(angle: float) -> Array:
	var cosine := cos(angle)
	var sine := sin(angle)
	return [1.0, 0.0, 0.0, 0.0, cosine, -sine, 0.0, sine, cosine]


func _check(condition: bool, message: String) -> void:
	if not condition:
		failures.append(message)
