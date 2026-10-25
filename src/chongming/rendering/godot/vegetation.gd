extends RefCounted
## Render-only vegetation built from the vendored CC0 low-poly plant kit.
##
## Python still owns vegetation truth, placement and occlusion proxies.  This
## script only turns the existing semantic payloads into shared asset meshes and
## per-instance nodes; it owns no clock and no global RNG.

const Assets = preload("res://vegetation_assets.gd")
const DEFAULT_LOD_NEAR := 140.0
const DEFAULT_LOD_FAR := 750.0
const MAX_GRASS_CLUMPS := 256


static func create(visual: Dictionary, rgb: Array, reflectance: float) -> Node3D:
	match str(visual.get("kind", "")):
		"tree", "tree_trunk":
			return _create_tree(visual, rgb, reflectance)
		"tree_canopy":
			# The tree is rendered on the trunk entity.  The canopy entity still
			# exists in the Python truth stream, but its visual is carried by the
			# paired trunk and receives a crown-only wind deformation there.
			var anchor := Node3D.new()
			anchor.name = "TreeCanopyAnchor"
			return anchor
		"shrub":
			return _create_shrub(visual, rgb, reflectance)
		"grass":
			return _create_grass(visual, rgb, reflectance)
	return _fallback(visual, rgb, reflectance)


static func _create_tree(visual: Dictionary, rgb: Array, reflectance: float) -> Node3D:
	var seed := int(visual.get("seed", 0))
	var prototype: Dictionary = Assets.tree(seed)
	if prototype.is_empty():
		return _fallback(visual, rgb, reflectance)
	var height := maxf(0.1, float(visual.get("height", 10.0)))
	var radius := maxf(0.05, float(visual.get("radius", 2.0)))
	var native_height := maxf(0.1, float(prototype.native_height))
	var native_radius := maxf(0.05, float(prototype.native_radius))
	var scale_y := height / native_height
	# Preserve the authored card proportions rather than stretching a narrow
	# semantic crown into a tall column.  Very narrow scene proxies still get a
	# plausible visual crown; the logical occlusion proxy remains unchanged.
	var scale_xz := maxf(radius / native_radius, scale_y * 0.82)
	var root := Node3D.new()
	root.name = "VegetationTree"
	root.set_meta("variant", str(prototype.name))
	var model := Node3D.new()
	model.name = "Model"
	# The vendored kit is Y-up; the scene protocol and worker are Z-up (body).
	model.rotation.x = PI * 0.5
	model.scale = Vector3(scale_xz, scale_y, scale_xz)
	root.add_child(model)
	var trunk := MeshInstance3D.new()
	trunk.name = "Trunk"
	trunk.mesh = prototype.trunk
	trunk.visibility_range_end = _lod_far(visual)
	trunk.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	model.add_child(trunk)
	var crown := MeshInstance3D.new()
	crown.name = "Crown"
	crown.mesh = prototype.crown
	crown.visibility_range_end = _lod_far(visual)
	crown.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	crown.set_meta("native_height", native_height)
	model.add_child(crown)
	return root


static func _create_shrub(visual: Dictionary, rgb: Array, reflectance: float) -> Node3D:
	var prototype: Dictionary = Assets.shrub(int(visual.get("seed", 0)))
	if prototype.is_empty():
		return _fallback(visual, rgb, reflectance)
	var height := maxf(0.05, float(visual.get("height", 1.0)))
	var radius := maxf(0.05, float(visual.get("radius", 1.0)))
	var native_height := maxf(0.1, float(prototype.native_height))
	var native_radius := maxf(0.05, float(prototype.native_radius))
	var root := Node3D.new()
	root.name = "VegetationShrub"
	root.set_meta("variant", str(prototype.name))
	var model := Node3D.new()
	model.name = "Model"
	model.rotation.x = PI * 0.5
	model.scale = Vector3(radius / native_radius, height / native_height, radius / native_radius)
	root.add_child(model)
	var foliage := MeshInstance3D.new()
	foliage.name = "Foliage"
	foliage.mesh = prototype.mesh
	foliage.visibility_range_end = _lod_far(visual)
	foliage.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	model.add_child(foliage)
	return root


