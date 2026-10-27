# Issue #18：Truck Town 首个外部场景测例

Issue #15 的录像格式已经支持多路相机 PNG、相机捕获时序、场景/目标真值和 timing 记录。Truck Town 的第一次接入只改变录制端的 Godot 视觉场景；录制完成后使用已有回放源，不再重新启动 Godot 渲染。

## 场景来源

使用 Godot 官方 Demo 仓库中的 [`3d/truck_town`](https://github.com/godotengine/godot-demo-projects/tree/master/3d/truck_town)。仓库整体采用 MIT 许可证，但目录内模型和纹理仍应保留各自的 `license.txt` 文件。固定一个仓库提交后再进行正式测例录制，并在录像说明中记录提交号和资源摘要。

Truck Town 的完整游戏场景包含车辆控制脚本和 UI。第一阶段只挂载静态城市场景 `res://town/model/town_model.gltf`，避免把游戏控制逻辑混入实验时钟；我们的 Python 逻辑负责相机和飞行物状态。

## 临时运行配置

先将外部工程检出到本地目录，不把它复制进本仓库：

```sh
git clone --depth 1 https://github.com/godotengine/godot-demo-projects.git /tmp/godot-demo-projects
```

录制时设置：

```sh
export CHONGMING_GODOT_EXTERNAL_PROJECT=/tmp/godot-demo-projects/3d/truck_town
export CHONGMING_GODOT_EXTERNAL_SCENE=res://town/model/town_model.gltf
uv run --frozen chongming-scene \
  --scenario scenarios/truck_town.json \
  --output recordings/truck-town-v1
```

`CHONGMING_GODOT_EXTERNAL_SCENE` 必须是工程内的 `res://` 路径。渲染器会创建临时工程，把外部资源与本项目 worker 合并；外部工程的 `project.godot` 不会覆盖 worker 的启动配置。

录制完成后，可以按 Issue #15 的方式以正常速度回放：

```sh
uv run --frozen chongming-preview \
  --recording recordings/truck-town-v1 \
  --playback-fps 30
```

回放不会重新渲染 Truck Town，也不会改变原始实验时间戳。

## 当前边界

- Truck Town 使用 `occlusion_mode: "unknown"`；外部网格不参与 Python 遮挡真值计算。
- 录制仍保存相机和目标真值，后续评估只在固定录像内比较系统排名。
- 第一阶段只验证静态城市场景和动态飞行物注入；车辆物理、交互 UI 和游戏脚本不属于实验场景。
