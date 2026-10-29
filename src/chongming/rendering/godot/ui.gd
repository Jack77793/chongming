extends Control

## Preview window. Shows the latest state Python pushed and turns user input
## into commands for the next UI exchange; it owns no experiment or edit state.

const PANEL_WIDTH := 440
const STATE_TEXT := {"playing": "播放中", "paused": "已暂停", "failed": "生成失败"}
const EDIT_FIELDS := [
	["E", "位置 E (m)"], ["N", "位置 N (m)"], ["U", "位置 U (m)"],
	["yaw", "主相机偏航角 (°)"], ["elevation", "主相机俯仰角 (°)"],
	["vE", "速度 E (m/s)"], ["vN", "速度 N (m/s)"], ["vU", "速度 U (m/s)"],
]


class ImageView extends Control:
	## A sensor image scaled to fit, with truth boxes drawn over it.
	var texture: Texture2D
	var marks: Array = []
	var overlay := true

	func _draw() -> void:
		if texture == null:
			return
		var pixels := texture.get_size()
		var factor := minf(size.x / pixels.x, size.y / pixels.y)
		var origin := (size - pixels * factor) * 0.5
		draw_texture_rect(texture, Rect2(origin, pixels * factor), false)
		if not overlay:
			return
		var font := get_theme_default_font()
		for mark in marks:
			var box: Rect2 = mark.box
			var color: Color = mark.color
			draw_rect(Rect2(origin + box.position * factor, box.size * factor), color, false, 2.0)
			draw_string(font, origin + Vector2(box.end.x, box.get_center().y) * factor + Vector2(4.0, 5.0),
				str(mark.label), HORIZONTAL_ALIGNMENT_LEFT, -1, 14, color)


var worker: Node
var _state: Dictionary = {}
var _frame: Dictionary = {}
var _textures: Dictionary = {}
var _freeview_shown := false
var _log_seq := -1
var _edit_loaded := ""
var _edit_pending := ""
var _play: Button
var _step: Button
var _restart: Button
var _seek_time: LineEdit
var _seek: Button
var _seek_hint: Label
var _station: OptionButton
var _overlay: CheckBox
var _freeview: Button
var _status: Label
var _views: TabContainer
var _main_view: ImageView
var _aux_view: ImageView
var _main_caption: Label
var _aux_caption: Label
var _freeview_hint: Label
var _panel: TabContainer
var _info: TextEdit
var _log: TextEdit
var _edit_begin: Button
var _edit_cancel: Button
var _edit_commit: Button
var _edit_state: Label
var _edit_object: OptionButton
var _edit_new_id: LineEdit
var _edit_add_station: Button
var _edit_add_target: Button
var _edit_delete: Button
var _edit_landscape: OptionButton
var _edit_fields: Dictionary = {}
var _edit_labels: Dictionary = {}
var _edit_apply: Button
var _edit_hint: Label
var _visualization_layers: Dictionary = {}
var _station_states: TextEdit


## Build the window around the worker's freeview display, which moves into a tab.
func build(freeview: TextureRect, freeview_fps: Label) -> void:
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var background := Panel.new()
	background.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(background)
	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left", "top", "right", "bottom"]:
		margin.add_theme_constant_override("margin_" + side, 8)
	add_child(margin)
	var column := VBoxContainer.new()
	margin.add_child(column)
	column.add_child(_toolbar())
	var split := HSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	column.add_child(split)
	_views = TabContainer.new()
	_views.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	split.add_child(_views)
	_views.add_child(_sensor_tab())
	_views.add_child(_freeview_tab(freeview, freeview_fps))
	_views.set_tab_title(0, "传感器画面")
	_views.set_tab_title(1, "自由视角")
	_panel = TabContainer.new()
	_panel.custom_minimum_size.x = PANEL_WIDTH
	split.add_child(_panel)
	_info = _text_view()
	_panel.add_child(_info)
	_panel.add_child(_editor_tab())
	_log = _text_view()
	_panel.add_child(_log)
	_panel.add_child(_visualization_tab())
	_panel.set_tab_title(3, "算法图层")
	_panel.set_tab_title(0, "信息")
	_panel.set_tab_title(1, "编辑")
	_panel.set_tab_title(2, "日志")
	_panel.tab_changed.connect(func(tab: int) -> void:
		if tab == 2:
			_panel.set_tab_title(2, "日志"))


