extends RefCounted
## Runtime cache for the vendored CC0 Shapespark low-poly plant kit.
##
## The upstream GLTF is self-contained (embedded buffer and textures), so it can
## be loaded with GLTFDocument without an editor import pass.  Every scene keeps
## sharing the same mesh and material resources; variation is per instance.

const ASSET_PATH := "res://assets/shapespark_low_poly_plants.gltf"
const IMPOSTOR_DIR := "res://assets/impostors"

const TREE_NAMES := [
	"Tree-01-1", "Tree-01-2", "Tree-01-3", "Tree-01-4",
	"Tree-02-1", "Tree-02-2", "Tree-02-3", "Tree-02-4",
	"Tree-03-1", "Tree-03-2", "Tree-03-3", "Tree-03-4",
]
const SHRUB_NAMES := ["Bush-01", "Bush-02", "Bush-03", "Bush-04", "Bush-05", "Hedge-01"]
const GRASS_NAMES := [
	"Grass-01", "Grass-02", "Grass-03",
	"Clover-01", "Clover-02", "Clover-03", "Clover-04", "Clover-05",
	"Flowers-01", "Flowers-02", "Flowers-03", "Flowers-04",
]

static var _loaded := false
static var _trees: Array[Dictionary] = []
static var _shrubs: Array[Dictionary] = []
static var _grasses: Array[Dictionary] = []
static var _impostor_meshes: Array[ArrayMesh] = []
static var _impostor_materials: Array[Material] = []


static func tree(seed: int) -> Dictionary:
	_ensure_loaded()
	return _pick(_trees, seed)


static func shrub(seed: int) -> Dictionary:
	_ensure_loaded()
	return _pick(_shrubs, seed)


static func grass(seed: int) -> Dictionary:
	_ensure_loaded()
	return _pick(_grasses, seed)


static func tree_count() -> int:
	_ensure_loaded()
	return _trees.size()


static func shrub_count() -> int:
	_ensure_loaded()
	return _shrubs.size()


static func grass_count() -> int:
	_ensure_loaded()
	return _grasses.size()


static func tree_at(index: int) -> Dictionary:
	_ensure_loaded()
	if _trees.is_empty():
		return {}
	return _trees[clampi(index, 0, _trees.size() - 1)]


static func shrub_at(index: int) -> Dictionary:
	_ensure_loaded()
	if _shrubs.is_empty():
		return {}
	return _shrubs[clampi(index, 0, _shrubs.size() - 1)]


static func grass_at(index: int) -> Dictionary:
	_ensure_loaded()
	if _grasses.is_empty():
		return {}
	return _grasses[clampi(index, 0, _grasses.size() - 1)]


static func tree_impostor_mesh(index: int) -> ArrayMesh:
	_ensure_loaded()
	if index < 0 or index >= _impostor_meshes.size():
		return null
	return _impostor_meshes[index]


static func tree_impostor_material(index: int) -> Material:
	_ensure_loaded()
	if index < 0 or index >= _impostor_materials.size():
		return null
	return _impostor_materials[index]


static func tree_low(index: int) -> Dictionary:
	# The kit has four variants per tree type; the fourth is the lightest
	# silhouette and is used only below the far LOD threshold.
	_ensure_loaded()
	if _trees.is_empty():
		return {}
	var family_start := (clampi(index, 0, _trees.size() - 1) / 4) * 4
	return _trees[mini(family_start + 3, _trees.size() - 1)]


static func _pick(items: Array[Dictionary], seed: int) -> Dictionary:
	if items.is_empty():
		return {}
	return items[posmod(seed, items.size())]


static func _ensure_loaded() -> void:
	if _loaded:
		return
	_loaded = true
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	var error := document.append_from_file(
		ProjectSettings.globalize_path(ASSET_PATH), state
	)
	if error != OK:
		push_error("could not load vendored plant asset: %s" % error_string(error))
		return
	var scene := document.generate_scene(state)
	if scene == null:
		push_error("vendored plant asset produced no scene")
		return
	var meshes: Dictionary = {}
	var pending: Array[Node] = [scene]
	while not pending.is_empty():
		var node: Node = pending.pop_back()
		if node is MeshInstance3D:
			var instance := node as MeshInstance3D
			if instance.mesh is ArrayMesh:
				meshes[str(node.name)] = instance.mesh
		for child in node.get_children():
			pending.append(child)
	scene.free()
	for index in range(TREE_NAMES.size()):
		var name: String = TREE_NAMES[index]
		var mesh: ArrayMesh = meshes.get(name)
		if mesh != null:
			_trees.append(_tree_prototype(name, mesh, index))
	for name in SHRUB_NAMES:
		var mesh: ArrayMesh = meshes.get(name)
		if mesh != null:
			_shrubs.append(_foliage_prototype(name, mesh))
	for name in GRASS_NAMES:
		var mesh: ArrayMesh = meshes.get(name)
		if mesh != null:
			_grasses.append(_foliage_prototype(name, mesh))
	_load_impostors()
	if _trees.is_empty() or _shrubs.is_empty() or _grasses.is_empty():
		push_error("vendored plant asset is missing tree, shrub or grass nodes")


