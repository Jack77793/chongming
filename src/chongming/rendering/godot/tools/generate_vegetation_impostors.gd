extends SceneTree
## Offline helper: render one transparent tree billboard per vendored variant.
## Run with: godot --path src/chongming/rendering/godot \
##   --script tools/generate_vegetation_impostors.gd \
##   --rendering-method gl_compatibility --audio-driver Dummy
const ASSET_PATH := "res://assets/shapespark_low_poly_plants.gltf"
const OUTPUT_DIR := "res://assets/impostors"
const NAMES := [
	"Tree-01-1", "Tree-01-2", "Tree-01-3", "Tree-01-4",
	"Tree-02-1", "Tree-02-2", "Tree-02-3", "Tree-02-4",
	"Tree-03-1", "Tree-03-2", "Tree-03-3", "Tree-03-4",
]
func _initialize() -> void:
	_run.call_deferred()

func _run() -> void:
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(OUTPUT_DIR))
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	var error := document.append_from_file(ProjectSettings.globalize_path(ASSET_PATH), state)
	if error != OK:
		push_error("load failed")
		quit(1); return
	var scene := document.generate_scene(state)
	var meshes: Dictionary = {}
	var pending: Array[Node] = [scene]
	while not pending.is_empty():
		var node: Node = pending.pop_back()
		if node is MeshInstance3D and str(node.name) in NAMES:
			meshes[str(node.name)] = (node as MeshInstance3D).mesh
		for child in node.get_children(): pending.append(child)
	for name in NAMES:
		var mesh: ArrayMesh = meshes[name]
		for i in range(mesh.get_surface_count()):
			var mat = mesh.surface_get_material(i)
			if mat is BaseMaterial3D:
				mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
				mat.alpha_scissor_threshold = 0.5
				mat.cull_mode = BaseMaterial3D.CULL_DISABLED
		var vp := SubViewport.new()
		vp.size = Vector2i(256, 512)
		vp.transparent_bg = true
		vp.own_world_3d = true
		vp.render_target_update_mode = SubViewport.UPDATE_ONCE
		root.add_child(vp)
		var env := Environment.new()
		env.background_mode = Environment.BG_CLEAR_COLOR
		env.background_color = Color(0, 0, 0, 0)
		env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
		env.ambient_light_color = Color(0.82, 0.86, 0.92)
		env.ambient_light_energy = 0.85
		var we := WorldEnvironment.new(); we.environment = env; vp.add_child(we)
		var sun := DirectionalLight3D.new(); sun.rotation_degrees = Vector3(-45, -30, 0); vp.add_child(sun)
		var mi := MeshInstance3D.new(); mi.mesh = mesh; vp.add_child(mi)
		var box := mesh.get_aabb()
		var wide := maxf(box.size.x, box.size.z)
		var ortho := maxf(box.size.y, wide * 2.25) * 1.12
		var cam := Camera3D.new(); vp.add_child(cam)
		cam.projection = Camera3D.PROJECTION_ORTHOGONAL
		cam.size = ortho
		cam.look_at_from_position(Vector3(0, box.size.y * 0.5, maxf(wide, box.size.y) * 2.0), Vector3(0, box.size.y * 0.5, 0), Vector3.UP)
		cam.current = true
		for i in range(4): await process_frame
		RenderingServer.force_draw(true)
		var image := vp.get_texture().get_image()
		var used := image.get_used_rect()
		if used.size.x > 0 and used.size.y > 0:
			var pad := 2
			used = used.grow(pad).intersection(Rect2i(Vector2i.ZERO, image.get_size()))
			image = image.get_region(used)
		image.save_png("%s/%s.png" % [ProjectSettings.globalize_path(OUTPUT_DIR), name])
		print(name, " -> ", image.get_size())
		vp.queue_free()
		await process_frame
	quit(0)
