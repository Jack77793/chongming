# Godot 集成测试指南

## 概述

`scripts/test_godot_integration.py` 验证 Godot 全栈架构重构（Issue #25）的正确性，确保新系统与原 Python 系统输出一致。

## 准备工作

### 环境要求

- Godot 4.7.2 标准版（设置 `CHONGMING_GODOT` 或将 `godot4`/`godot` 加入 PATH）
- Python 3.12+ 及项目依赖（`uv sync --frozen`）
- 项目根目录执行测试

### 测试场景

默认测试场景：
- `scenarios/empty.json` - 无目标基础场景
- `scenarios/constant_velocity.json` - 匀速直线运动

可通过命令行指定其他场景。

## 测试工作流

### 第一步：验证场景加载

仅验证 Godot 能否正确加载和解析场景配置：

```bash
python3 scripts/test_godot_integration.py --check-only
```

**验收标准**：
- Godot 成功加载场景 JSON
- 输出 `✓ Validation passed`
- 不执行实际运行或对比

### 第二步：生成基线输出

从原 Python 系统生成真值基线：

```bash
python3 scripts/test_godot_integration.py --regenerate-baseline
```

**生成内容**：
- `baseline/<scenario_name>/truth.jsonl` - Python 系统真值输出
- `baseline/<scenario_name>/manifest.json` - 运行元数据
- 其他运行产物（timing.jsonl 等）

**注意**：
- 基线输出需提交到 `baseline/` 目录作为参考
- 仅在 Python 系统修改后需要重新生成
- 当前 Python 系统需要 Godot 支持，确保 `CHONGMING_GODOT` 已设置

### 第三步：对比测试

将 Godot 新系统输出与 Python 基线对比：

```bash
python3 scripts/test_godot_integration.py
```

**对比内容**：
- 时间戳精度（`time_s`, `time_ns`, `step`）
- 物体位置（`position_enu_m`）
- 物体速度（`velocity_enu_mps`）
- 记录条数

**数值容差**：
- 浮点误差容忍度：`1e-9`
- 向量比较：逐分量比较，每个分量独立判定

**输出示例**：
```
Testing: empty.json
  ✓ Outputs match (tolerance 1e-9)

Testing: constant_velocity.json
  Output mismatch (2 errors):
    Step 0: target[0] position_enu_m differs: Godot=[1.0, 2.0, 3.0], Python=[1.0, 2.0, 3.00000001]
    Step 5: time_s differs: Godot=0.166667, Python=0.166666667, delta=3.33e-07
```

### 测试特定场景

```bash
# 单个场景
python3 scripts/test_godot_integration.py empty.json

# 多个场景
python3 scripts/test_godot_integration.py empty.json constant_velocity.json center_clean.json
```

## 对比字段说明

### 顶层字段
- `time_s` (float) - 实验时间（秒）
- `time_ns` (int) - 实验时间（纳秒）
- `step` (int) - 步骤编号
- `frame_period_s` (float) - 帧周期

### 嵌套字段（logical_scene）

**站点（stations）**：
- `id` (string) - 站点标识
- `position_enu_m` (Vec3) - ENU 坐标

**目标（targets）**：
- `id` (string) - 目标标识
- `position_enu_m` (Vec3) - 位置
- `velocity_enu_mps` (Vec3) - 速度
- `geometry` (object) - 几何形状（仅校验存在性）

### 忽略字段
- 时间戳格式化字符串
- 会话 ID
- 帧文件路径
- Python 特有的辅助数据

## 调试失败案例

### 位置误差过大
**原因**：运动计算精度差异
**检查**：
1. 对比 `scene_controller.gd` 与 `scenarios/behaviors.py` 的运动公式
2. 验证浮点运算顺序
3. 确认时间步进一致性

### 记录数不匹配
**原因**：场景执行步数不同
**检查**：
1. 确认 `duration_s` 和 `fps` 解析正确
2. 验证 `is_finished()` 逻辑
3. 检查步进循环终止条件

### 字段缺失
**原因**：输出格式不完整
**检查**：
1. 对比 `_build_truth_record()` 与 Python 的 `ScenarioStep.truth`
2. 确认所有必需字段已填充
3. 验证 JSON 序列化未丢失字段

## 限制与未来工作

### 当前限制
- 不对比射线数据（`rays` 字段）
- 不对比相机帧元数据（`camera_frames` 字段）
- 不对比静态世界物体（`world_objects`）
- 不验证渲染输出（PNG/视频）

### Phase 1.2 增强
- 增加射线方向和起点对比
- 增加相机位置和姿态对比
- 增加遮挡状态验证

### Phase 1.3 增强
- 增加视频帧哈希对比
- 增加像素级回归测试（可选）

## 参考

- [Godot 全栈架构重构方案](../REFACTOR_PLAN.md)
- [设计说明](design.md)
- [开发指南](development.md)
