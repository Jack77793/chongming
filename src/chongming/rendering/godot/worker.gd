extends Node

## Python owns simulation time; Godot owns procedural visuals. Render only on a snapshot request.
var _server := TCPServer.new()
var _peer: StreamPeerTCP
var _buffer := PackedByteArray()
var _expected_payload := -1
var _port := 0
var _token := ""
var _viewport: SubViewport
var _world: Node3D
var _camera: Camera3D
var _environment: Environment
var _sun: DirectionalLight3D
var _ground: MeshInstance3D
var _instances: Array[MeshInstance3D] = []
var _signatures: Array[String] = []
var _busy := false


func _ready() -> void:
	DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_MINIMIZED)
	get_viewport().disable_3d = true
	seed(42)
	_parse_arguments()
	_setup_world()
	var error := _server.listen(_port, "127.0.0.1")
	if error != OK:
		push_error("Godot renderer could not listen: %s" % error_string(error))
		get_tree().quit(2)


func _setup_world() -> void:
	_viewport = SubViewport.new()
	_viewport.own_world_3d = true
	_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	_viewport.transparent_bg = false
	add_child(_viewport)
	_world = Node3D.new()
	_viewport.add_child(_world)
	_camera = Camera3D.new()
	_camera.current = true
	_world.add_child(_camera)
	_environment = Environment.new()
	_environment.background_mode = Environment.BG_COLOR
	_environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	_environment.ambient_light_color = Color.WHITE
	_environment.ambient_light_energy = 0.6
	_environment.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	var environment_node := WorldEnvironment.new()
	environment_node.environment = _environment
	_world.add_child(environment_node)
	_sun = DirectionalLight3D.new()
	_sun.shadow_enabled = true
	_world.add_child(_sun)
	_ground = MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(200000.0, 200000.0)
	_ground.mesh = plane
	_world.add_child(_ground)


func _process(_delta: float) -> void:
	if _peer == null:
		if _server.is_connection_available():
			_peer = _server.take_connection()
			_peer.set_no_delay(true)
			_peer.put_data(("READY %s\n" % _token).to_utf8_buffer())
		return
	_peer.poll()
	if _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		get_tree().quit()
		return
	var available := _peer.get_available_bytes()
	if available > 0:
		var packet := _peer.get_data(available)
		if packet[0] != OK:
			get_tree().quit(2)
			return
		_buffer.append_array(packet[1])
	if not _busy:
		_process_packets()


func _parse_arguments() -> void:
	var args := OS.get_cmdline_user_args()
	for index in range(args.size() - 1):
		if args[index] == "--port":
			_port = int(args[index + 1])
		elif args[index] == "--token":
			_token = args[index + 1]
	if _port <= 0:
		push_error("Godot renderer requires --port")
		get_tree().quit(2)


func _process_packets() -> void:
	if _expected_payload < 0:
		if _buffer.size() < 4:
			return
		_expected_payload = _buffer.decode_u32(0)
		_buffer = _buffer.slice(4)
		if _expected_payload < 2 or _expected_payload > 256 * 1024 * 1024:
			get_tree().quit(2)
			return
	if _buffer.size() < _expected_payload:
		return
	var payload := _buffer.slice(0, _expected_payload)
	_buffer = _buffer.slice(_expected_payload)
	_expected_payload = -1
	var request = JSON.parse_string(payload.get_string_from_utf8())
	if not request is Dictionary:
		get_tree().quit(2)
		return
	_busy = true
	var started := Time.get_ticks_usec()
	var pixels := await _render(request)
	var response := PackedByteArray()
	response.resize(16)
	response.encode_u64(0, pixels.size())
	response.encode_u64(8, (Time.get_ticks_usec() - started) * 1000)
	_peer.put_data(response)
	_peer.put_data(pixels)
	_busy = false