func _button(text: String, pressed: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.pressed.connect(pressed)
	return button


func _toolbar() -> HBoxContainer:
	var bar := HBoxContainer.new()
	_play = _button("暂停", func() -> void:
		var playing := str(_state.get("playback", {}).get("state", "")) == "playing"
		_emit({"type": "control", "action": "pause" if playing else "play"}))
	_step = _button("单步", func() -> void: _emit({"type": "control", "action": "step"}))
	_restart = _button("回到起点", func() -> void: _emit({"type": "control", "action": "restart"}))
	_seek_time = LineEdit.new()
	_seek_time.placeholder_text = "时间 (s)"
	_seek_time.custom_minimum_size.x = 90
	_seek_time.text_submitted.connect(func(_text: String) -> void: _request_seek())
	_seek = _button("跳转", _request_seek)
	_seek_hint = Label.new()
	_station = OptionButton.new()
	_station.item_selected.connect(func(index: int) -> void:
		_emit({"type": "select_station", "station": _station.get_item_text(index)}))
	_overlay = CheckBox.new()
	_overlay.text = "真值叠加"
	_overlay.button_pressed = true
	_overlay.toggled.connect(func(_on: bool) -> void: _show_images())
	_freeview = _button("打开自由视角", func() -> void:
		_emit({"type": "freeview", "enabled": not bool(_state.get("editor", {}).get("freeview", false))}))
	_status = Label.new()
	_status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_status.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_status.clip_text = true
	for control in [_play, _step, _restart, VSeparator.new(), _seek_time, _seek, _seek_hint,
			VSeparator.new(), _station, _overlay, _freeview, _status]:
		bar.add_child(control)
	return bar


func _sensor_tab() -> VBoxContainer:
	var box := VBoxContainer.new()
	_main_caption = Label.new()
	_main_view = ImageView.new()
	_main_view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_main_view.size_flags_stretch_ratio = 2.0
	_aux_caption = Label.new()
	_aux_view = ImageView.new()
	_aux_view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	for control in [_main_caption, _main_view, _aux_caption, _aux_view]:
		box.add_child(control)
	return box


func _freeview_tab(freeview: TextureRect, freeview_fps: Label) -> Control:
	var box := Control.new()
	freeview.reparent(box, false)
	freeview.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	freeview_fps.reparent(box, false)
	_freeview_hint = Label.new()
	_freeview_hint.text = "自由视角未打开。右键拖动调整视线，按住右键时 WASD 平移、Q/E 升降、Shift 加速。"
	_freeview_hint.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_freeview_hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_freeview_hint.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_freeview_hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_freeview_hint)
	return box


func _visualization_tab() -> VBoxContainer:
	var box := VBoxContainer.new()
	for pair in [["truth", "真值：绿色圆圈、速度及视场连线"], ["rays", "边缘：黄色射线及独立角度 σ 圆锥"],
			["measurements", "中心：红色测量位置及尺寸圆"], ["tracks", "轨迹：历史及当前位置"]]:
		var check := CheckBox.new()
		var key: String = pair[0]
		check.text = pair[1]
		check.button_pressed = true
		check.toggled.connect(func(enabled: bool) -> void: worker.ui_set_visualization_layer(key, enabled))
		_visualization_layers[key] = check
		box.add_child(check)
	var legend := Label.new()
	legend.text = "仅自由视角，不写入传感器像素。\n射线：VALID 实线 / FROZEN 虚线 / STALE 点线\n轨迹：暂定浅红 / 确认深红 / 滑行橙 / 删除灰\n射线保留 1 s，测量及删除轨迹保留 3 s（实验时间）。\n轨迹保留最近 100 点；速度箭头为 1 s 位移。\n测量圆直径 = max(σE, σN, σU)，仅尺寸示意。\n未知 σ 不绘制；角度 σ ≥ π/2 无法绘制圆锥。\n\n站点姿态差：配置相机坐标系（右 / 下 / 前），单位 rad。"
	legend.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(legend)
	_station_states = _text_view()
	_station_states.size_flags_vertical = Control.SIZE_EXPAND_FILL
	box.add_child(_station_states)
	return box


