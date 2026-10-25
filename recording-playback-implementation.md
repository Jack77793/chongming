# 录制与回放功能实现

**分支**: `feature/recording-playback`  
**状态**: ✅ 第一阶段完成  
**创建时间**: 2026-10-26

## 概述

实现 issue #15 提出的"录制与回放"机制,以解决虚拟场景实时渲染性能瓶颈(~10fps)。通过离线录制高质量场景,然后高帧率回放,提升演示直观性和调试效率。

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

1. **帧率固定**: 回放帧率由录像时决定,无法在回放时调整
2. **真值简化**: `ScenarioTruthOutput` 中的 `camera_frames` 和 `online_correspondences` 为空元组(待完整反序列化)
3. **中心端未适配**: `chongming/__main__.py` 尚未添加 `--recording` 支持
4. **无视频压缩**: 使用 PNG 存储,占用空间较大

## 下一步计划

**第二阶段** (性能优化):
- [ ] 视频压缩支持(H.264/H.265)
- [ ] 回放速率控制(加速/减速)
- [ ] 完整真值数据反序列化

**第三阶段** (增强功能):
- [ ] 录像片段剪辑和拼接
- [ ] 录像质量验证工具
- [ ] 中心端 `--recording` 参数支持
- [ ] 录像元数据查询工具

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
