# Godot 全栈架构重构 - 实施方案

**Issue**: #25  
**目标**: 将场景逻辑、编辑和可视化全部迁移到 Godot,移除 Python 逻辑层和 Web 工作台

---

## 一、当前架构分析

### 1.1 Python 职责 (待移除)

#### `scenarios/run.py` (场景运行)
- 编译 JSON → `ScenarioSpec`
- 时间步进和实验时钟管理
- 物体运动计算 (`constant_velocity`、`orbit`)
- 可见性判断和遮挡计算
- 输出 JSONL 证据文件

#### `rendering/godot.py` (渲染适配器)
- 启动 Godot worker 进程
- 序列化场景快照 → JSON
- Socket 通信 (Python ↔ Godot)
- 几何实体编码 (`_entity()`, `_request()`)
- 资源注册表管理

#### `web/preview.html` (Web 工作台)
- 场景编辑 UI
- 实时预览
- 时间轴控制
- 参数调整面板

### 1.2 Godot 当前职责

#### `worker.gd` (渲染 worker)
- Socket 服务器,接收快照请求
- 场景图管理 (世界、相机、光照)
- 材质和着色器
- 视频编码接口
- 编辑器自由视角

### 1.3 数据流 (当前)

```
JSON场景配置
  ↓
Python编译器 (scenarios/authoring.py)
  ↓
ScenarioSpec (内存对象)
  ↓
Python场景控制器 (scenarios/run.py)
  |→ 物体运动逻辑
  |→ 时间步进
  |→ 快照生成 → JSON → Socket → Godot worker.gd → 渲染
  └→ 输出JSONL证据
```

---

## 二、目标架构

### 2.1 Godot 新职责

#### `main.gd` (CLI 入口)
- 命令行参数解析
- 场景加载和验证
- 输出管理 (PNG + JSON)
- 批量场景运行

#### `scene_controller.gd` (场景控制核心)
- JSON 场景加载和解析
- 物体运动逻辑 (`constant_velocity`, `orbit`)
- 时间步进和实验时钟
- 状态管理和真值输出
- 外部资产加载 (SHA256 校验)
- 遮挡计算 (集成现有逻辑)

#### `visualizer.gd` (可视化工具)
- 射线绘制 (从边缘侧输入)
- 交点显示 (从中心端输入)
- 真值对比
- 回放控制 UI

#### `editor_plugin.gd` (Godot Editor 插件)
- 可视化编辑场景
- 参数面板
- 导入/导出 JSON
- 实时预览

### 2.2 Python 简化职责

#### `godot.py` (薄层启动器)
- 调用 Godot CLI
- 参数转换
- 保持命令行兼容性

### 2.3 数据流 (目标)

```
JSON场景配置
  ↓
Godot scene_controller.gd
  |→ 物体运动逻辑
  |→ 时间步进
  |→ 渲染 (内部调用)
  └→ 输出 PNG + JSON真值

Python包装器 (可选)
  └→ 仅调用 Godot CLI
```

---

## 三、关键设计决策

### 3.1 数据格式兼容性

**决策**: 保持 JSON 场景配置 100% 向后兼容
- 现有 `scenarios/*.json` 无需修改
- `scenario-authoring-v2` 格式不变
- 内部可增加新字段,但旧场景必须能加载

**验证**: 所有现有场景通过 `--check` 测试

### 3.2 Godot 架构边界

**场景控制器** (`scene_controller.gd`):
- 纯数据处理,不依赖场景树
- 可独立单元测试
- 输出标准 JSON

**渲染器** (`worker.gd` 改造):
- 接收场景控制器快照
- 负责视觉呈现
- 保持现有材质/着色器

### 3.3 Python ↔ Godot 接口

**阶段 1**: Python 调用 Godot 的方式
```bash
godot --headless --script main.gd -- \
  --scenario scenarios/empty.json \
  --output ./output/ \
  --mode finite
```

**Python 包装器**:
```python
# godot.py 简化为
def run_scenario(scenario_path, output_dir, **options):
    godot = _find_godot()
    args = _build_args(scenario_path, output_dir, options)
    subprocess.run([godot, "--headless", "--script", "main.gd", "--"] + args)
```

