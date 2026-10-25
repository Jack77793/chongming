# 录制与回放功能实施方案

**生命周期**: 本文档仅在 `feat/recording-playback` 分支活跃期间有效,合并后删除。

**关联 Issue**: #15 - Overcome performance bottleneck with prefabricated scene recordings

## 背景

### 现状
- 虚拟场景实时渲染受性能限制,帧率约 10fps
- 影响演示直观性和开发调试效率
- 场景体积膨胀,管理不便
- 本机已安装 Godot 4
- Hannover 测例未妥善配置(JSON 手写困难,需可视化编辑)

### 目标
- 实现"离线录制 + 高帧率回放"机制
- 保持对外接口不变
- 兼容现有实时渲染模式
- 支持更复杂/拟真的预录制场景

## 设计方案

### 核心架构

```
┌─────────────────────────────────────────────────┐
│           ScenarioSource (Protocol)             │
├─────────────────────────────────────────────────┤
│  + next_step() -> ScenarioStep                  │
│  + get_spec() -> ScenarioSpec                   │
│  + is_finished() -> bool                        │
└──────────────┬──────────────────────────────────┘
               │
       ┌───────┴────────┐
       │                │
┌──────▼──────┐  ┌──────▼──────────┐
│   Live      │  │   Recorded      │
│ Scenario    │  │   Scenario      │
│ Source      │  │   Source        │
└─────────────┘  └─────────────────┘
  (现有逻辑)       (新增回放)
```

### 数据格式

**录像包结构**:
```
recordings/<name>/
├── manifest.json          # 元信息
├── scenario.json          # 原始定义(可选)
├── video/                 # PNG 序列(已有格式)
│   ├── camera-000/
│   └── camera-001/
├── online.jsonl           # 在线数据包
├── truth.jsonl            # 真值时序
├── truth-static.json      # 静态场景几何
└── timing.jsonl           # 性能指标
```

**manifest.json 结构**:
```json
{
  "format": "recording-v1",
  "recorded_at": "2026-10-26T...",
  "scenario_id": "...",
  "total_steps": 1234,
  "total_duration_ns": ...,
  "cameras": [...],
  "provenance": {...}
}
```

### 实施阶段

#### 第一阶段: 基础回放 (当前)
- [x] 方案文档
- [ ] 定义 `ScenarioSource` Protocol
- [ ] 实现 `RecordedScenarioSource`
- [ ] 录像包读取器 (`RecordingReader`)
- [ ] 命令行参数支持 (`--recording`)
- [ ] 基础测试与验证

#### 第二阶段: 增强功能 (后续)
- [ ] 优化录像格式(视频压缩)
- [ ] 录像剪辑工具
- [ ] 质量验证工具
- [ ] 性能基准测试

## 实现细节

### 1. Protocol 定义

`src/chongming/scenarios/source.py`:
```python
class ScenarioSource(Protocol):
    def next_step(self) -> ScenarioStep | None: ...
    def get_spec(self) -> ScenarioSpec: ...
    def is_finished(self) -> bool: ...
```

### 2. 回放源实现

`src/chongming/scenarios/playback.py`:
- `RecordingReader`: 读取录像包
- `RecordedScenarioSource`: 实现 `ScenarioSource`
- 按需解码 PNG,重建 `ScenarioStep`

### 3. 命令行集成

扩展现有入口点:
- `chongming-center`: 支持 `--recording` 代替 `--scenario`
- `chongming-preview`: 同上
- 新增 `chongming-record`: 专用录制工具(可选)

## 兼容性保证

- 现有 `--scenario` 模式完全保留
- 下游模块(中心端、预览)无感知
- 录像格式基于现有 evidence 输出,无额外转换

## 验证计划

1. **功能验证**: 回放录像与实时渲染输出一致
2. **性能验证**: 回放帧率达到 30-60fps
3. **接口验证**: 中心端算法正常运行
4. **预览验证**: Web 界面正常显示

## 注意事项

- 录像包可能体积较大(PNG 序列),未来考虑压缩
- 当前不实现录制工具,使用现有 `chongming-center --check` 生成 evidence 即可
- Hannover 测例配置暂不处理,等待可视化编辑器
