extends Node

## Minimal Godot 4 worker for the virtual-scene image path.
##
## Python sends a framed JSON scene snapshot over a loopback TCP connection.
## This worker rasterizes a deterministic RGB8 frame and returns a framed
## binary response. The protocol is deliberately independent of the scene
## logic so richer Godot materials and scenes can be added incrementally.

var _server := TCPServer.new()
var _peer: StreamPeerTCP
var _buffer := PackedByteArray()
var _expected_payload := -1
var _port := 0
var _token := ""
var _request_count := 0


func _ready() -> void:
	_parse_arguments()
	var error := _server.listen(_port, "127.0.0.1")
	if error != OK:
		push_error("Godot renderer could not listen on port %d: %s" % [_port, error_string(error)])
		get_tree().quit(2)
		return
	print("READY %s" % _token)


func _process(_delta: float) -> void:
	if _peer == null:
		if _server.is_connection_available():
			_peer = _server.take_connection()
			_peer.set_no_delay(true)
			_peer.put_data(("READY %s\n" % _token).to_utf8_buffer())
		return
	_peer.poll()
	if _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		_peer = null
		_buffer = PackedByteArray()
		_expected_payload = -1
		return
	var available := _peer.get_available_bytes()
	if available > 0:
		var packet = _peer.get_data(available)
		if packet[0] != OK:
			_close_peer()
			return
		_buffer.append_array(packet[1])
	_process_packets()


func _parse_arguments() -> void:
	var args := OS.get_cmdline_user_args()
	var index := 0
	while index < args.size():
		var arg: String = args[index]
		if arg == "--port" and index + 1 < args.size():
			_port = int(args[index + 1])
			index += 2
		elif arg == "--token" and index + 1 < args.size():
			_token = str(args[index + 1])
			index += 2
		else:
			index += 1
	if _port <= 0:
		push_error("Godot renderer requires --port")
		get_tree().quit(2)


func _process_packets() -> void:
	while true:
		if _expected_payload < 0:
			if _buffer.size() < 4:
				return
			_expected_payload = _read_u32(_buffer, 0)
			_buffer = _buffer.slice(4)
			if _expected_payload < 2 or _expected_payload > 256 * 1024 * 1024:
				push_error("invalid Godot render request length")
				_close_peer()
				return
		if _buffer.size() < _expected_payload:
			return
		var payload := _buffer.slice(0, _expected_payload)
		_buffer = _buffer.slice(_expected_payload)
		_expected_payload = -1
		_handle_request(payload)


func _handle_request(payload: PackedByteArray) -> void:
	var request = JSON.parse_string(payload.get_string_from_utf8())
	if not request is Dictionary:
		push_error("Godot renderer received a non-object request")
		_close_peer()
		return
	var started := Time.get_ticks_usec()
	var pixels := _render(request)
	var elapsed_ns := (Time.get_ticks_usec() - started) * 1000
	var response := PackedByteArray()
	_append_u64(response, pixels.size())
	_append_u64(response, elapsed_ns)
	_peer.put_data(response)
	_peer.put_data(pixels)
	_request_count += 1


func _render(request: Dictionary) -> PackedByteArray:
	var width := int(request.get("width", 0))
	var height := int(request.get("height", 0))
	if width <= 0 or height <= 0 or width * height > 64 * 1024 * 1024:
		push_error("invalid Godot render dimensions")
		return PackedByteArray()
	var sky := _color_from_array(request.get("sky", [0.1, 0.15, 0.25]))
	var image := Image.create(width, height, false, Image.FORMAT_RGB8)
	image.fill(sky)
	# A simple ground band gives every valid request a visible frame while the
	# scene/material library is being expanded in later milestones.
	var ground := _color_from_array([0.18, 0.24, 0.16])
	for y in range(int(height * 0.68), height):
		for x in range(width):
			image.set_pixel(x, y, ground)
	for entity in request.get("entities", []):
		if entity is Dictionary:
			_draw_entity(image, request, entity)
	return image.get_data()