func _show_station_states(stations: Dictionary) -> void:
	var lines := []
	for station in stations.values():
		lines.append("%s · %s\n  实际相对配置：%s\n  校准相对配置：%s" % [station.station_id,
			"未知" if station.calibration_usage == null else str(station.calibration_usage).to_upper(),
			_vector(station.truth_orientation_error_rad, 9), _vector(station.calibration_orientation_error_rad, 9)])
	_set_text(_station_states, "尚无站点状态" if lines.is_empty() else "\n\n".join(lines))


func _text_view() -> TextEdit:
	var view := TextEdit.new()
	view.editable = false
	view.context_menu_enabled = true
	view.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	return view


func _editor_tab() -> ScrollContainer:
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	var box := VBoxContainer.new()
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(box)
	var session := HBoxContainer.new()
	_edit_begin = _button("进入编辑", func() -> void: _emit({"type": "edit_begin"}))
	_edit_cancel = _button("撤销", func() -> void: _emit({"type": "edit_cancel"}))
	_edit_commit = _button("提交 JSON", func() -> void: _emit({"type": "edit_commit"}))
	_edit_state = Label.new()
	for control in [_edit_begin, _edit_cancel, _edit_commit, _edit_state]:
		session.add_child(control)
	box.add_child(session)
	_edit_object = OptionButton.new()
	_edit_object.item_selected.connect(func(_index: int) -> void: _load_selection())
	box.add_child(_labeled("对象", _edit_object))
	var create := HBoxContainer.new()
	_edit_new_id = LineEdit.new()
	_edit_new_id.placeholder_text = "新对象 ID"
	_edit_new_id.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_edit_add_station = _button("添加相机站", func() -> void: _create("station"))
	_edit_add_target = _button("添加飞行物", func() -> void: _create("target"))
	for control in [_edit_new_id, _edit_add_station, _edit_add_target]:
		create.add_child(control)
	box.add_child(create)
	var confirm := ConfirmationDialog.new()
	confirm.dialog_text = "删除所选对象？"
	confirm.confirmed.connect(func() -> void:
		var selected := _selected()
		if not selected.is_empty():
			_emit({"type": "edit_delete", "kind": selected[0], "id": selected[1]}))
	add_child(confirm)
	_edit_delete = _button("删除所选", func() -> void: confirm.popup_centered())
	box.add_child(_edit_delete)
	_edit_landscape = OptionButton.new()
	_edit_landscape.item_selected.connect(func(index: int) -> void:
		if index > 0:
			_emit({"type": "edit_landscape", "landscape_id": _edit_landscape.get_item_text(index)}))
	box.add_child(_labeled("地景", _edit_landscape))
	box.add_child(HSeparator.new())
	for field in EDIT_FIELDS:
		var edit := LineEdit.new()
		_edit_fields[field[0]] = edit
		_edit_labels[field[0]] = _labeled(field[1], edit)
		box.add_child(_edit_labels[field[0]])
	_edit_apply = _button("应用修改", _apply_edit)
	box.add_child(_edit_apply)
	_edit_hint = Label.new()
	_edit_hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_edit_hint)
	return scroll


func _labeled(text: String, control: Control) -> HBoxContainer:
	var row := HBoxContainer.new()
	var label := Label.new()
	label.text = text
	label.custom_minimum_size.x = 150
	control.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(label)
	row.add_child(control)
	return row


func _emit(command: Dictionary) -> void:
	worker.ui_emit(command)


func _request_seek() -> void:
	var text := _seek_time.text.strip_edges()
	if not text.is_valid_float():
		_seek_hint.text = "请输入秒数"
		return
	_seek_hint.text = ""
	_emit({"type": "seek", "time_s": text.to_float()})