static func _create_grass(visual: Dictionary, rgb: Array, reflectance: float) -> Node3D:
	var prototype: Dictionary = Assets.grass(int(visual.get("seed", 0)))
	if prototype.is_empty():
		return _fallback(visual, rgb, reflectance)
	var width := maxf(0.1, float(visual.get("width", 1.0)))
	var depth := maxf(0.1, float(visual.get("depth", 1.0)))
	var height := maxf(0.02, float(visual.get("height", 0.35)))
	var density := maxf(0.1, float(visual.get("density", 1.0)))
	var root := Node3D.new()
	root.name = "VegetationGrassPatch"
	root.set_meta("variant", str(prototype.name))
	var spacing := clampf(3.0 / sqrt(density), 1.0, 2.8)
	var columns := maxi(1, ceili(width / spacing))
	var rows := maxi(1, ceili(depth / spacing))
	var count := mini(MAX_GRASS_CLUMPS, columns * rows)
	var rng := RandomNumberGenerator.new()
	rng.seed = 4242 ^ int(visual.get("seed", 0))
	var clump_width := spacing * 1.20
	var native_width := maxf(0.1, float(prototype.native_width))
	var native_height := maxf(0.05, float(prototype.native_height))
	var scale_xz := clump_width / native_width
	var scale_y := height / native_height
	var clumps := MultiMesh.new()
	clumps.transform_format = MultiMesh.TRANSFORM_3D
	clumps.use_colors = true
	clumps.mesh = prototype.mesh
	clumps.instance_count = count
	var cell_width := width / float(columns)
	var cell_depth := depth / float(rows)
	for index in range(count):
		var column := index % columns
		var row := index / columns
		var x := -width * 0.5 + (float(column) + 0.5) * cell_width
		var z := -depth * 0.5 + (float(row) + 0.5) * cell_depth
		x += rng.randf_range(-0.35, 0.35) * cell_width
		z += rng.randf_range(-0.35, 0.35) * cell_depth
		var scale := scale_xz * rng.randf_range(0.82, 1.16)
		var basis := Basis(Vector3.RIGHT, PI * 0.5) * Basis(
			Vector3.UP, rng.randf_range(0.0, TAU)
		).scaled(Vector3(scale, scale_y * rng.randf_range(0.85, 1.15), scale))
		clumps.set_instance_transform(index, Transform3D(basis, Vector3(x, z, 0.0)))
		var tint := rng.randf_range(0.82, 1.06)
		clumps.set_instance_color(
			index,
			Color(tint * rng.randf_range(0.92, 1.04), tint, tint * rng.randf_range(0.82, 1.0))
		)
	var half_width := clump_width * 0.5
	clumps.custom_aabb = AABB(
		Vector3(-width * 0.5 - half_width, -depth * 0.5 - half_width, 0.0),
		Vector3(width + clump_width, depth + clump_width, height * 1.15)
	)
	var patch := MultiMeshInstance3D.new()
	patch.name = "Clumps"
	patch.multimesh = clumps
	patch.visibility_range_end = _lod_near(visual)
	patch.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	root.add_child(patch)
	return root


static func _fallback(visual: Dictionary, rgb: Array, reflectance: float) -> Node3D:
	# Keep the renderer alive if the vendored asset is absent or malformed.  The
	# production scenarios always have the asset, so this is a safety net only.
	var root := Node3D.new()
	root.name = "VegetationFallback"
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(
		float(rgb[0]) * reflectance, float(rgb[1]) * reflectance, float(rgb[2]) * reflectance
	).linear_to_srgb()
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.roughness = 1.0
	var kind := str(visual.get("kind", ""))
	if kind == "grass":
		var blades := MeshInstance3D.new()
		var quad := QuadMesh.new()
		quad.size = Vector2(float(visual.get("width", 1.0)), float(visual.get("height", 0.3)))
		blades.mesh = quad
		blades.material_override = material
		root.add_child(blades)
	else:
		var height := float(visual.get("height", 1.0))
		var radius := float(visual.get("radius", 1.0))
		var mesh := MeshInstance3D.new()
		var cylinder := CylinderMesh.new()
		cylinder.height = height
		cylinder.top_radius = radius * 0.15
		cylinder.bottom_radius = radius * 0.18
		mesh.mesh = cylinder
		mesh.position.y = height * 0.5
		mesh.material_override = material
		root.add_child(mesh)
	return root


static func _lod_near(visual: Dictionary) -> float:
	return float(visual.get("lod_near", DEFAULT_LOD_NEAR))


static func _lod_far(visual: Dictionary) -> float:
	return float(visual.get("lod_far", DEFAULT_LOD_FAR))
