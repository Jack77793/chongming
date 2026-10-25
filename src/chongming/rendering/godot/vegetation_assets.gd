extends RefCounted
## Runtime cache for the vendored CC0 Shapespark low-poly plant kit.
##
## The upstream GLTF is self-contained (embedded buffer and textures), so it can
## be loaded with GLTFDocument without an editor import pass.  Every scene keeps
## sharing the same mesh and material resources; variation is per instance.

const ASSET_PATH := "res://assets/shapespark_low_poly_plants.gltf"

const TREE_NAMES := [
	"Tree-01-1", "Tree-01-2", "Tree-01-3", "Tree-01-4",
	"Tree-02-1", "Tree-02-2", "Tree-02-3", "Tree-02-4",
	"Tree-03-1", "Tree-03-2", "Tree-03-3", "Tree-03-4",
]
const SHRUB_NAMES := ["Bush-01", "Bush-02", "Bush-03", "Bush-04", "Bush-05"]
const GRASS_NAMES := ["Grass-01", "Grass-02", "Grass-03"]

static var _loaded := false
static var _trees: Array[Dictionary] = []
static var _shrubs: Array[Dictionary] = []
static var _grasses: Array[Dictionary] = []


static func tree(seed: int) -> Dictionary:
	_ensure_loaded()
	return _pick(_trees, seed)


static func shrub(seed: int) -> Dictionary:
	_ensure_loaded()
	return _pick(_shrubs, seed)


static func grass(seed: int) -> Dictionary:
	_ensure_loaded()
	return _pick(_grasses, seed)


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
	for name in TREE_NAMES:
		var mesh: ArrayMesh = meshes.get(name)
		if mesh != null:
			_trees.append(_tree_prototype(name, mesh))
	for name in SHRUB_NAMES:
		var mesh: ArrayMesh = meshes.get(name)
		if mesh != null:
			_shrubs.append(_foliage_prototype(name, mesh))
	for name in GRASS_NAMES:
		var mesh: ArrayMesh = meshes.get(name)
		if mesh != null:
			_grasses.append(_foliage_prototype(name, mesh))
	if _trees.is_empty() or _shrubs.is_empty() or _grasses.is_empty():
		push_error("vendored plant asset is missing tree, shrub or grass nodes")


static func _tree_prototype(name: String, mesh: ArrayMesh) -> Dictionary:
	var crown_indices: Array[int] = []
	for index in range(1, mesh.get_surface_count()):
		crown_indices.append(index)
	var trunk := _subset(mesh, [0], false)
	var crown := _subset(mesh, crown_indices, true)
	var bounds := mesh.get_aabb()
	return {
		"name": name,
		"trunk": trunk,
		"crown": crown,
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