func _create(kind: String) -> void:
	var object_id := _edit_new_id.text.strip_edges()
	if object_id.is_empty():
		_edit_hint.text = "先填写新对象 ID（ASCII 字母、数字、_、-）"
		return
	_edit_pending = kind + ":" + object_id
	_edit_new_id.text = ""
	_emit({"type": "edit_create", "kind": kind, "id": object_id})


## [kind, id] of the selected edit object, or [] when nothing is selected.
func _selected() -> Array:
	if _edit_object.selected < 0:
		return []
	return Array(str(_edit_object.get_item_metadata(_edit_object.selected)).split(":", true, 1))


func _selected_item() -> Dictionary:
	var selected := _selected()
	if selected.is_empty():
		return {}
	var key := "stations" if selected[0] == "station" else "targets"
	for item in _document().get(key, []):
		if item is Dictionary and str(item.get("id", "")) == selected[1]:
			return item
	return {}


func _document() -> Dictionary:
	var document = _state.get("editor", {}).get("document")
	return document if document is Dictionary else {}


## The motion field holding a target's editable position.
static func _position_field(motion: Dictionary) -> String:
	return "center_enu_m" if str(motion.get("type", "")) == "orbit" else "position_enu_m"


func _numbers(keys: Array) -> Array:
	var values := []
	for key in keys:
		var text: String = _edit_fields[key].text.strip_edges()
		if not text.is_valid_float() or not is_finite(text.to_float()):
			return []
		values.append(text.to_float())
	return values


func _apply_edit() -> void:
	var selected := _selected()
	var item := _selected_item()
	if item.is_empty():
		return
	var position := _numbers(["E", "N", "U"])
	if position.is_empty():
		_edit_hint.text = "位置必须是有限数值"
		return
	var patch := {}
	if selected[0] == "station":
		var angles := _numbers(["yaw", "elevation"])
		if angles.is_empty():
			_edit_hint.text = "角度必须是有限数值"
			return
		patch = {"position_enu_m": position, "main_yaw_deg": angles[0], "main_elevation_deg": angles[1]}
	else:
		var motion: Dictionary = item.get("motion", {}).duplicate(true)
		motion[_position_field(motion)] = position
		if str(motion.get("type", "")) == "constant_velocity":
			var velocity := _numbers(["vE", "vN", "vU"])
			if velocity.is_empty():
				_edit_hint.text = "速度必须是有限数值"
				return
			motion["velocity_enu_mps"] = velocity
		patch = {"motion": motion}
	_edit_hint.text = ""
	_emit({"type": "edit_update", "kind": selected[0], "id": selected[1], "patch": patch})


## Show the state Python just pushed. Textures are the worker's keyed images,
## updated in place; a state without a frame keeps showing the previous one.
func show_state(state: Dictionary, textures: Dictionary, station_states: Dictionary) -> void:
	_state = state
	_textures = textures
	if state.get("frame") is Dictionary:
		_frame = state.frame
	_show_controls()
	_show_images()
	_set_text(_info, _info_text())
	_show_log()
	_show_editor()
	_show_station_states(station_states)