### 3.4 物体运动实现

**Python 当前实现** (参考 `scenarios/behaviors.py`):
```python
def constant_velocity(initial_pos, velocity, time):
    return initial_pos + velocity * time
```

**GDScript 实现** (scene_controller.gd):
```gdscript
func _evaluate_motion(target: Dictionary, time_s: float) -> Vector3:
    var motion: Dictionary = target.motion
    match motion.type:
        "constant_velocity":
            var pos0 := _vec3(motion.position_enu_m)
            var vel := _vec3(motion.velocity_enu_mps)
            return pos0 + vel * time_s
        "orbit":
            # TODO: 实现轨道运动
            return Vector3.ZERO
```

### 3.5 遮挡计算迁移

**当前**: Python `occlusion_index.py`
**目标**: 
1. 短期: Godot 通过射线查询调用 Python (FFI)
2. 长期: GDScript 重写或 GDExtension (C++)

**阶段 1 方案**: 暂时关闭遮挡,设置 `occlusion_mode: "unknown"`

---

## 四、实施阶段

### 阶段 1: Godot 独立运行 (P0 - 阻塞)

#### 任务 1.1: GDScript 场景控制器

**文件**: `src/chongming/rendering/godot/scene_controller.gd`

**核心功能**:
```gdscript
class_name SceneController

var scenario: Dictionary
var current_time_s := 0.0
var current_step := 0

func load_scenario(json_path: String) -> Error:
    var file := FileAccess.open(json_path, FileAccess.READ)
    if file == null: return FAILED
    var json := JSON.parse_string(file.get_as_text())
    if not _validate_schema(json): return FAILED
    scenario = json
    return OK

func step(delta_s: float) -> Dictionary:
    current_time_s += delta_s
    current_step += 1
    var snapshot := _build_snapshot()
    return snapshot

func _build_snapshot() -> Dictionary:
    return {
        "time_s": current_time_s,
        "step": current_step,
        "stations": _evaluate_stations(),
        "targets": _evaluate_targets(),
        "terrain": scenario.get("terrain", {}),
        "illumination": _evaluate_illumination()
    }

func _evaluate_targets() -> Array:
    var result := []
    for target in scenario.get("targets", []):
        var pos := _evaluate_motion(target, current_time_s)
        result.append({
            "id": target.id,
            "position_enu_m": [pos.x, pos.y, pos.z],
            "geometry": target.geometry
        })
    return result
```

**依赖**:
- JSON 解析: `JSON.parse_string()`
- 文件读取: `FileAccess`
- 向量数学: `Vector3`

**测试**:
```gdscript
# test_scene_controller.gd
func test_constant_velocity():
    var controller := SceneController.new()
    controller.load_scenario("test_data/simple.json")
    controller.step(1.0)
    var targets := controller._evaluate_targets()
    assert(targets[0].position_enu_m[0] == 3.0)  # 0 + 3*1
```

#### 任务 1.2: Godot CLI 入口

**文件**: `src/chongming/rendering/godot/main.gd`

**命令行参数**:
```gdscript
extends Node

var args := {}

func _ready() -> void:
    _parse_args()
    var controller := SceneController.new()
    var error := controller.load_scenario(args.scenario)
    if error != OK:
        push_error("Failed to load scenario: %s" % args.scenario)
        get_tree().quit(1)
        return
    
    match args.mode:
        "finite":
            _run_finite(controller)
        "check":
            _check_scenario(controller)
        _:
            push_error("Unknown mode: %s" % args.mode)
            get_tree().quit(1)

func _parse_args() -> void:
    var user_args := OS.get_cmdline_user_args()
    var i := 0
    while i < user_args.size():
        match user_args[i]:
            "--scenario":
                args.scenario = user_args[i + 1]
                i += 2
            "--output":
                args.output = user_args[i + 1]
                i += 2
            "--mode":
                args.mode = user_args[i + 1]
                i += 2
            _:
                i += 1

func _run_finite(controller: SceneController) -> void:
    var output_dir := DirAccess.open(args.output)
    if output_dir == null:
        DirAccess.make_dir_absolute(args.output)
    
    var duration_s := controller.scenario.duration_s
    var fps := controller.scenario.fps
    var dt := 1.0 / fps
    
    var steps := []
    for frame in range(int(duration_s * fps)):
        var snapshot := controller.step(dt)
        steps.append(snapshot)
        # TODO: 渲染快照 → PNG
        # TODO: 输出真值 JSON
    
    _write_manifest(args.output, steps)
    get_tree().quit(0)
```