static func _load_impostors() -> void:
	for name in TREE_NAMES:
		var path := "%s/%s.png" % [IMPOSTOR_DIR, name]
		var image := Image.load_from_file(ProjectSettings.globalize_path(path))
		if image == null:
			_impostor_meshes.append(null)
			_impostor_materials.append(null)
			continue
		if image.is_compressed():
			image.decompress()
		var texture := ImageTexture.create_from_image(image)
		var aspect := float(texture.get_width()) / maxf(1.0, float(texture.get_height()))
		var material := StandardMaterial3D.new()
		material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		material.albedo_texture = texture
		material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
		material.alpha_scissor_threshold = 0.5
		material.cull_mode = BaseMaterial3D.CULL_DISABLED
		material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
		var mesh := _crossed_billboard(aspect)
		_impostor_meshes.append(mesh)
		_impostor_materials.append(material)


static func _crossed_billboard(aspect: float) -> ArrayMesh:
	var half := maxf(0.05, aspect * 0.5)
	var surface := SurfaceTool.new()
	surface.begin(Mesh.PRIMITIVE_TRIANGLES)
	var corners_a := [
		Vector3(-half, 0.0, 0.0), Vector3(half, 0.0, 0.0),
		Vector3(half, 0.0, 1.0), Vector3(-half, 0.0, 1.0),
	]
	var corners_b := [
		Vector3(0.0, -half, 0.0), Vector3(0.0, half, 0.0),
		Vector3(0.0, half, 1.0), Vector3(0.0, -half, 1.0),
	]
	var uvs := [Vector2(0.0, 1.0), Vector2(1.0, 1.0), Vector2(1.0, 0.0), Vector2(0.0, 0.0)]
	for corners in [corners_a, corners_b]:
		for order in [[0, 1, 2], [0, 2, 3]]:
			for vertex_index in order:
				surface.set_uv(uvs[vertex_index])
				surface.add_vertex(corners[vertex_index])
	surface.generate_normals()
	return surface.commit()


static func _tree_prototype(name: String, mesh: ArrayMesh, variant_index: int) -> Dictionary:
	var crown_indices: Array[int] = []
	for index in range(1, mesh.get_surface_count()):
		crown_indices.append(index)
	var trunk := _subset(mesh, [0], false)
	var crown := _subset(mesh, crown_indices, true)
	var bounds := mesh.get_aabb()
	return {
		"name": name,
		"mesh": mesh,
		"trunk": trunk,
		"crown": crown,
		"family": variant_index / 4,
		"variant": variant_index % 4,
		"native_height": maxf(0.1, bounds.size.y),
		"native_radius": maxf(0.05, maxf(bounds.size.x, bounds.size.z) * 0.5),
	}


static func _foliage_prototype(name: String, mesh: ArrayMesh) -> Dictionary:
	for index in range(mesh.get_surface_count()):
		mesh.surface_set_material(
			index, _prepare_material(mesh.surface_get_material(index), true, true)
		)
	var bounds := mesh.get_aabb()
	return {
		"name": name,
		"mesh": mesh,
		"native_height": maxf(0.1, bounds.size.y),
		"native_radius": maxf(0.05, maxf(bounds.size.x, bounds.size.z) * 0.5),
		"native_width": maxf(0.1, maxf(bounds.size.x, bounds.size.z)),
	}


static func _subset(mesh: ArrayMesh, indices: Array, foliage: bool) -> ArrayMesh:
	var result := ArrayMesh.new()
	for index in indices:
		var arrays: Array = mesh.surface_get_arrays(index)
		result.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
		var material := _prepare_material(mesh.surface_get_material(index), foliage, false)
		result.surface_set_material(result.get_surface_count() - 1, material)
	return result


static func _prepare_material(material: Material, foliage: bool, vertex_color: bool) -> Material:
	if not material is BaseMaterial3D:
		return material
	var base := material as BaseMaterial3D
	# The upstream GLTF uses alpha blending.  Alpha scissor keeps the card
	# silhouette while avoiding transparent sorting and expensive overdraw.
	if foliage:
		base.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
		base.alpha_scissor_threshold = 0.5
	else:
		base.transparency = BaseMaterial3D.TRANSPARENCY_DISABLED
	base.cull_mode = BaseMaterial3D.CULL_DISABLED
	base.roughness = 0.9
	base.metallic_specular = 0.1
	base.vertex_color_use_as_albedo = vertex_color
	base.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
	return base
