# 录制与回放功能实现

**分支**: `feat/recording-playback`  
**状态**: ✅ 第二阶段完成 - 回放速率控制  
**创建时间**: 2026-10-26  
**更新时间**: 2026-10-26

## 概述

实现 issue #15 提出的"录制与回放"机制,以解决虚拟场景实时渲染性能瓶颈(~10fps)。通过离线录制高质量场景,然后高帧率回放,提升演示直观性和调试效率。

## 第二阶段: 回放速率控制 ✅

### 实现内容

1. **帧率参数化** (`playback.py`)
   - 为 `RecordedScenarioSource` 添加 `playback_fps` 参数
   - 为 `load_recording_source()` 添加 `playback_fps` 参数
   - 支持 `None` 值以实现无限制回放

2. **时间控制机制** (`playback.py`)
   ```python
   if self.playback_fps is not None:
       current_time = time.perf_counter()
       if self._last_step_time is not None:
           target_interval = 1.0 / self.playback_fps
           elapsed = current_time - self._last_step_time
           sleep_time = target_interval - elapsed
           if sleep_time > 0:
               time.sleep(sleep_time)
       self._last_step_time = time.perf_counter()
   ```

3. **命令行接口** (`preview.py`)
   - 添加 `--playback-fps` 参数
   - 参数验证: 必须与 `--recording` 配合使用
   - 参数验证: 必须为正数

4. **单元测试** (`tests/test_playback_fps.py`)
   - 创建模拟录像包的辅助函数
   - 三个测试用例覆盖所有场景
   - 所有测试通过 ✅

### 测试结果

**单元测试**:
```
test_playback_fps_accuracy ... ok
test_playback_with_fps_limit ... ok
test_playback_without_fps_limit ... ok

Ran 3 tests in 0.978s - OK
```

**30fps 回放实测**:
```
Target FPS: 30.0
Total time: 1.073s (expected 0.967s)
Average FPS: 27.02
Frame interval: 36.99ms (expected 33.33ms)
Accuracy: ✓ Within 20% tolerance
Unlimited speed: 2022fps (72x faster)
```

### 性能分析

| 模式 | 实测帧率 | CPU 负载 | 备注 |
|------|----------|----------|------|
| 实时渲染 | ~10fps | 高 | 受 Godot 渲染瓶颈限制 |
| 回放(无限制) | ~2000fps | 低 | 仅 PNG 解码 + Python 开销 |
| 回放(30fps) | ~27fps | 极低 | 帧率控制生效,流畅体验 |

帧率略低于目标的原因:
1. Python `time.sleep()` 的精度限制(~1-10ms)
2. 操作系统线程调度延迟
3. PNG 图像解码的 I/O 开销波动

对于 30fps 的目标,27fps 的实际性能是可接受的(误差 10%),用户体验流畅。

## 实现方案

### 核心组件

1. **录制模式** (已有,无需修改)
   - 使用现有的 `run.py` 和 `ScenarioRunner`
   - 输出包含视频帧、真值数据、在线数据包和时序信息的完整录像包
   - 保存 `authoring.json` (用户配置) 和 `scenario.json` (编译后配置)

2. **回放模块** (`chongming/scenarios/playback.py`) ✅
   - `RecordingReader`: 读取并验证录像包格式
   - `RecordedScenarioSource`: 实现 `ScenarioSource` protocol,按时序回放录像
   - `load_recording_source()`: 主入口,从录像包加载场景源

3. **场景源抽象** (`chongming/scenarios/source.py`) ✅
   - 定义 `ScenarioSource` Protocol
   - 统一实时渲染和回放的接口
   - 下游模块(中心端算法、预览界面)无需感知数据来源

4. **预览界面支持** (`chongming/preview.py`) ✅
   - 添加 `--recording` 参数支持回放模式
   - 保持与实时模式相同的用户体验

### 录像包结构

```
recordings/demo_v1/
├── manifest.json          # 录像元信息(帧率、时长、相机配置等)
├── authoring.json         # 原始场景配置(用户提供的JSON)
├── scenario.json          # 编译后的场景定义
├── video/                 # 视频帧(PNG格式)
│   ├── camera-000/
│   ├── camera-001/
│   ├── camera-002/
│   └── camera-003/
├── online.jsonl           # 在线数据包(每步一行)
├── truth.jsonl            # 真值时序数据(每步一行)
├── truth-static.json      # 静态场景几何
├── timing.jsonl           # 性能指标
└── summary.json           # 运行摘要
```

### 使用方法

**录制场景**:
```bash
export GODOT_BINARY=/Applications/Godot.app/Contents/MacOS/Godot
uv run python -m chongming.scenarios.run scenarios/blank.json output_dir/
```

**回放场景** (预览界面):
```bash
uv run python -m chongming.preview --recording output_dir/
```

**回放场景** (中心端,待实现):
```bash
uv run python -m chongming --recording output_dir/
```

## 测试验证

✅ 基本回放功能验证通过:
- 成功录制 `blank.json` 场景 (2秒, 60帧, 4个相机)
- 回放源正确读取录像包元数据
- 正确生成 `ScenarioStep` 序列
- 图像帧正确加载 (640x480 RGB8)

```
✓ 场景ID: empty-control
✓ 相机配置数: 4

生成前3步:
  Step 1: frames=4 (station-01:auxiliary 640x480)
  Step 2: frames=0
  Step 3: frames=0

✅ 回放功能测试成功!
```

## 已知限制

1. **帧率精度**: 由于 Python `time.sleep()` 和系统调度的限制,实际帧率可能比目标帧率低 5-15%
2. **真值简化**: `ScenarioTruthOutput` 中的 `camera_frames` 和 `online_correspondences` 为空元组(待完整反序列化)
3. **中心端未适配**: `chongming/__main__.py` 尚未添加 `--recording` 支持
4. **无视频压缩**: 使用 PNG 存储,占用空间较大

## 下一步计划

**第三阶段** (性能优化):
- [ ] 视频压缩支持(H.264/H.265)
- [ ] 完整真值数据反序列化
- [ ] 提高帧率控制精度(考虑使用更精确的计时机制)

**第四阶段** (增强功能):
- [ ] 录像片段剪辑和拼接
- [ ] 录像质量验证工具
- [ ] 中心端 `--recording` 参数支持
- [ ] 录像元数据查询工具
- [ ] 回放暂停/快进/倒放控制

## 技术细节

### 设计决策

1. **使用 authoring.json 而非 scenario.json**
   - `scenario.json` 是编译后的完整配置(~27KB),不便于手动编辑
   - `authoring.json` 是原始用户配置(~250B),紧凑且可读
   - 回放时重新编译 authoring.json,确保与录制时行为一致

2. **过滤 `$type` 字段**
   - JSONL 中的对象包含类型标记 `$type` 字段
   - Python dataclass 构造器不接受额外参数,需显式过滤

3. **Protocol 而非 ABC**
   - 使用 `typing.Protocol` 定义 `ScenarioSource` 接口
   - 支持结构化子类型(structural subtyping)
   - 无需显式继承,减少耦合

### 兼容性

- ✅ 与现有实时渲染模式完全兼容
- ✅ 录像包格式向后兼容(识别 `scope: virtual-subsystem`)
- ✅ 保留所有现有 API 和命令行接口

## 参考

- Issue: #15
- 分支: `feature/recording-playback`
- 相关文件:
  - `src/chongming/scenarios/playback.py` (新增)
  - `src/chongming/scenarios/source.py` (新增)
  - `src/chongming/preview.py` (修改)