func _draw_entity(image: Image, request: Dictionary, entity: Dictionary) -> void:
	var position := _vector3(entity.get("position", [0.0, 0.0, 0.0]))
	var camera_point := _to_camera(position, request)
	if camera_point.z <= 0.001:
		return
	var focal: Array = request.get("focal", [1.0, 1.0, 0.0, 0.0])
	var center := Vector2(
		float(focal[0]) * camera_point.x / camera_point.z + float(focal[2]),
		float(focal[1]) * camera_point.y / camera_point.z + float(focal[3])
	)
	var radius := _entity_radius(entity) * 0.5 * (float(focal[0]) + float(focal[1])) / camera_point.z
	var pixel_radius := clampi(int(ceil(radius)), 1, 512)
	var color := _color_from_array(entity.get("color", [0.7, 0.7, 0.7]))
	var reflectance := clampf(float(entity.get("reflectance", 1.0)), 0.0, 1.0)
	color = color * reflectance
	var left := maxi(0, int(floor(center.x)) - pixel_radius)
	var right := mini(image.get_width() - 1, int(floor(center.x)) + pixel_radius)
	var top := maxi(0, int(floor(center.y)) - pixel_radius)
	var bottom := mini(image.get_height() - 1, int(floor(center.y)) + pixel_radius)
	var type := int(entity.get("type", 0))
	for y in range(top, bottom + 1):
		for x in range(left, right + 1):
			var dx := float(x) - center.x
			var dy := float(y) - center.y
			var inside := absf(dx) <= pixel_radius and absf(dy) <= pixel_radius
			if type == 0:
				inside = dx * dx + dy * dy <= float(pixel_radius * pixel_radius)
			if inside:
				image.set_pixel(x, y, color)


func _entity_radius(entity: Dictionary) -> float:
	var parameters: Array = entity.get("parameters", [])
	var type := int(entity.get("type", 0))
	if type == 0 and parameters.size() > 0:
		return maxf(0.1, float(parameters[0]))
	if type == 1:
		var radius := 0.5
		for value in parameters:
			radius = maxf(radius, float(value))
		return radius
	var radius := 0.5
	var vertices: Array = entity.get("vertices", [])
	for vertex in vertices:
		if vertex is Array and vertex.size() >= 3:
			radius = maxf(radius, _vector3(vertex).length())
	return radius


func _to_camera(point: Vector3, request: Dictionary) -> Vector3:
	var origin := _vector3(request.get("origin", [0.0, 0.0, 0.0]))
	var delta := point - origin
	var rotation: Array = request.get("rotation", [])
	if rotation.size() < 9:
		return delta
	return Vector3(
		delta.x * float(rotation[0]) + delta.y * float(rotation[3]) + delta.z * float(rotation[6]),
		delta.x * float(rotation[1]) + delta.y * float(rotation[4]) + delta.z * float(rotation[7]),
		delta.x * float(rotation[2]) + delta.y * float(rotation[5]) + delta.z * float(rotation[8])
	)


func _vector3(value: Variant) -> Vector3:
	var values: Array = value if value is Array else [0.0, 0.0, 0.0]
	return Vector3(
		float(values[0]) if values.size() > 0 else 0.0,
		float(values[1]) if values.size() > 1 else 0.0,
		float(values[2]) if values.size() > 2 else 0.0
	)


func _color_from_array(value: Variant) -> Color:
	var values: Array = value if value is Array else [0.0, 0.0, 0.0]
	return Color(
		clampf(float(values[0]) if values.size() > 0 else 0.0, 0.0, 1.0),
		clampf(float(values[1]) if values.size() > 1 else 0.0, 0.0, 1.0),
		clampf(float(values[2]) if values.size() > 2 else 0.0, 0.0, 1.0)
	)


func _read_u32(data: PackedByteArray, offset: int) -> int:
	return int(data[offset]) | (int(data[offset + 1]) << 8) | (int(data[offset + 2]) << 16) | (int(data[offset + 3]) << 24)


func _append_u64(data: PackedByteArray, value: int) -> void:
	var remaining := value
	for _index in range(8):
		data.append(remaining & 0xff)
		remaining = remaining >> 8


func _close_peer() -> void:
	if _peer != null:
		_peer.disconnect_from_host()
	_peer = null
	_buffer = PackedByteArray()
	_expected_payload = -1
