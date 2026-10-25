# 用户指南

## 安装

源码使用仓库的 Nix／uv 环境，见[开发指南](development.md#环境与依赖)。本指南的源码命令在开发 shell 和源码根目录执行。

用户发行归档包含 wheel、sdist、带哈希的运行依赖清单、场景及文档。先在发布文件所在目录执行 `sha256sum -c SHA256SUMS`，再解压用户归档；此完整检查需要校验清单中的全部文件。仅下载用户归档时，应将其 SHA-256 与可信发布渠道提供的值核对。

在解压后的用户目录中安装普通 wheel。以下为 POSIX shell 命令，需要 uv 及 Python 3.12 至 3.14：

```sh
uv venv --python python3 .venv
uv pip install --python .venv/bin/python --require-hashes --no-deps --requirements requirements.txt
uv pip install --python .venv/bin/python --no-deps ./chongming-*-py3-none-any.whl
uv pip check --python .venv/bin/python
source .venv/bin/activate
chongming-center --scenario scenarios/center_clean.json --check
```

普通安装不使用 `uv run` 自动同步源码项目。用户目录没有开发脚本和 Flake，需要开发环境时先解压随附 sdist。NixOS 用户应在源码的 Nix shell 内安装和运行，以取得 Python wheel 所需的动态库。依赖安装可能联网，实验运行不下载地景数据。

实际运行另需 Godot 4.7.2 stable 标准版、图形会话及 OpenGL 3.3。设置 `CHONGMING_GODOT` 指向可执行文件，或在 PATH 提供 `godot4`／`godot`；Nix shell 已配置。Windows 的环境路径与激活方式不同，本指南的安装命令和发行检查以 POSIX 环境为准。

下文采用源码形式 `uv run --frozen <command>`；普通安装激活环境后直接执行 `<command>`。场景路径相对当前目录，资产路径默认相对场景文件目录，包内资源不依赖工作目录。

## 命令

| 命令                | 用途                                  |
| ------------------- | ------------------------------------- |
| `chongming-preview` | 浏览器监视、场景编辑与 Godot 自由视角 |
| `chongming-center`  | 场景检查、中心算法同步分步开发        |
| `chongming-scene`   | 有限虚拟场景及证据导出                |
| `chongming-osm`     | 本地 OSM XML 清洗与地景导入           |

各命令的完整参数见 `--help`。`chongming-center --check` 不启动渲染、不创建输出；实际运行即使关闭视频也会启动 Godot。运行输出目录必须尚不存在。

### 预览与编辑

```sh
uv run --frozen chongming-preview --scenario scenarios/review.json
```

默认服务地址为 <http://127.0.0.1:8765/>，仅监听本机。`--port 8766` 更换端口，`--no-browser` 不自动打开浏览器，Ctrl+C 关闭服务和渲染进程。`--render-fps 8` 限制墙钟生产节拍并降低像素请求频率，不改变实验时间戳；`--no-video` 停止像素生产，保留逻辑与事件。

从 authoring JSON 启动后，编辑模式取得独占 token 并暂停实验。面板可编辑站点、目标及地景选择；只有提交才原子写回场景文件。提交前检查磁盘字节摘要，文件被外部修改时拒绝覆盖。撤销重新读取磁盘，关闭服务放弃未提交草稿。

Godot 自由视角使用独立窗口：右键拖动调整视线，按住右键时 WASD 平移、Q/E 升降、Shift 加速。编辑期间连续绘制草稿，但实验保持暂停；非编辑期间随离散场景步更新。浏览器轮询不推进实验时间，视锥和标签不混入传感器图像。

省略 `--scenario` 可查看内置演示，但不能持久化编辑或使用 `--landscape-asset`。预览会评估已生成的辅助图像并显示标定结果，不调用中心工具的算法插件。

### 中心算法开发

```sh
uv run --frozen chongming-center --scenario scenarios/center_clean.json --check
uv run --frozen chongming-center --scenario scenarios/center_clean.json --output results/center-test
```

默认暂停、无视频、不启动 HTTP 服务。交互命令：

| 命令                     | 行为                                              |
| ------------------------ | ------------------------------------------------- |
| `step`                   | 完成一个曝光中点组及中心调用                      |
| `run 10` / `run`         | 连续十步／运行到有限终点；Ctrl+C 在完整步骤后暂停 |
| `video on` / `video off` | 恢复／停止像素生产，不暂停逻辑                    |
| `status`                 | 查看状态                                          |
| `reset`                  | 关闭当前会话，在新目录创建新会话和算法实例        |
| `quit`                   | 关闭资源并退出                                    |

`--run --tick-hz 10` 可直接连续运行，节拍只影响墙钟速度。默认 `InputInspector` 检查配置、校准引用、序号与射线，不计算位置或轨迹。

受信任的 Python 工厂可通过 `--algorithm my_center:factory` 接入。工厂返回实现 `ports.development.CenterAlgorithm` 的对象：

- `start(registration)` 接收在线站点配置、搜索盒、时钟和中心会话。
- `process(step)` 接收校准解、普通事件和主相机流进度，同步返回 `(measurements, tracks)`。
- `close()` 释放资源。

先登记校准解，再消费引用它的事件和完成进度；有限末尾另有 `stream_ended` 步。已有 `FusionEngine` 可通过 `FusionEngineAdapter` 接入。JSON 不加载 Python 代码，算法不得读取真值文件或完整场景配置。

### 有限导出与输出

```sh
uv run --frozen chongming-scene --scenario scenarios/empty.json --output results/scene-test --no-video
```

省略 `--no-video` 输出 PNG 序列；`--render-every N` 按各相机独立曝光计数抽取像素，默认每帧绘制。所有逻辑曝光仍被访问，跳过的帧记录 ID 和原因。

| 文件                           | 内容                                          |
| ------------------------------ | --------------------------------------------- |
| `authoring.json`               | 输入的可编辑场景，输入为 authoring 格式时保存 |
| `scenario.json`                | 有限导出的编译场景，用于诊断                  |
| `manifest.json`                | 运行范围、配置、会话、渲染策略及来源记录；通过 `scenario_path` 和摘要引用场景 |
| `online.jsonl`                 | 普通帧元数据、事件及像素跳过记录              |
| `truth-static.json`            | 运行期间不变的特权场景对象快照，仅供评估      |
| `truth.jsonl`                  | 逐步真实状态及事件到目标的对应；静态对象通过摘要引用，仅供评估 |
| `timing.jsonl`、`summary.json` | 逐步耗时、计数、结束或失败状态                |
| `video/camera-*/`              | 按流编号保存的 PNG，不以用户 ID 构造路径      |

中心工具另保存 `center-registration.json`、`center-input.jsonl` 和 `center-output.jsonl`。完整 manifest 与真值是审计资料，不是中心算法输入。失败时保留已写证据，不自动清理输出目录。

`truth.jsonl` 使用 `truth-jsonl-v2` 布局。每条记录的 `logical_scene_static_ref` 指向同目录的 `truth-static.json`；评估读取器会校验摘要并将静态 `world_objects` 恢复到内存中的完整场景快照。运行期间若静态对象发生变化，运行会失败而不会静默生成错误真值。

manifest 的 `installation` 区分 `editable` 与 `installed`；`package_digests`／`package_digest` 描述实际加载的包文件。Git 提交、工作区状态和 `uv.lock` 摘要仅在可获取的 editable 源码环境记录，普通 wheel 安装对应字段为 `null`。包摘要不是完整源码溯源，发行包的校验和与源码快照应另行保留。

## 场景配置

人工编辑格式为 `scenario-authoring-v2`。`load_scenario()` 编译该格式；`scenario-json-v1` 是诊断用运行图，由 `load_compiled_scenario()` 显式读取。二者用途不同。

示例均位于发行包或源码的 `scenarios/`：

| 场景                                         | 用途                             |
| -------------------------------------------- | -------------------------------- |
| `center_clean.json`                          | 双站单目标、30 秒中心输入        |
| `constant_velocity.json`、`empty.json`       | 交叉直线运动、无目标             |
| `heightfield.json`、`heightfield_asset.json` | 内嵌与外部山脊网格               |
| `review.json`、`photography.json`            | 三类形状、摄影环境与相机抖动     |
| `osm.json`、`osm_landscape.json`             | 合成建筑及完整地景，不是真实地图 |

坐标为 ENU 东／北／上，距离单位米，时间单位秒。相机系为右／下／前。基础字段如下，未知字段或非法值会报错：

| 字段                                                  | 约束或用途                                                                                                               |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `format`、`scenario_id`                               | 格式及场景标识                                                                                                           |
| `random_seed`                                         | 固定为 42，可省略                                                                                                        |
| `duration_s`、`fps`                                   | 正时长，默认 24 秒；采样率默认 30 Hz，每站支持 1 至 60 Hz                                                                |
| `stations[]`                                          | 1 至 4 站，唯一 `id`、`position_enu_m`；可选 `main_yaw_deg`、`main_elevation_deg`、`auxiliary_yaw_deg`、`fps`、`start_s` |
| `targets[]`                                           | 唯一 ID、`geometry`、`motion`；`[]` 表示无目标                                                                           |
| `geometry`                                            | `sphere` + `radius_m`、`box` + `half_extents_m`，或 `aircraft`                                                           |
| `motion`                                              | `constant_velocity` + 位置/速度，或 `orbit` + 中心/半径/周期，可选相位及垂直振幅                                         |
| `world_objects[]`                                     | 静态物体；`[]` 清空内置地物，仍保留各站 A4 标志物                                                                        |
| `terrain`                                             | `flat` 或 `heightfield`；网格使用原点、间距、内嵌高度或外部资产                                                          |
| `vegetation[]`、`wind`                                | 植被类型及尺寸、平均风和湍流；`[]` 清空植被，`null` 关闭风                                                               |
| `search`                                              | `min_enu_m`、`max_enu_m`、`step_enu_m`；搜索盒外目标仍可产生射线                                                         |
| `environment`、`camera_jitter`、`clouds`、`rendering` | 可选摄影、抖动、云及渲染效果                                                                                             |
| `camera_position_offset_enu_m`                        | 对真实主辅光心共同施加位移，不修改在线安装配置，用于视差评估                                                             |
| `osm`                                                 | 本地 `asset` 引用与可选 `vegetation` 策略                                                                                |

省略地物、目标、植被或风字段时可能保留演示默认值；空场景应显式使用空数组及 `wind: null`。主相机为 1920×1080、对角 FOV 60°；辅助相机为 640×480、水平 FOV 2.6°，标称水平朝向 10 m 外与站点 ID 绑定的横向 A4 棋盘。方位角 0° 向北，正向东；显式抖动可使瞬时辅助姿态偏离水平。输出为无 alpha 的 sRGB RGB8。

### 摄影与植被

`photography.json` 提供可运行配置。太阳方向 `sun_direction_enu_unit` 在编译时归一化；天空色为 0 至 1 的线性 RGB，照度非负。正值 `atmospheric_visibility_m` 启用大气透视；`sun_angular_diameter_deg` 范围 0 至 5，默认 0；`glare_strength` 范围 0 至 2，默认 0。

`camera_jitter` 使用 `angular_amplitude_deg`（右／下／前轴）、`pixel_amplitude_px`（水平／竖直）、正值 `frequency_hz` 和 `roles`（`main`／`auxiliary`）。角幅度与按焦距换算的像素幅度叠加，直接改变物理姿态，不以图像平移模拟。`camera_position_offset_enu_m` 与旋转抖动分别表达平移和旋转。

`clouds` 最多两层，各有正值 `layer_altitude_m`、0 至 1 的 `optical_density` 和 `drift_velocity_enu_mps`。太阳、大气和云是未标定的视觉近似，不改变普通射线的几何可见性。雨、气象雾、通信扰动及滚动快门尚未实现。

`rendering` 可配置地表细节、SSAO、边缘抗锯齿和光束：`terrain_detail_strength` 为 0 至 1，默认 0.35；`ssao_intensity` 为 0 至 4，`god_rays_strength` 为 0 至 2，二者默认 0。`fxaa_enabled` 为布尔值，默认关闭；当前抗锯齿为四邻域边缘混合，不是 Godot 原生 FXAA。

植被公共字段为 `id`、`position_enu_m`，可选外观和遮挡开关：

| `type`         | 尺寸及参数                                                            |
| -------------- | --------------------------------------------------------------------- |
| `tree`（默认） | `height_m`、`crown_radius_m`，可选摆幅和频率                          |
| `shrub`        | `height_m`、`radius_m`                                                |
| `grass`        | `size_m`，可选 `blade_height_m`、`density_per_m2`；默认不遮挡逻辑视线 |

植被使用随包分发的 CC0 卡片资产，包括 12 个乔木、6 个灌木／绿篱和 12 个草／地被变体，无需运行时下载。品种按世界坐标噪声与确定性哈希选择，逻辑遮挡仍使用圆柱代理。

乔木按投影高度切换 LOD：约 45 px 以下使用无阴影、无动画的交叉 billboard；较大尺寸使用完整卡片网格，阴影和风摆分别在约 75 px、60 px 以下关闭。灌木也随屏幕尺寸关闭阴影和风摆；乔木与灌木最终在 `vegetation_lod_far_m` 后剔除，草在 `vegetation_lod_near_m` 后剔除，要求 `far > near > 0`。切换不渐变，未实现全局合批；每个草 patch 最多 256 个草簇。

近景树冠、灌木和草按实验时间、平均风及确定性湍流做整体平移／缩放，树干静止；不模拟枝叶级形变或真实风场。

## JSON 资产

外部高度网格及 OSM 地景均使用以下引用形式：

```json
{
  "uri": "assets/ridge.json",
  "content_sha256": "6744c8b5dd9c1494b5828de16938651560686bd7b9e155a1a3fd73acfce4bd03"
}
```

URI 支持本地相对路径、绝对路径和不含主机的 `file:` URI，不接受远程地址、查询或片段。默认以场景文件目录为资产根；Python API 可显式指定 `asset_root`。

高度资产形如 `{"heights_m": [[0, 1], [2, 3]]}`，至少为 2×2 矩形，数值有限且非负。`origin_enu_m`、正值 `spacing_m` 留在场景中；行从南到北，列从西到东，高度相对基础高程，网格外使用基础平面。

在源码目录计算内容摘要：

```sh
uv run --frozen python scripts/hash_json.py scenarios/assets/ridge.json
uv run --frozen chongming-center --scenario scenarios/heightfield_asset.json --check
```

真实内容修改后，将第一条命令输出的摘要更新到引用并校验场景；仅排版无需更新。普通 wheel 用户可使用公开函数 `content_sha256(parse_json(path.read_bytes()))`，二者位于 `chongming.scenarios.json_content`。

内容身份采用项目自定义规则，不是 RFC 8785/JCS：

- 规范字节为紧凑 UTF-8 JSON，无 BOM、尾换行；对象键按 Unicode 码点排序，使用 Python `ensure_ascii=False` 转义规则。
- 数组顺序和字符串码点保持不变，不执行 Unicode 归一化。
- `1`、`1.0`、`1e0` 等价，所有带符号零归为 `0`；所有整数值限于 `±(2**53 - 1)`。
- 数字转为 binary64 及其最短十进制写法后必须保持原十进制值；拒绝精度损失、溢出和下溢，不静默舍入。
- 拒绝重复键、非有限数、孤立代理码点及非法 JSON/UTF-8。摘要为规范字节的 64 位小写十六进制 SHA-256。

加载器单次读取、解析并验证，消费者使用同一份已验证值。源码/下载文件、发行包、图像和编辑并发检测仍使用字节摘要；不能用 `sha256sum` 的文件摘要替代 `content_sha256`。

## OSM 地景

导入仅读取固定本地 OSM XML 0.6，输出 `osm-landscape-v2`，导入器为 `chongming-osm-2`。`--source-url` 仅记录来源，不发起网络请求。PBF 如需使用，应先在准备阶段转换并保存原文件摘要及转换命令。

合成 fixture 的离线导入示例：

```sh
uv run --frozen chongming-osm scenarios/assets/osm_demo.osm \
  --config scenarios/assets/osm_import.json \
  --source-url 'repository:scenarios/assets/osm_demo.osm (synthetic fixture)' \
  --data-date 2026-10-20 --license CC0-1.0 \
  --attribution 'Synthetic fixture, CC0-1.0; not an OpenStreetMap extract' \
  --output output/osm/demo.buildings.json --cleaned-output output/osm/demo.cleaned.osm
```

报告默认将输出文件的扩展名替换为 `.report.json`，如 `demo.buildings.report.json`，含 `asset_content_sha256`、源 XML 与清洗 XML 的字节摘要、配置、诊断、统计和耗时。将资产路径及报告的内容摘要写入 `osm.asset`；完整合成地景也可由 `scripts/prepare_osm_demo.py` 生成，其 stdout 提供 `content_sha256`。

配置明确 WGS84 锚点、基准历元、椭球高、west/south/east/north 包围框及高度策略。导入结果经 ECEF 转为局部 ENU；运行场景与站点使用同一锚点，手写坐标不自动重投影。

- 建筑支持闭合、无自交的单外环和平顶挤出。高度依次取 `height`、`building:levels × level_height_m`、配置默认值，并记录回退。跨界建筑、关系建筑、内洞和 building parts 不生成任意补面。
- 道路按中心线和标签/类别宽度生成路面，不生成交通图；桥、隧道和非零抬升层拒绝并记录诊断。
- 树点、森林、绿地和水体可导入；自然地表支持有效 multipolygon 及内洞。缺失关系成员整体拒绝，道路和地表裁剪到选区。
- 清洗 XML 是本导入器的重导入输入，不是完整 OSM 区域备份；保留原始下载文件及其摘要。

可选 DEM 参数包括 `--dem-hgt`、`--dem-tile`、`--dem-source-url`、`--dem-data-date`、`--dem-license`、`--dem-attribution`、`--dem-spacing`。支持本地 1201²／3601² 大端 HGT 或 gzip，按 EGM96 正高处理；相对锚点 `reference_height_m` 生成地形，默认间距 60 m。范围外、void 和超过 250000 样本的网格拒绝。复合 DEM 不等于裸地测量，贴地水体不代表真实水位。

`osm.vegetation` 控制额外乔木、灌木及草 patch。森林基础树及 OSM 树点计入目标密度，不重复种植；新增实例排除建筑、道路、水体、已有树冠和 DEM 陡变。主要默认值为：森林／绿地乔木 200／12 株每公顷，灌木 55／30，草 patch 35／40；`seed` 为 42，尺寸、团簇及预算字段见源码 `OSMVegetationConfig`。预算仅约束新增实例，策略属于场景，不改变 OSM 资产内容摘要。

候选排序叠加低频密度噪声形成斑块，约 16% 的新增乔木按确定性哈希缩小为幼树／下木；相同资产与配置的生成结果可重复，不依赖墙钟。

### Hannover 固定预设

仓库只保存选区配置 `scenarios/assets/hannover_500m_import.json`，不分发真实样区 JSON 或下载文件。启用该编辑预设时，必须显式提供已经准备的资产：

```sh
uv run --frozen chongming-preview --scenario scenarios/review.json \
  --landscape-asset /path/to/hannover/landscape-dem.json
```

该选项只接受固定 Hannover 内容，不是任意地景入口；启动及选用时校验。内容摘要为：

```text
d02d6a1e8437374b4842f91c66a04b0cfb3da522c3e7747e8e5b298d5eb61388
```

选区中心为 `(52.38 N, 9.77 E)`，约 1×1 km，配置位于 `scenarios/assets/hannover_500m_import.json`。来源、日期和原始文件字节摘要保存在资产中。在线数据会变化，重新下载或导入不保证命中固定预设。任意合法本地地景应通过场景的 `osm.asset` 引用；没有匹配资产时不要修改预设摘要绕过校验。

### 数据许可

仓库的 `osm_demo.osm`、`osm_landscape_demo.osm` 及其合成资产标注为 CC0-1.0，不是真实 OSM 提取。真实 OSM 数据通常适用 [ODbL-1.0](https://opendatacommons.org/licenses/odbl/1-0/)，需保留 [OpenStreetMap contributors 署名](https://www.openstreetmap.org/copyright)，并遵守 [OSMF attribution guidelines](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines)。转换为网格不自动消除来源许可义务。

Mapzen/Skadi 的 HGT 格式及来源条件见 [格式说明](https://github.com/tilezen/joerd/blob/master/docs/formats.md) 和 [署名说明](https://github.com/tilezen/joerd/blob/master/docs/attribution.md)。Hannover DEM 保留以下提供方署名，不能将复合来源统一标记为 SRTM 公有领域：

> Mapzen terrain tiles; Europe terrain produced using Copernicus data and information funded by the European Union - EU-DEM layers; global GMTED2010 and SRTM courtesy of USGS.

展示或分发地景时保留资产中的署名与许可；再分发前按实际数据来源及发布方式核对条件。未经授权的本地输入不得进入仓库或发行包。