#### 任务 1.3: Python 包装器重构

**当前**: `godot.py` 768 行,包含复杂的 socket 通信

**重构后**: ~100 行,仅命令行包装

```python
"""Godot CLI wrapper for backward compatibility."""

import os
import shutil
import subprocess
from pathlib import Path

def _find_godot() -> str:
    """Locate Godot 4.7.2 executable."""
    env_godot = os.environ.get("CHONGMING_GODOT")
    if env_godot:
        return env_godot
    for name in ("godot4", "godot"):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(
        "Godot 4.7.2 not found. Set CHONGMING_GODOT or install in PATH."
    )

def run_scenario(
    scenario_path: str | Path,
    output_dir: str | Path,
    *,
    mode: str = "finite",
    no_video: bool = False,
    render_every: int = 1,
) -> subprocess.CompletedProcess:
    """Run a scenario through Godot CLI."""
    godot = _find_godot()
    script = Path(__file__).parent / "godot" / "main.gd"
    
    args = [
        godot,
        "--headless",
        "--script", str(script),
        "--",
        "--scenario", str(scenario_path),
        "--output", str(output_dir),
        "--mode", mode,
    ]
    if no_video:
        args.append("--no-video")
    if render_every != 1:
        args.extend(["--render-every", str(render_every)])
    
    return subprocess.run(args, check=True)

# 移除的代码 (~650 行):
# - GodotRenderer 类
# - Socket 通信
# - _entity() / _request() 序列化
# - 资源注册表
# - 线程管理
```

**保留的接口**:
```python
# 命令行工具仍然工作
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run_scenario(args.scenario, args.output)
```

#### 验收标准

**功能测试**:
```bash
# 方式 1: 直接调用 Godot
godot --headless --script src/chongming/rendering/godot/main.gd -- \
  --scenario scenarios/empty.json \
  --output /tmp/test-godot \
  --mode finite

# 方式 2: Python 包装器 (兼容性)
chongming-center --scenario scenarios/empty.json --check

# 验证输出
test -f /tmp/test-godot/manifest.json
test -f /tmp/test-godot/online.jsonl
```

**回归测试**:
```bash
# 所有现有场景必须通过
for scenario in scenarios/*.json; do
    godot --headless --script main.gd -- \
      --scenario "$scenario" \
      --mode check || exit 1
done
```

---

### 阶段 2: Godot 可视化和编辑 (P1)

#### 任务 2.1: 射线可视化

**文件**: `src/chongming/rendering/godot/ray_visualizer.gd`

**功能**:
- 从 JSONL 加载边缘侧射线
- 3D 场景中绘制线段
- 颜色编码 (时间/ID/置信度)
- 实时更新

**实现**:
```gdscript
class_name RayVisualizer extends Node3D

var rays := []

func load_rays(jsonl_path: String) -> void:
    var file := FileAccess.open(jsonl_path, FileAccess.READ)
    while not file.eof_reached():
        var line := file.get_line()
        if line.is_empty(): continue
        var ray: Dictionary = JSON.parse_string(line)
        rays.append(ray)
    _rebuild_mesh()

func _rebuild_mesh() -> void:
    var mesh := ImmediateMesh.new()
    mesh.surface_begin(Mesh.PRIMITIVE_LINES)
    for ray in rays:
        var origin := _vec3(ray.origin)
        var direction := _vec3(ray.direction)
        var color := _time_color(ray.time_s)
        mesh.surface_set_color(color)
        mesh.surface_add_vertex(origin)
        mesh.surface_add_vertex(origin + direction * 1000.0)
    mesh.surface_end()
    $MeshInstance3D.mesh = mesh
```

#### 任务 2.2: Godot Editor 插件

