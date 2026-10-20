extends Node

## Python owns simulation time; Godot owns procedural visuals. Render only on a snapshot request.
const Vegetation = preload("res://vegetation.gd")
const AtmosphereShader = preload("res://atmosphere.gdshader")
const OpticsShader = preload("res://optics.gdshader")
const TerrainShader = preload("res://terrain.gdshader")
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
var _sky_material: ShaderMaterial
var _optics_material: ShaderMaterial
var _output_viewport: SubViewport
var _output_rect: ColorRect
var _ground: MeshInstance3D
var _ground_material: ShaderMaterial
var _instances: Array[Node3D] = []
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
	_sky_material = ShaderMaterial.new()
	_sky_material.shader = AtmosphereShader
	var sky := Sky.new()
	sky.process_mode = Sky.PROCESS_MODE_REALTIME
	sky.sky_material = _sky_material
	_environment.sky = sky
	var environment_node := WorldEnvironment.new()
	environment_node.environment = _environment
	_world.add_child(environment_node)
	_sun = DirectionalLight3D.new()
	_sun.shadow_enabled = true
	_world.add_child(_sun)
	# A second, request-only viewport applies inexpensive optical degradation.
	# Compatibility has no HDR glow/auto-exposure, so use an explicit shader.
	_output_viewport = SubViewport.new()
	_output_viewport.disable_3d = true
	_output_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	add_child(_output_viewport)
	_output_rect = ColorRect.new()
	_optics_material = ShaderMaterial.new()
	_optics_material.shader = OpticsShader
	_optics_material.set_shader_parameter("scene_texture", _viewport.get_texture())
	_output_rect.material = _optics_material
	_output_viewport.add_child(_output_rect)
	_ground = MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(200000.0, 200000.0)
	_ground.mesh = plane
	_ground_material = ShaderMaterial.new()
	_ground_material.shader = TerrainShader
	_ground.material_override = _ground_material
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
	var sky_color := _color(request["sky"])
	var visibility_variant: Variant = request.get("atmosphericVisibility", null)
	var sun_diameter := float(request.get("sunDiameter", 0.0))
	var cloud_layers: Array = request.get("clouds", [])
	_environment.background_mode = Environment.BG_SKY if visibility_variant != null or sun_diameter > 0.0 or not cloud_layers.is_empty() else Environment.BG_COLOR
	_sky_material.set_shader_parameter("sky_tint", Vector3(float(request.sky[0]), float(request.sky[1]), float(request.sky[2])))
	_sky_material.set_shader_parameter("sun_direction", _enu(request["sun"]).normalized())
	_sky_material.set_shader_parameter("sun_radius", deg_to_rad(sun_diameter * 0.5))
	_sky_material.set_shader_parameter("sun_energy", maxf(0.0, float(request.illuminance) / 100000.0))
	_sky_material.set_shader_parameter("cloud_count", mini(cloud_layers.size(), 2))
	_sky_material.set_shader_parameter("camera_horizontal", Vector2(_camera.position.x, _camera.position.z))
	_sky_material.set_shader_parameter("camera_altitude", _camera.position.y)
	_sky_material.set_shader_parameter("cloud_time", float(request.get("cloudTime", 0.0)))
	for layer_index in range(2):
		var cloud := Vector4.ZERO
		if layer_index < cloud_layers.size():
			var layer: Dictionary = cloud_layers[layer_index]
			var drift: Array = layer.get("drift", [0.0, 0.0, 0.0])
			cloud = Vector4(float(layer.get("altitude", 0.0)), float(layer.get("density", 0.0)),
				float(drift[0]), -float(drift[1]))
		_sky_material.set_shader_parameter("cloud_layer_%d" % layer_index, cloud)
	_environment.fog_enabled = visibility_variant != null
	if visibility_variant != null:
		var visibility := maxf(1.0, float(visibility_variant))
		_environment.fog_light_color = sky_color.lightened(0.08)
		_environment.fog_light_energy = 0.7
		# Koschmieder's 2% contrast distance. Clear-air haze only; no ray cutoff.
		_environment.fog_density = 3.912 / visibility
		_environment.fog_height = 0.0
		_environment.fog_height_density = 0.0
		_environment.fog_sky_affect = 0.0
	var ssao_intensity := float(request.get("ssaoIntensity", 0.0))
	_environment.ssao_enabled = ssao_intensity > 0.0
	_environment.ssao_radius = 1.25
	_environment.ssao_intensity = ssao_intensity
	# Artistic baseline: unit directional energy represents full daylight.
	# This is not a calibrated photometric camera/exposure model.
	var cloud_cover := 0.0
	for layer in cloud_layers:
		cloud_cover = 1.0 - (1.0 - cloud_cover) * (1.0 - float(layer.get("density", 0.0)))
	_sun.light_energy = float(request["illuminance"]) / 100000.0 * (1.0 - cloud_cover * 0.32)
	var sun_direction := _enu(request["sun"]).normalized()
	_sun.basis = Basis.looking_at(-sun_direction, Vector3.FORWARD if absf(sun_direction.y) > 0.99 else Vector3.UP)
	_ground.position = Vector3(_camera.position.x, float(request["terrainElevation"]), _camera.position.z)
	_ground_material.set_shader_parameter("base_color", Vector3(
		float(request.terrainColor[0]) * float(request.terrainReflectance),
		float(request.terrainColor[1]) * float(request.terrainReflectance),
		float(request.terrainColor[2]) * float(request.terrainReflectance)))
	_ground_material.set_shader_parameter("detail_strength", float(request.get("terrainDetail", 0.0)))
	_sync_entities(request["entities"])
	# No GPU frames between requests, even while the experiment is paused.
	# Flush SceneTree transform/material notifications before drawing; otherwise
	# the first image can contain the previous snapshot or an empty viewport.
	await get_tree().process_frame
	_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
	RenderingServer.force_draw(false)
	var output := _viewport
	var glare := float(request.get("glareStrength", 0.0))
	var god_rays := float(request.get("godRaysStrength", 0.0))
	var fxaa := bool(request.get("fxaaEnabled", false))
	if glare > 0.0 or god_rays > 0.0 or fxaa:
		_output_viewport.size = Vector2i(width, height)
		_output_rect.size = Vector2(width, height)
		_optics_material.set_shader_parameter("strength", glare)
		_optics_material.set_shader_parameter("pixel_size", Vector2(1.0 / width, 1.0 / height))
		var local_sun := _camera.basis.inverse() * sun_direction
		var sun_uv := Vector2(-10.0, -10.0)
		var sun_radius_uv := Vector2.ZERO
		if local_sun.z < -0.0001 and sun_direction.y > 0.0 and sun_diameter > 0.0:
			sun_uv = Vector2(
				(float(focal[0]) * local_sun.x / -local_sun.z + float(focal[2]) + 0.5) / width,
				(-float(focal[1]) * local_sun.y / -local_sun.z + float(focal[3]) + 0.5) / height
			)
			sun_radius_uv = Vector2(float(focal[0]) / width, float(focal[1]) / height) * tan(deg_to_rad(sun_diameter * 0.5)) / -local_sun.z
		_optics_material.set_shader_parameter("sun_uv", sun_uv)
		_optics_material.set_shader_parameter("sun_radius_uv", sun_radius_uv)
		_optics_material.set_shader_parameter("focal_uv", Vector2(float(focal[0]) / width, float(focal[1]) / height))
		_optics_material.set_shader_parameter("god_rays_strength", god_rays)
		_optics_material.set_shader_parameter("fxaa_enabled", fxaa)
		await get_tree().process_frame
		_output_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
		RenderingServer.force_draw(false)
		output = _output_viewport
	var image := output.get_texture().get_image()
	image.convert(Image.FORMAT_RGB8)
	return image.get_data()


