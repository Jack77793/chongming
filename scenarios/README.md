# 场景 JSON 与中心端开发

JSON 描述每次重新生成的世界、相机与目标；它不是运行时 `ScenarioSpec` 转储，也不包含中心算法或正确关联。志愿者可让 Agent 复制/编辑 JSON、校验后运行，无需改 Python。当前只提供干净射线，扰动/降级和图形编辑器后置。

## 运行

```sh
uv sync
uv run chongming-center --scenario scenarios/center_clean.json --check
uv run chongming-center --scenario scenarios/center_clean.json --output results/center-test
```

工具默认无视频、暂停，不启动 HTTP 服务。输入 `step` 完成一个曝光中点组及中心调用；`run 10` 连续十步，`run` 到有限终点，Ctrl+C 在完整步骤后暂停；`status` 查看状态，`video on|off` 在步间恢复/停止像素，`reset` 新建输出目录、会话和算法实例，`quit` 关闭所有资源。所有运行均启动 Godot 4.7.2 stable，并通过本地 socket 通信；需要图形会话，保持最小化窗口，关闭视频时不绘图。配置见 [运行说明](../docs/虚拟场景预览.md)。按主 1920×1080、辅 640×480 sRGB RGB8 输出，中心工具将视频保存为 PNG。

`--run --tick-hz 10` 可直接连续运行；节拍只控制墙钟速度，不改变曝光时间。输出目录必须不存在。每次启动完整装配与模块生命周期；尚未实现的业务在 manifest 中标明 `algorithm_unimplemented`。默认 `InputInspector` 只检查输入，不计算定位/跟踪，实际图像检测/标定与完整算法链路仍未实现。

## 可编辑字段

格式为 `scenario-authoring-v2`，坐标是 ENU 东/北/上、米，时间是秒。不支持格式兼容。

| 字段 | 用途 |
| --- | --- |
| `scenario_id / random_seed / duration_s / fps` | 标识、固定种子 42（可省略，其余值拒绝）、有限时长、1–60 Hz 实验采样；默认 24 秒、30 Hz |
| `stations[]` | 1–4 站，各有唯一 `id`、`position_enu_m`；可选 `main_yaw_deg`（0 向北、正向东）、`main_elevation_deg`、`auxiliary_yaw_deg`；辅助始终水平 |
| `stations[].fps / start_s` | 每站独立主辅采样率与首个曝光起点，默认继承全局 fps、从 0 开始 |
| `targets[]` | 任意数量，空数组表示无目标；每个目标有唯一 ID、geometry 与 motion |
| `geometry` | sphere + radius_m，box + half_extents_m，或 aircraft；目标和地物共用 |
| `motion` | constant_velocity + position_enu_m/velocity_enu_mps；或 orbit + center_enu_m/radius_m/period_s，可选 phase_rad/vertical_amplitude_m，支持多周期 |
| `world_objects[]` | 覆盖内置地物，`[]` 清空建筑，始终保留每站 A4 标志物；每项含 id、position_enu_m、geometry，可选 display_name、appearance、occludes_sight_lines |
| `terrain` | flat 或 heightfield + origin_enu_m/spacing_m；内嵌 heights_m 或外部 asset 二选一，高度行从南到北、每行从西到东，网格外为平面 |
| `search` | min_enu_m、max_enu_m、step_enu_m 三个向量，控制中心搜索盒；范围外目标仍可发射线 |
| `appearance` | 可选 linear_rgb 三个 0–1 数和 diffuse_reflectance；只影响外观 |

未写 `world_objects` 时保留内置建筑、球形地物和每站标志物；相机朝向未指定时采用演示朝向。主相机对角线 FOV 60°，辅助相机水平 FOV 2.6°、俯仰角 0，朝向 10 m 外自己的横向 A4 棋盘；标志物 ID 为 `<station-id>-a4`，角点与站配置绑定。仅在主视场内且中心视线未被地形、地物或其他飞行器挡住时发包。校准为理想有效状态，覆盖全部配置时长。

可对 Agent 说：“复制 center_clean.json，改成三站、两个交叉运动目标、运行 60 秒，清空地物，搜索盒缩小到使第二个目标在盒外；只编辑 JSON，然后执行 --check。”未知字段和非法值会报错，JSON 不加载 Python 代码。

现成场景：`center_clean.json` 双站单目标 30 秒；`constant_velocity.json` 双站交叉；`empty.json` 无目标；`heightfield.json` 山脊遮挡；`heightfield_asset.json` 同一山脊的外部资产版本；`review.json` 三类形状绕行。

较大的地形网格放在独立 JSON 中，场景只保留位置、间距和 `"asset": {"uri": "assets/ridge.json", "sha256": "…"}`。资产只含 `{"heights_m": [[0, 1], [2, 3]]}`：至少 2×2 的矩形有限非负高度，单位米、相对基础高程。原点和间距留在场景中，同一网格可复用。URI 相对场景文件目录解析，也支持本地绝对路径/file URI；不下载远程资产。Python 调用 `load_scenario` 可用 `asset_root` 指定素材目录。

编辑资产后执行 `shasum -a 256 scenarios/assets/ridge.json`，将完整的 64 位小写摘要填回场景，再用 `chongming-center --scenario scenarios/heightfield_asset.json --check` 校验。摘要不匹配、未知字段、非法网格在启动前报错；证据保留引用、摘要及编译后的几何。小网格仍可直接写 heights_m，不强制拆文件。

## 接入中心算法与答案参考

用受信任 Python 工厂接入：`--algorithm my_center:factory`，工厂返回 `ports.development.CenterAlgorithm`，实现 `start(registration)`、`process(step)` 和 `close()`。`process` 同步返回 `(measurements, tracks)`；工具等待返回后才接受下一步。`registration` 有在线站点配置、搜索盒、时钟与中心会话；`step` 有新校准解、普通事件/到达时刻、每个实际采样主流的完成进度。先注册解，再按顺序消费事件、进度；有限末尾还有单独的 stream_ended 步。已有 FusionEngine 可由工厂返回 `FusionEngineAdapter(engine)`。

`center-registration.json`、`center-input.jsonl` 是中心可见输入；`center-output.jsonl` 保存算法测量、轨迹和实际处理耗时。`truth.jsonl` 独立保存真实目标、理想射线和事件到目标的对应，供开发者/评估对答案，求解器不能读取它。manifest/authoring 同样只给编排和审计；无自动关联评分或定位算法。manifest 保存 Godot 完整版本、初始目标/相机元数据与场景配置。默认无视频运行的绘图请求为零，timing 仍记录每个逻辑组。历史字段 `gpu_ms` 是 Godot 内场景更新、绘图与回读墙钟耗时，`gpu_submissions` 是绘图请求数。

只查看逻辑总览可用 `uv run chongming-preview --scenario scenarios/center_clean.json --no-video --port 8878 --no-browser`，完成后 Ctrl+C 关闭。预览不调用志愿者中心算法，中心分步调试使用上面的工具；预览的“单步到下一可显示帧”与中心工具的实验组单步不同。

独立导出场景证据可用 `uv run chongming-scene --scenario scenarios/center_clean.json --output results/scene-test --no-video`；省略 --no-video 则输出完整标准图像。所有自动检查以几何、时间、事件、答案隔离和状态为主，不维护像素 golden。