**文件**: `src/chongming/rendering/godot/addons/chongming/editor_plugin.gd`

**面板功能**:
- 加载 JSON 场景
- 可视化编辑站点位置
- 可视化编辑目标轨迹
- 参数调整 (duration, fps)
- 导出编辑后的 JSON

**实现**:
```gdscript
@tool
extends EditorPlugin

var dock: Control

func _enter_tree() -> void:
    dock = preload("res://addons/chongming/editor_dock.tscn").instantiate()
    add_control_to_dock(DOCK_SLOT_RIGHT_UL, dock)
    dock.file_loaded.connect(_on_file_loaded)

func _exit_tree() -> void:
    remove_control_from_docks(dock)
    dock.free()

func _on_file_loaded(path: String) -> void:
    var controller := SceneController.new()
    controller.load_scenario(path)
    _populate_scene(controller.scenario)
```

---

### 阶段 3: Web 功能迁移和移除 (P2)

#### 任务 3.1: 移除 Web 工作台

**删除文件**:
```bash
rm -rf src/chongming/web/
```

**移除依赖** (`pyproject.toml`):
```toml
# 删除:
# fastapi
# uvicorn
# jinja2
```

**移除命令入口**:
```python
# pyproject.toml [project.scripts]
# 删除: chongming-preview
```

#### 任务 3.2: 文档更新

**需要更新的文档**:
- `README.md`: 移除 Web 预览命令
- `docs/user-guide.md`: 更新所有命令示例
- `docs/development.md`: 更新测试命令
- `docs/design.md`: 更新架构说明
- `AGENTS.md`: 更新工作流程

**示例修改** (`docs/user-guide.md`):
```markdown
## 命令

| 命令                | 用途                           |
| ------------------- | ------------------------------ |
| `chongming-center`  | 场景检查、中心算法开发         |
| `chongming-scene`   | 有限场景运行及证据导出         |
| `chongming-osm`     | OSM 地景导入                   |

### 场景可视化

使用 Godot Editor 打开项目:
\`\`\`bash
godot --editor src/chongming/rendering/godot/project.godot
\`\`\`

加载 JSON 场景文件,使用 Chongming 插件编辑和预览。
```

---

## 五、风险和缓解

### 5.1 集成测试困难

**风险**: "我们很难在重构半途进行集成测试"

**缓解**:
1. **分支开发**: 在独立分支完成全部阶段
2. **增量验证**: 每个任务完成后立即运行单元测试
3. **快照对比**: 保存重构前输出,重构后逐项对比
4. **回滚策略**: 保留原有代码直到全部验证通过

### 5.2 正确性保证

**关键点**:
- 物体运动逻辑必须与 Python 实现完全一致
- 时间步进精度不能降低
- JSON 输出格式严格兼容

**验证方法**:
```bash
# 运行相同场景,对比输出
python scripts/compare_outputs.py \
  --old results/python-baseline/ \
  --new results/godot-refactor/ \
  --tolerance 1e-9
```

### 5.3 性能回退

**风险**: GDScript 性能可能不如 Python + NumPy

**监控**:
- 记录每步耗时 (`timing.jsonl`)
- 对比重构前后帧率
- 性能关键路径考虑 GDExtension (C++)

### 5.4 依赖管理

**风险**: Godot 4.7.2 必须保持稳定

**缓解**:
- Nix flake 锁定 Godot 版本
- 不升级 Godot 直到重构完成
- 记录最小可用 Godot 版本

---

## 六、测试策略

### 6.1 单元测试

**GDScript 测试** (使用 GUT - Godot Unit Test):
```gdscript
# test/unit/test_scene_controller.gd
extends GutTest

func test_load_empty_scenario():
    var controller := SceneController.new()
    var err := controller.load_scenario("res://test/data/empty.json")
    assert_eq(err, OK)
    assert_eq(controller.scenario.scenario_id, "empty-control")

func test_constant_velocity_motion():
    var controller := SceneController.new()
    controller.load_scenario("res://test/data/constant_velocity.json")
    controller.step(1.0)
    var targets := controller._evaluate_targets()
    assert_almost_eq(targets[0].position_enu_m[0], -15.0, 0.001)
```

### 6.2 集成测试