func _sync_entities(entities: Array) -> void:
	while _instances.size() > entities.size():
		_instances.pop_back().free()
		_signatures.pop_back()
	for index in range(entities.size()):
		var entity: Dictionary = entities[index]
		if index == _instances.size():
			_instances.append(Node3D.new())
			_signatures.append("")
		var signature := JSON.stringify([entity.type, entity.parameters, entity.vertices, entity.triangles, entity.visual, entity.color, entity.reflectance, entity.get("surface", null), entity.get("surface_detail", 0.0)])
		if signature != _signatures[index]:
			var old_instance := _instances[index]
			old_instance.free()
			var visual_value: Variant = entity.get("visual", null)
			var new_instance: Node3D
			if visual_value is Dictionary and str(visual_value.get("kind", "")) in ["tree_trunk", "tree_canopy", "shrub", "grass"]:
				new_instance = Vegetation.create(visual_value, entity.color, float(entity.reflectance))
			else:
				var mesh_instance := MeshInstance3D.new()
				mesh_instance.mesh = _mesh(entity)
				if str(entity.get("surface", "")) == "terrain":
					mesh_instance.material_override = _terrain_material(entity.color, float(entity.reflectance), float(entity.get("surface_detail", 0.0)))
				else:
					mesh_instance.material_override = _material(entity.color, float(entity.reflectance))
				new_instance = mesh_instance
			_world.add_child(new_instance)
			_instances[index] = new_instance
			_signatures[index] = signature
		var instance := _instances[index]
		instance.transform = Transform3D(_basis(entity.rotation), _enu(entity.position))


func _mesh(entity: Dictionary) -> Mesh:
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


func _material(rgb: Array, reflectance: float) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = _color([float(rgb[0]) * reflectance, float(rgb[1]) * reflectance, float(rgb[2]) * reflectance])
	material.roughness = 0.9
	material.metallic_specular = 0.0
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	return material


func _terrain_material(rgb: Array, reflectance: float, detail: float) -> ShaderMaterial:
	var material := ShaderMaterial.new()
	material.shader = TerrainShader
	material.set_shader_parameter("base_color", Vector3(
		float(rgb[0]) * reflectance, float(rgb[1]) * reflectance, float(rgb[2]) * reflectance))
	material.set_shader_parameter("detail_strength", detail)
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