func _show_controls() -> void:
	var playback: Dictionary = _state.get("playback", {})
	var editor: Dictionary = _state.get("editor", {})
	var state := str(playback.get("state", ""))
	var locked := state == "failed" or bool(editor.get("active", false))
	_play.text = "播放" if state == "paused" else "暂停"
	for button in [_play, _step, _restart]:
		button.disabled = locked
	var seeking := bool(playback.get("seeking", false))
	_seek.disabled = locked or seeking
	_seek_time.placeholder_text = "0–%.1f s" % float(playback.get("duration_s", 0.0))
	if seeking:
		_seek_hint.text = "跳转中…"
	elif _seek_hint.text == "跳转中…":
		_seek_hint.text = ""
	var stations: Array = playback.get("stations", [])
	var names := []
	for index in range(_station.item_count):
		names.append(_station.get_item_text(index))
	if names != stations:
		_station.clear()
		for station in stations:
			_station.add_item(str(station))
	_station.select(stations.find(str(_state.get("station", ""))))
	var freeview := bool(editor.get("freeview", false))
	_freeview.text = "关闭自由视角" if freeview else "打开自由视角"
	_freeview.disabled = bool(editor.get("active", false))
	if freeview != _freeview_shown:
		_freeview_shown = freeview
		_views.current_tab = 1 if freeview else 0
	_freeview_hint.visible = not freeview
	var time_s = playback.get("time_s")
	_status.text = "%s · %s / %.3f s" % [STATE_TEXT.get(state, state),
		"—" if time_s == null else "%.3f" % float(time_s), float(playback.get("duration_s", 0.0))]
	_status.add_theme_color_override("font_color",
		Color(1.0, 0.45, 0.4) if state == "failed" else Color(0.85, 0.9, 0.95))


func _show_images() -> void:
	var station := str(_state.get("station", ""))
	for role in ["main", "auxiliary"]:
		var view: ImageView = _main_view if role == "main" else _aux_view
		var caption: Label = _main_caption if role == "main" else _aux_caption
		var truth := _frame_truth(station, role)
		view.texture = _textures.get(station + "/" + role)
		view.overlay = _overlay.button_pressed
		view.marks = _marks(truth)
		var title := "主相机" if role == "main" else "辅助相机"
		if view.texture == null:
			caption.text = "%s · 尚无画面" % title
		else:
			var pixels: Vector2 = view.texture.get_size()
			caption.text = "%s · %s %d × %d" % [title, station, pixels.x, pixels.y]
			if not truth.is_empty():
				caption.text += " · 曝光起点 %.6f s · %s" % [
					float(truth.capture_time.exposure_start_ns) / 1e9, truth.frame_id]
		view.queue_redraw()


func _frame_truth(station: String, role: String) -> Dictionary:
	for truth in _frame.get("monitor", {}).get("frame_truth", []):
		if str(truth.station_id) == station and str(truth.camera_role) == role:
			return truth
	return {}


func _marks(truth: Dictionary) -> Array:
	var unknown := str(_frame.get("monitor", {}).get("occlusion_mode", "")) == "unknown"
	var marks := []
	for target in truth.get("targets", []):
		var centroid = target.projection.centroid_px
		if centroid == null or not bool(target.projection.center_in_field_of_view):
			continue
		var extent := Vector2(
			float(target.projected_width_px) if target.projected_width_px != null else 8.0,
			float(target.projected_height_px) if target.projected_height_px != null else 8.0)
		var color := Color("#f9d475") if unknown else \
			Color("#67e4ae") if bool(target.visibility.capture_eligible) else Color("#f9a475")
		marks.append({
			"box": Rect2(Vector2(float(centroid[0]), float(centroid[1])) - extent * 0.5 - Vector2(3, 3),
				extent + Vector2(6, 6)),
			"color": color,
			"label": str(target.truth_object_id),
		})
	return marks


static func _stats(value: Variant) -> String:
	if not value is Dictionary or value.get("p50") == null:
		return "—"
	return "%s / %s / %s ms（%d 样本）" % [value["p50"], value["p95"], value["max"], int(value["sample_count"])]


static func _vector(value: Variant, digits := 1) -> String:
	if not value is Array:
		return "—"
	var parts := []
	for item in value:
		parts.append(String.num(float(item), digits))
	return " / ".join(parts)