**场景回归测试**:
```bash
#!/bin/bash
# scripts/test_all_scenarios.sh

set -e

for scenario in scenarios/*.json; do
    echo "Testing $scenario..."
    godot --headless --script main.gd -- \
      --scenario "$scenario" \
      --output "/tmp/test-$(basename $scenario .json)" \
      --mode check
done

echo "All scenarios passed!"
```

### 6.3 输出对比测试

**验证数值一致性**:
```python
# scripts/compare_outputs.py
import json
import numpy as np

def compare_truth_files(old_path, new_path, tolerance=1e-9):
    old_data = [json.loads(line) for line in open(old_path)]
    new_data = [json.loads(line) for line in open(new_path)]
    
    assert len(old_data) == len(new_data), "Step count mismatch"
    
    for i, (old, new) in enumerate(zip(old_data, new_data)):
        old_pos = np.array(old['targets'][0]['position_enu_m'])
        new_pos = np.array(new['targets'][0]['position_enu_m'])
        diff = np.abs(old_pos - new_pos).max()
        assert diff < tolerance, f"Step {i}: position diff {diff}"
```

---

## 七、时间估算

### 阶段 1 (2-3 天)
- 任务 1.1: GDScript 场景控制器 (1 天)
- 任务 1.2: Godot CLI 入口 (0.5 天)
- 任务 1.3: Python 包装器重构 (0.5 天)
- 测试和修复 (1 天)

### 阶段 2 (2-3 天)
- 任务 2.1: 射线可视化 (0.5 天)
- 任务 2.2: 交点可视化 (0.5 天)
- 任务 2.3: Editor 插件 (1.5 天)
- 任务 2.4: 回放控制 UI (0.5 天)

### 阶段 3 (1-2 天)
- 任务 3.1: 移除 Web (0.5 天)
- 任务 3.2: 文档更新 (0.5 天)
- 最终回归测试 (1 天)

**总计**: 约 5-8 天

---

## 八、下一步行动

### 立即行动
1. **审查本方案**: 确认架构设计和边界划分
2. **准备测试数据**: 运行当前系统,保存输出作为 baseline
3. **创建开发分支**: `git checkout -b refactor/godot-fullstack`

### 开始实施
1. 从阶段 1.1 开始: `scene_controller.gd`
2. 每完成一个任务,立即编写测试
3. 保持小步提交,便于回滚

### 审查点
- 阶段 1 完成: 验证 Godot 可独立运行
- 阶段 2 完成: 验证可视化功能
- 阶段 3 完成: 最终回归测试

---

## 附录 A: 关键文件清单

### 新增文件
- `src/chongming/rendering/godot/scene_controller.gd`
- `src/chongming/rendering/godot/main.gd`
- `src/chongming/rendering/godot/ray_visualizer.gd`
- `src/chongming/rendering/godot/target_visualizer.gd`
- `src/chongming/rendering/godot/addons/chongming/editor_plugin.gd`
- `src/chongming/rendering/godot/addons/chongming/editor_dock.tscn`

### 重构文件
- `src/chongming/rendering/godot.py` (768 行 → ~100 行)
- `src/chongming/rendering/godot/worker.gd` (集成 scene_controller)

### 删除文件
- `src/chongming/web/preview.html`
- `src/chongming/web/__init__.py`

### 更新文档
- `README.md`
- `docs/user-guide.md`
- `docs/development.md`
- `docs/design.md`
- `AGENTS.md`

---

## 附录 B: JSON 场景格式 (不变)

```json
{
  "format": "scenario-authoring-v2",
  "scenario_id": "example",
  "random_seed": 42,
  "duration_s": 10,
  "fps": 30,
  "stations": [
    { "id": "station-01", "position_enu_m": [0, 0, 4] }
  ],
  "targets": [
    {
      "id": "target-01",
      "geometry": { "type": "sphere", "radius_m": 1.0 },
      "motion": {
        "type": "constant_velocity",
        "position_enu_m": [0, 10, 20],
        "velocity_enu_mps": [1, 0, 0]
      }
    }
  ]
}
```

---

**最后更新**: 2026-10-28  
**状态**: 等待审查