func _render(request: Dictionary) -> PackedByteArray:
	var width := int(request.get("width", 0))
	var height := int(request.get("height", 0))
	if width <= 0 or height <= 0 or width * height > 64 * 1024 * 1024:
		return PackedByteArray()
	_viewport.size = Vector2i(width, height)
	# ENU -> Godot: (east, up, -north), a proper rotation. Optical axes
	# right/down/forward -> Camera3D right/up/back by flipping y and z.
	_camera.transform = Transform3D(
		_basis(request["rotation"]) * Basis(Vector3.RIGHT, Vector3.DOWN, Vector3.FORWARD),
		_enu(request["origin"])
	)
	var focal: Array = request["focal"]
	var near := 0.02
	var offset := Vector2(
		(width * 0.5 - float(focal[2]) - 0.5) * near / float(focal[0]),
		(float(focal[3]) + 0.5 - height * 0.5) * near / float(focal[1])
	)
	_camera.set_frustum(height * near / float(focal[1]), offset, near, 100000.0)
	_environment.background_color = _color(request["sky"])
	# Artistic baseline: unit directional energy represents full daylight.
	# This is not a calibrated photometric camera/exposure model.
	_sun.light_energy = float(request["illuminance"]) / 100000.0
	var sun_direction := _enu(request["sun"]).normalized()
	_sun.basis = Basis.looking_at(-sun_direction, Vector3.FORWARD if absf(sun_direction.y) > 0.99 else Vector3.UP)
	_ground.position = Vector3(_camera.position.x, float(request["terrainElevation"]), _camera.position.z)
	_ground.material_override = _material(request["terrainColor"], float(request["terrainReflectance"]))
	var entities: Array = request["entities"]
	while _instances.size() > entities.size():
		_instances.pop_back().free()
		_signatures.pop_back()
	for index in range(entities.size()):
		var entity: Dictionary = entities[index]
		if index == _instances.size():
			var new_instance := MeshInstance3D.new()
			_world.add_child(new_instance)
			_instances.append(new_instance)
			_signatures.append("")
		var instance := _instances[index]
		var signature := JSON.stringify([entity.type, entity.parameters, entity.vertices, entity.triangles, entity.visual, entity.color, entity.reflectance])
		if signature != _signatures[index]:
			instance.mesh = _mesh(entity)
			instance.material_override = _material(entity.color, float(entity.reflectance))
			_signatures[index] = signature
		instance.transform = Transform3D(_basis(entity.rotation), _enu(entity.position))
	# No GPU frames between requests, even while the experiment is paused.
	# Flush SceneTree transform/material notifications before drawing; otherwise
	# the first image can contain the previous snapshot or an empty viewport.
	await get_tree().process_frame
	_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
	RenderingServer.force_draw(false)
	var image := _viewport.get_texture().get_image()
	image.convert(Image.FORMAT_RGB8)
	return image.get_data()


func _mesh(entity: Dictionary) -> Mesh:
	var visual: Dictionary = {}
	var visual_value: Variant = entity.get("visual", null)
	if visual_value is Dictionary:
		visual = visual_value
	if visual.get("kind", "") == "tree_trunk":
		return _tree_trunk_mesh(float(visual.get("height", 1.0)), float(visual.get("radius", 0.2)))
	if visual.get("kind", "") == "tree_canopy":
		return _tree_canopy_mesh(float(visual.get("height", 1.0)), float(visual.get("radius", 1.0)))
	var parameters: Array = entity.parameters
	if int(entity.type) == 0:
		var mesh := SphereMesh.new()
		mesh.radius = float(parameters[0])
		mesh.height = 2.0 * mesh.radius
		return mesh
	if int(entity.type) == 1:
		var mesh := BoxMesh.new()
		mesh.size = 2.0 * _vector(parameters)
		return mesh
	var surface := SurfaceTool.new()
	surface.begin(Mesh.PRIMITIVE_TRIANGLES)
	for triangle in entity.triangles:
		for vertex in triangle:
			surface.add_vertex(_vector(entity.vertices[int(vertex)]))
	surface.generate_normals()
	return surface.commit()


func _tree_trunk_mesh(height: float, radius: float) -> Mesh:
	var surface := SurfaceTool.new()
	surface.begin(Mesh.PRIMITIVE_TRIANGLES)
	var trunk_radius: float = maxf(radius, 0.05)
	_add_cylinder_between(surface, Vector3(0.0, 0.0, 0.0), Vector3(0.0, 0.0, height), trunk_radius, trunk_radius * 0.48, 10)
	# A few tapered branches make the silhouette read as a tree at low frame rates.
	for index in range(5):
		var angle := TAU * float(index) / 5.0 + 0.35
		var branch_height := height * (0.42 + 0.09 * float(index % 3))
		var branch_start := Vector3(0.0, 0.0, branch_height)
		var branch_end := Vector3(cos(angle) * radius * 2.3, sin(angle) * radius * 2.3, branch_height + height * 0.14)
		_add_cylinder_between(surface, branch_start, branch_end, trunk_radius * 0.28, trunk_radius * 0.08, 7)
	surface.generate_normals()
	return surface.commit()


