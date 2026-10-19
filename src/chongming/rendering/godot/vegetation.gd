extends RefCounted
## Render-only broadleaf tree generator. Coordinates are body-space Z-up.
## No SceneTree clock, global RNG, Python vertices, or external assets are used.

static var _leaf_prototype: ArrayMesh
const BOUGHS := 18
const TWIGS := 6
const LEAVES_PER_TWIG := 24


static func create(visual: Dictionary, rgb: Array, reflectance: float) -> Node3D:
	var root := Node3D.new()
	root.name = "ProceduralTree"
	var rng := RandomNumberGenerator.new()
	rng.seed = 42 ^ int(visual.get("seed", 0))
	var height := float(visual.height)
	var radius := float(visual.radius)
	var wood := SurfaceTool.new()
	wood.begin(Mesh.PRIMITIVE_TRIANGLES)
	if visual.kind == "tree_trunk":
		_trunk(wood, height, radius, rng)
	else:
		_canopy(root, wood, height, radius, rgb, reflectance, rng)
	var bark := MeshInstance3D.new()
	bark.name = "Branches"
	bark.mesh = wood.commit()
	bark.material_override = _wood_material()
	root.add_child(bark)
	return root


static func _trunk(wood: SurfaceTool, height: float, radius: float, rng: RandomNumberGenerator) -> void:
	var previous := Vector3.ZERO
	for section in range(7):
		var fraction := float(section + 1) / 7.0
		var next := Vector3(sin(fraction * 2.0) * radius * 0.3, sin(fraction * 3.0) * radius * 0.2, height * fraction)
		_tube(wood, previous, next, radius * (1.0 - float(section) / 7.0 * 0.55), radius * (1.0 - fraction * 0.55), 14)
		previous = next
	# Buttress roots taper into the ground rather than ending in a flat pole.
	for index in range(6):
		var angle := TAU * float(index) / 6.0 + rng.randf_range(-0.2, 0.2)
		_tube(wood, Vector3(0.0, 0.0, radius * 1.8), Vector3(cos(angle), sin(angle), 0.0) * radius * 2.7, radius * 0.45, radius * 0.07, 7)


static func _canopy(root: Node3D, wood: SurfaceTool, height: float, radius: float, rgb: Array, reflectance: float, rng: RandomNumberGenerator) -> void:
	_tube(wood, Vector3.ZERO, Vector3(radius * 0.08, 0.0, height * 0.87), radius * 0.065, radius * 0.008, 10)
	var leaves := MultiMesh.new()
	leaves.transform_format = MultiMesh.TRANSFORM_3D
	leaves.use_colors = true
	leaves.mesh = leaf_mesh()
	leaves.instance_count = BOUGHS * TWIGS * LEAVES_PER_TWIG
	var leaf_bounds := leaves.mesh.get_aabb()
	var bounds := AABB()
	var leaf_index := 0
	var phase := rng.randf_range(0.0, TAU)
	for index in range(BOUGHS):
		var level := (float(index) + 0.5) / float(BOUGHS)
		var angle := phase + float(index) * 2.399963 + rng.randf_range(-0.22, 0.22)
		var spread := radius * sqrt(maxf(0.1, 1.0 - pow((level - 0.42) / 0.76, 2.0)))
		var start := Vector3(0.0, 0.0, height * (0.05 + level * 0.64))
		var bend := Vector3(cos(angle), sin(angle), 0.0) * spread * 0.45 + start + Vector3(0.0, 0.0, height * 0.08)
		var end := Vector3(cos(angle) * spread, sin(angle) * spread, height * (0.19 + level * 0.69))
		var thickness := radius * (0.027 - level * 0.016)
		_tube(wood, start, bend, thickness, thickness * 0.55, 8)
		_tube(wood, bend, end, thickness * 0.55, thickness * 0.12, 7)
		for twig in range(TWIGS):
			var along := 0.25 + float(twig) / float(TWIGS) * 0.73
			var twig_start := bend.lerp(end, along)
			var twig_angle := angle + (-1.0 if twig % 2 == 0 else 1.0) * rng.randf_range(0.5, 1.3)
			var twig_end := twig_start + Vector3(cos(twig_angle) * radius * 0.28, sin(twig_angle) * radius * 0.28, height * rng.randf_range(0.03, 0.13))
			_tube(wood, twig_start, twig_end, thickness * 0.22, thickness * 0.035, 5)
			for leaf in range(LEAVES_PER_TWIG):
				# Leaf sprays around each twig, with gaps between sprays. No solid crown.
				var position := twig_start.lerp(twig_end, rng.randf_range(0.15, 1.15))
				position += Vector3(rng.randfn(0.0, radius * 0.11), rng.randfn(0.0, radius * 0.11), rng.randfn(0.0, height * 0.065))
				var size := radius * rng.randf_range(0.075, 0.13)
				var rotation := Basis.from_euler(Vector3(rng.randf_range(-1.2, 1.2), rng.randf_range(-1.2, 1.2), rng.randf_range(0.0, TAU)))
				var leaf_transform := Transform3D(rotation.scaled(Vector3(size * rng.randf_range(0.8, 1.15), size, size)), position)
				leaves.set_instance_transform(leaf_index, leaf_transform)
				bounds = bounds.merge(leaf_transform * leaf_bounds)
				var shade := rng.randf_range(0.58, 1.05)
				leaves.set_instance_color(leaf_index, Color(shade * rng.randf_range(0.83, 1.1), shade, shade * rng.randf_range(0.7, 1.0)))
				leaf_index += 1
	# Bounds are fixed in body space; pose-only updates must not scan the instance buffer.
	leaves.custom_aabb = bounds
	var foliage := MultiMeshInstance3D.new()
	foliage.name = "Leaves"
	foliage.multimesh = leaves
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(float(rgb[0]) * reflectance * 0.5, float(rgb[1]) * reflectance * 0.5, float(rgb[2]) * reflectance * 0.5).linear_to_srgb()
	material.vertex_color_use_as_albedo = true
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.roughness = 0.85
	material.metallic_specular = 0.15
	material.backlight_enabled = true
	material.backlight = Color(0.12, 0.18, 0.05)
	foliage.material_override = material
	root.add_child(foliage)