func _info_text() -> String:
	var playback: Dictionary = _state.get("playback", {})
	var lines := []
	if playback.get("error") != null:
		lines.append("错误：%s" % playback.error)
		lines.append("")
	lines.append("配置：%s · 渲染时钟 %s Hz" % [playback.get("profile", "—"), playback.get("render_clock_fps", "—")])
	lines.append("逻辑 / 渲染 / 跳过步：%s / %s / %s" % [playback.get("logical_step_count", "—"),
		playback.get("rendered_step_count", "—"), playback.get("skipped_step_count", "—")])
	lines.append("生成耗时 p50/p95/max：%s" % _stats(playback.get("generation_stats_ms")))
	lines.append("标定耗时 p50/p95/max：%s" % _stats(playback.get("calibration_stats_ms")))
	if _frame.is_empty():
		lines.append("")
		lines.append("等待第一帧")
		return "\n".join(lines)
	var monitor: Dictionary = _frame.get("monitor", {})
	var counts: Dictionary = monitor.get("scene_counts", {})
	var packets: Dictionary = _frame.get("packets", {})
	lines.append("")
	lines.append("序号 %s · 第 %s 轮 · 步 %s · %.3f s" % [_frame.get("sequence"), _frame.get("loop"),
		_frame.get("step_no"), float(_frame.get("time_ns", 0)) / 1e9])
	lines.append("场景：%s 地物 · %s 树 / %s 灌木 / %s 草 patch · %d 相机 · %d 飞行物" % [
		counts.get("world_objects", "—"), counts.get("trees", "—"), counts.get("shrubs", "—"),
		counts.get("grass_patches", "—"), monitor.get("cameras", []).size(),
		monitor.get("flying_objects", []).size()])
	lines.append("事件包 标定 / 射线 / 诊断：%s / %s / %s" % [packets.get("calibration_event_count", "—"),
		packets.get("ray_count", "—"), packets.get("diagnostic_count", "—")])
	lines.append("渲染 %s ms · GPU %s ms · 成像开销 %s ms · 标定 %s ms" % [_frame.get("render_ms", "—"),
		_frame.get("gpu_render_ms", "—"), _frame.get("imager_overhead_ms", "—"), _frame.get("calibration_ms", "—")])
	lines.append("")
	lines.append("飞行物（%s 主相机）" % _state.get("station", ""))
	var unknown := str(monitor.get("occlusion_mode", "")) == "unknown"
	for item in monitor.get("flying_objects", []):
		var capture := "视场外"
		for seen in monitor.get("visibility", []):
			if str(seen.station_id) == str(_state.get("station", "")) and str(seen.camera_role) == "main" \
					and str(seen.truth_object_id) == str(item.truth_object_id):
				if bool(seen.capture_eligible):
					capture = "视场内（遮挡未知）" if unknown else "可见"
				elif not unknown and not seen.occlusions.is_empty():
					capture = "遮挡"
		var velocity: Array = item.state.velocity_enu_mps
		var speed := Vector3(float(velocity[0]), float(velocity[1]), float(velocity[2])).length()
		lines.append("  %s (%s)  位置 %s m  速度 %.2f m/s  %s" % [item.display_name, item.truth_object_id,
			_vector(item.state.position_enu_m), speed, capture])
	lines.append("")
	lines.append("辅助相机标定")
	var calibration: Dictionary = _frame.get("calibration", {})
	if calibration.is_empty():
		lines.append("  等待已渲染的辅助相机帧")
	for result in calibration.values():
		lines.append("  %s  %s / %s / %s  角点 %s  RMSE %s px  旋转误差 %s°  主相机偏移 %s m" % [
			result.get("station_id"), result.get("recognition_status"), result.get("calibration_status"),
			result.get("health"), result.get("corner_count", "—"), result.get("reprojection_rmse_px", "—"),
			result.get("rotation_error_deg", "—"), _vector(result.get("main_position_offset_enu_m"), 3)])
	return "\n".join(lines)


func _show_log() -> void:
	var entries: Array = _state.get("log", [])
	var last := -1 if entries.is_empty() else int(entries[-1].seq)
	if last == _log_seq:
		return
	var lines := []
	var warned := false
	for entry in entries:
		lines.append("[%s] %s: %s" % [entry.level, entry.logger, entry.message])
		if entry.get("traceback") != null:
			lines.append(str(entry.traceback))
		if int(entry.seq) > _log_seq and str(entry.level) in ["WARNING", "ERROR", "CRITICAL"]:
			warned = true
	_log_seq = last
	_set_text(_log, "\n".join(lines))
	_log.scroll_vertical = _log.get_line_count()
	if warned:
		_panel.set_tab_title(2, "日志 ●")