func _tree_canopy_mesh(height: float, radius: float) -> Mesh:
	var surface := SurfaceTool.new()
	surface.begin(Mesh.PRIMITIVE_TRIANGLES)
	# Overlapping low-poly ellipsoids provide a clustered canopy rather than cones.
	var centers := [
		Vector3(0.0, 0.0, height * 0.14),
		Vector3(-radius * 0.38, radius * 0.08, height * 0.32),
		Vector3(radius * 0.34, -radius * 0.12, height * 0.38),
		Vector3(-radius * 0.12, -radius * 0.34, height * 0.54),
		Vector3(radius * 0.16, radius * 0.28, height * 0.62),
		Vector3(0.0, 0.0, height * 0.82),
	]
	var scales := [
		Vector3(radius * 0.72, radius * 0.67, height * 0.25),
		Vector3(radius * 0.55, radius * 0.48, height * 0.24),
		Vector3(radius * 0.58, radius * 0.52, height * 0.25),
		Vector3(radius * 0.48, radius * 0.43, height * 0.22),
		Vector3(radius * 0.46, radius * 0.42, height * 0.24),
		Vector3(radius * 0.38, radius * 0.36, height * 0.28),
	]
	for index in range(centers.size()):
		_add_ellipsoid(surface, centers[index], scales[index], 5, 8)
	surface.generate_normals()
	return surface.commit()


func _add_ellipsoid(surface: SurfaceTool, center: Vector3, radii: Vector3, rings: int, segments: int) -> void:
	for ring in range(rings):
		var phi0 := PI * float(ring) / float(rings)
		var phi1 := PI * float(ring + 1) / float(rings)
		for segment in range(segments):
			var theta0 := TAU * float(segment) / float(segments)
			var theta1 := TAU * float(segment + 1) / float(segments)
			var a := center + Vector3(sin(phi0) * cos(theta0) * radii.x, sin(phi0) * sin(theta0) * radii.y, cos(phi0) * radii.z)
			var b := center + Vector3(sin(phi0) * cos(theta1) * radii.x, sin(phi0) * sin(theta1) * radii.y, cos(phi0) * radii.z)
			var c := center + Vector3(sin(phi1) * cos(theta1) * radii.x, sin(phi1) * sin(theta1) * radii.y, cos(phi1) * radii.z)
			var d := center + Vector3(sin(phi1) * cos(theta0) * radii.x, sin(phi1) * sin(theta0) * radii.y, cos(phi1) * radii.z)
			surface.add_triangle_fan(PackedVector3Array([a, b, c]))
			surface.add_triangle_fan(PackedVector3Array([a, c, d]))


func _add_cylinder_between(surface: SurfaceTool, start: Vector3, finish: Vector3, start_radius: float, finish_radius: float, segments: int) -> void:
	var direction := finish - start
	var length := direction.length()
	if length <= 0.001:
		return
	var axis := direction / length
	var helper := Vector3.UP if absf(axis.y) < 0.9 else Vector3.RIGHT
	var side := axis.cross(helper).normalized()
	var other := axis.cross(side).normalized()
	for index in range(segments):
		var angle0 := TAU * float(index) / float(segments)
		var angle1 := TAU * float(index + 1) / float(segments)
		var radial0 := side * cos(angle0) + other * sin(angle0)
		var radial1 := side * cos(angle1) + other * sin(angle1)
		surface.add_triangle_fan(PackedVector3Array([
			start + radial0 * start_radius,
			start + radial1 * start_radius,
			finish + radial1 * finish_radius,
		]))
		surface.add_triangle_fan(PackedVector3Array([
			start + radial0 * start_radius,
			finish + radial1 * finish_radius,
			finish + radial0 * finish_radius,
		]))


func _material(rgb: Array, reflectance: float) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = _color([float(rgb[0]) * reflectance, float(rgb[1]) * reflectance, float(rgb[2]) * reflectance])
	material.roughness = 0.9
	material.metallic_specular = 0.0
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	return material


func _vector(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2]))


func _enu(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[2]), -float(value[1]))


func _basis(rotation: Array) -> Basis:
	return Basis(
		_enu([rotation[0], rotation[3], rotation[6]]),
		_enu([rotation[1], rotation[4], rotation[7]]),
		_enu([rotation[2], rotation[5], rotation[8]])
	)


func _color(rgb: Array) -> Color:
	# Linear protocol reflectance -> sRGB API colors -> sRGB RGB8 viewport.
	return Color(float(rgb[0]), float(rgb[1]), float(rgb[2])).linear_to_srgb()