static func leaf_mesh() -> ArrayMesh:
	if _leaf_prototype != null:
		return _leaf_prototype
	# A folded, tapered blade with a raised midrib: 8 opaque triangles, no alpha overdraw.
	var surface := SurfaceTool.new()
	surface.begin(Mesh.PRIMITIVE_TRIANGLES)
	var centers := [Vector3.ZERO, Vector3(0.0, 0.07, 0.35), Vector3(0.0, 0.04, 0.72), Vector3(0.0, 0.0, 1.0)]
	for side in [-1.0, 1.0]:
		var edges := [Vector3.ZERO, Vector3(side * 0.24, -0.02, 0.35), Vector3(side * 0.18, -0.04, 0.72), Vector3(0.0, 0.0, 1.0)]
		for segment in range(3):
			if segment > 0:
				_triangle(surface, centers[segment], edges[segment], centers[segment + 1])
			if segment < 2:
				_triangle(surface, edges[segment], edges[segment + 1], centers[segment + 1])
	surface.generate_normals()
	_leaf_prototype = surface.commit()
	return _leaf_prototype


static func _triangle(surface: SurfaceTool, a: Vector3, b: Vector3, c: Vector3) -> void:
	surface.add_vertex(a)
	surface.add_vertex(b)
	surface.add_vertex(c)


static func _tube(surface: SurfaceTool, start: Vector3, end: Vector3, start_radius: float, end_radius: float, sides: int) -> void:
	var axis := (end - start).normalized()
	var side := axis.cross(Vector3.UP if absf(axis.y) < 0.9 else Vector3.RIGHT).normalized()
	var other := axis.cross(side)
	for index in range(sides):
		var a := TAU * float(index) / float(sides)
		var b := TAU * float(index + 1) / float(sides)
		var n0 := side * cos(a) + other * sin(a)
		var n1 := side * cos(b) + other * sin(b)
		# Radial fluting and per-strip color give bark relief without a texture.
		var groove0 := 1.0 + 0.08 * cos(float(index) * 2.4)
		var groove1 := 1.0 + 0.08 * cos(float(index + 1) * 2.4)
		var a0 := start + n0 * start_radius * groove0
		var a1 := start + n1 * start_radius * groove1
		var b0 := end + n0 * end_radius * groove0
		var b1 := end + n1 * end_radius * groove1
		var shade := 0.7 + 0.3 * groove0
		surface.set_color(Color(shade, shade, shade))
		for vertex in [a0, a1, b1, a0, b1, b0]:
			surface.set_normal(n0 if vertex == a0 or vertex == b0 else n1)
			surface.add_vertex(vertex)


static func _wood_material() -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.095, 0.065, 0.038).linear_to_srgb()
	material.vertex_color_use_as_albedo = true
	material.roughness = 1.0
	material.metallic_specular = 0.0
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	return material