static func _set_text(view: TextEdit, text: String) -> void:
	# Replacing identical text would reset the reader's scroll and selection.
	if view.text != text:
		var scroll := view.scroll_vertical
		view.text = text
		view.scroll_vertical = scroll


func _show_editor() -> void:
	var editor: Dictionary = _state.get("editor", {})
	var active := bool(editor.get("active", false))
	var document := _document()
	_edit_begin.disabled = active or document.is_empty()
	for control in [_edit_cancel, _edit_commit, _edit_object, _edit_add_station,
			_edit_add_target, _edit_delete, _edit_landscape, _edit_apply]:
		control.disabled = not active
	for edit in _edit_fields.values() + [_edit_new_id]:
		edit.editable = active
	_edit_state.text = "编辑中，实验已暂停" if active else "需要 --scenario 启动" if document.is_empty() else "未锁定"
	var options := []
	for pair in [["stations", "station", "相机站"], ["targets", "target", "飞行物"]]:
		for item in document.get(pair[0], []):
			options.append([pair[1] + ":" + str(item.id), "%s · %s" % [pair[2], item.get("display_name", item.id)]])
	var previous := "" if _edit_object.selected < 0 else str(_edit_object.get_item_metadata(_edit_object.selected))
	var current := []
	for index in range(_edit_object.item_count):
		current.append([str(_edit_object.get_item_metadata(index)), _edit_object.get_item_text(index)])
	if current != options:
		_edit_object.clear()
		for option in options:
			_edit_object.add_item(option[1])
			_edit_object.set_item_metadata(_edit_object.item_count - 1, option[0])
	var wanted := _edit_pending if not _edit_pending.is_empty() else previous
	for index in range(_edit_object.item_count):
		if str(_edit_object.get_item_metadata(index)) == wanted:
			_edit_object.select(index)
			_edit_pending = ""
	if _edit_object.selected < 0 and _edit_object.item_count > 0:
		_edit_object.select(0)
	var landscapes: Array = ["保持当前地景"] + editor.get("landscapes", [])
	var shown := []
	for index in range(_edit_landscape.item_count):
		shown.append(_edit_landscape.get_item_text(index))
	if shown != landscapes:
		_edit_landscape.clear()
		for landscape in landscapes:
			_edit_landscape.add_item(str(landscape))
	var uri := str(document.get("osm", {}).get("asset", {}).get("uri", "")) if document.get("osm") is Dictionary else ""
	_edit_landscape.select(maxi(0, landscapes.find("hannover")) if uri.contains("hannover") else 0)
	_load_selection()


## Copy the selected object into the fields whenever the selection or its draft changes.
func _load_selection() -> void:
	var selected := _selected()
	var item := _selected_item()
	var key := "" if item.is_empty() else str(selected) + JSON.stringify(item)
	if key == _edit_loaded:
		return
	_edit_loaded = key
	var is_station: bool = not selected.is_empty() and selected[0] == "station"
	var motion: Dictionary = item.get("motion", {}) if item.get("motion") is Dictionary else {}
	var position = item.get("position_enu_m") if is_station else motion.get(_position_field(motion))
	var velocity = motion.get("velocity_enu_mps", [0, 0, 0])
	var values := {
		"yaw": item.get("main_yaw_deg", 0), "elevation": item.get("main_elevation_deg", 0),
	}
	for index in range(3):
		values[["E", "N", "U"][index]] = position[index] if position is Array else ""
		values[["vE", "vN", "vU"][index]] = velocity[index] if velocity is Array else 0
	for field in _edit_fields:
		var value = values[field]
		_edit_fields[field].text = "" if value is String else String.num(float(value))
	var velocity_editable := str(motion.get("type", "")) == "constant_velocity"
	for field in ["yaw", "elevation"]:
		_edit_labels[field].visible = is_station
	for field in ["vE", "vN", "vU"]:
		_edit_labels[field].visible = not is_station and velocity_editable
