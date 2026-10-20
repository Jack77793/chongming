# OpenStreetMap 地景整合说明

状态：当前方案与后续实现约束。本文只定义虚拟场景的 OSM 地景方向，不新增主规范之外的总体系统需求；场景功能以根目录[虚拟场景需求清单](../虚拟场景需求清单.md)为准。

## 决策摘要

- 使用 OpenStreetMap (OSM) 的固定本地数据，优先降低建筑地景制作成本；首期只关注建筑外轮廓及其对画面和视线的影响。
- 借鉴 `godot-openstreetmap` 的 OSM 几何分类、瓦片/缓存思路和多边形挤出做法，不把它作为运行时插件或在线数据服务直接接入。
- 不采用 OSM2World/GLB 作为中转管线。导入与简化由本仓库掌控，保持数据可审计、可复现，并适配当前 Python 逻辑层单向控制 Godot 渲染层的边界。
- OSM 视觉建筑和 Python 遮挡代理必须从同一份固定输入及同一套变换参数派生；Godot 的三角网格不成为场景真值的权威来源。

当前仓库尚无 OSM 导入实现，也没有提交 OSM 数据。下列影响面是后续实现预期，不表示相关接口已经冻结。

## 参考资料

### godot-openstreetmap

参考仓库：[RodZill4/godot-openstreetmap](https://github.com/RodZill4/godot-openstreetmap)，本次检查固定在 `master` 提交 [`a3a9ca5c46ddcc956a7d4798ee835d749d711e42`](https://github.com/RodZill4/godot-openstreetmap/commit/a3a9ca5c46ddcc956a7d4798ee835d749d711e42)。仓库附带 [MIT License](https://github.com/RodZill4/godot-openstreetmap/blob/a3a9ca5c46ddcc956a7d4798ee835d749d711e42/LICENSE)。其 README 说明 simple demo 可运行但不稳定，其他 demo 有过时问题；它是实现参考，不是可直接依赖的生产组件。

| 入口 | 可借鉴内容 | 不直接照搬的部分 |
| --- | --- | --- |
| [`global/osm.gd`](https://github.com/RodZill4/godot-openstreetmap/blob/a3a9ca5c46ddcc956a7d4798ee835d749d711e42/addons/openstreetmap/global/osm.gd) | Web Mercator 瓦片坐标换算；解析 OSM XML 的 node/way/tag 并按建筑、道路、水体、草地等分类；将解析结果写入缓存 | Godot 3 时代的 API、隐式全局状态、随机扰动几何，以及未经本仓库验证的分类/默认高度规则 |
| [`scripts/map.gd`](https://github.com/RodZill4/godot-openstreetmap/blob/a3a9ca5c46ddcc956a7d4798ee835d749d711e42/addons/openstreetmap/scripts/map.gd) | 地图区域按 tile 组织、中心变化时复用 tile、把地表纹理生成拆为单独工作 | 面向持续移动游戏的动态瓦片地图；当前实验是固定场景，不需要在线流式地图 |
| [`scripts/tile3d.gd`](https://github.com/RodZill4/godot-openstreetmap/blob/a3a9ca5c46ddcc956a7d4798ee835d749d711e42/addons/openstreetmap/scripts/tile3d.gd) | 解析缓存格式、按建筑/道路/水体分派场景对象的组织方式 | 缓存缺失时会请求 OSM API，并使用项目自定义 TWM 二进制格式；这会引入联网、缓存状态和额外格式维护，不适合直接进入实验启动链路 |
| [`buildings.gd`](https://github.com/RodZill4/godot-openstreetmap/blob/a3a9ca5c46ddcc956a7d4798ee835d749d711e42/addons/openstreetmap/buildings.gd) | 根据 footprint 生成墙面、平屋顶或简化屋顶网格 | 复杂屋顶和修复策略并非首期所需；视觉网格不能替代逻辑遮挡代理 |

项目采用“OSM 输入 -> 解析/分类 -> 自有 tile 缓存 -> Godot 网格”的流程。值得借鉴的是数据流分层和建筑挤出，而不是它的运行时网络请求、自有缓存格式或游戏式连续瓦片加载。

### OSM 数据与许可

- [OpenStreetMap Copyright and License](https://www.openstreetmap.org/copyright)：数据归属与 ODbL 说明。
- [OpenStreetMap Foundation Attribution Guidelines](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines)：产品界面、图像和其他输出中的署名指导。
- [Open Data Commons Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/)：ODbL 正文。
- [Geofabrik 下载服务](https://download.geofabrik.de/)：可选的固定 PBF 区域提取来源之一；具体使用区域、下载日期和源文件摘要需随实验资产记录。

若仓库或发布包分发 OSM 数据、改编数据库或由其生成的数据库，应在分发前按 ODbL 评估义务。对外展示含 OSM 衍生地景的画面时，应保留适当的 OpenStreetMap attribution。具体数据集和发布方式的许可判断需要随实际来源确认，不能仅凭转换成几何文件就假定许可义务消失。

## 数据与实现要求

### 固定输入与复现

1. 实验运行不下载 OSM 数据，不调用 OSM API、Overpass 或瓦片服务。在线获取只能是开发者主动执行的离线准备步骤。
2. 每个 OSM 场景固定数据文件或固定提取版本，并记录来源 URL/提供方、数据日期、WGS84 区域边界、源文件 SHA-256、导入器版本、tag 映射/高度回退配置及坐标锚点。
3. 相同的输入摘要、配置和导入器版本必须产出相同的规范化几何与稳定 ID；不得依赖输入遍历顺序或未固定种子产生几何抖动。
4. 导入结果只允许仓库认可的本地资产 URI，并在解析前校验摘要。资产格式及是否保存中间产物由第一阶段 spike 决定；不得让场景 JSON 隐式引用会变化的远程地址。

### 首期地景范围

- 从 OSM building footprint 生成简化建筑墙体与平顶，先支持无自交、可稳定闭合的外轮廓；复杂关系、多内环、自交或损坏几何须确定性拒绝并在导入报告列明，不能静默生成任意网格。
- 高度优先使用可解释的 OSM 高度标签；缺失或异常值使用显式配置的确定性回退，并在资产/manifest 中记录回退策略。楼层数换算的层高不得沿用参考项目未经校准的默认值。
- 坐标由选定 WGS84 锚点投影到局部 ENU 米制坐标。投影、轴向、原点、裁剪边界及边界相交建筑的处理需固定并测试，避免渲染与逻辑的方向或尺度不一致。
- 首期不要求道路交通网、完整自然地表分类、POI、完整地形高程、屋顶细节、运行时在线瓦片、任意大区域流式加载或照片级建筑材质。
- 建筑来源和导入统计应可追溯；缺失建筑或标签并不代表现实中不存在该建筑，不能用 OSM 覆盖完整性做场景真值声明。

### Python / Godot 职责边界

建议数据路径：固定 OSM 文件 -> 离线导入/校验 -> 带版本和摘要的规范化建筑数据 -> Python 场景编译、ENU 几何与遮挡代理 -> 语义/网格快照单向发送给 Godot -> 渲染输出。

Python 持有已导入 footprint、稳定对象 ID、确定的高度/姿态和简化遮挡几何，并为真值可见性查询负责；Godot 只消费这些描述并绘制视觉建筑。不得由 Godot 独立重新查询 OSM、独立推导建筑高度或向 Python 回传另一套几何真值。视觉优化可以改变网格精度，不得改变用于真值的规范几何。

现有场景契约、Python 可见性求交和 Godot 渲染请求都支持通用三角网格，但尚无 OSM 建筑的规范表示或挤出实现。spike 应比较直接用挤出网格参与 Python 求交与由同一 footprint 构造轻量垂直棱柱/凸分解代理的成本和一致性；不能只把三角网格传给 Godot，而不同时建立逻辑侧遮挡几何。

## 预期影响面

| 区域 | 预期工作 | 影响边界 |
| --- | --- | --- |
| 场景入口 | 在 `src/chongming/scenarios/authoring.py` 增加 OSM 资产/锚点等语义配置；在 `serialization.py` 校验字段、版本与摘要 | 保持 JSON 是简单场景入口；无远程 URL 和任意代码入口 |
| 资产加载 | 复用并按需扩展 `src/chongming/scenarios/assets.py` 的本地 URI 与 SHA-256 校验；新增导入器/格式版本及诊断报告 | 离线准备可以使用单独工具；运行时只读已固定资产 |
| 几何与真值 | 在 `contracts/scene.py` 表达建筑来源/稳定 ID 和遮挡代理；在 `modules/scene.py` 的可见性路径加入经验证的多边形代理 | 这是 OSM 影响逻辑层的主要边界；摄影、时钟、三路出口语义不变 |
| 渲染 | 在 `rendering/godot.py` 传递建筑实体，`rendering/godot/worker.gd` 构建或复用网格与材质 | 渲染实现不能联网，也不能回写场景/实验时间 |
| 证据与可复现 | 在运行 manifest/场景证据中记录源摘要、边界、导入器和 tag/高度策略 | 让图像、真值与 OSM 来源能够关联复核 |
| 测试与示例 | 增加极小、许可清楚的固定 fixture；覆盖投影、闭合轮廓、失败几何、高度回退、遮挡及 Godot 绘制一致性 | 不把真实大城市数据集纳入普通单元测试或依赖网络 |

预期主要改动限制在 `src/chongming/scenarios/`、`src/chongming/contracts/scene.py`、`src/chongming/modules/scene.py`、`src/chongming/rendering/`、`tests/` 和一个专用离线导入工具/小型 fixture。预计不触及中心融合算法、设备端视觉算法、离散时钟规则和输出协议。若实现需要修改这些边界，应先说明原因并增加对应架构决策记录。

## 第一阶段 spike 验收

使用一个面积小、建筑标注有代表性的固定 OSM 样区，完成以下检查后再扩大范围：

1. 输入边界、来源、日期、许可证说明和 SHA-256 可从配置/manifest 查到；断网重复运行能加载同一结果。
2. 投影后的建筑位置、朝向和米制尺寸通过已知控制点/手工抽样核验；相同输入重复导入产生相同摘要或规范化内容。
3. 常见闭合建筑轮廓正确挤出；不支持的几何、缺失高度和异常标签都有稳定、可解释的报告及回退结果。
4. 至少设置一个确定的相机—建筑—目标共线用例：Python 遮挡真值与渲染建筑位置一致；建筑以外的标签/视觉细节不会改变伪装包和真值规则。
5. 测量导入耗时、首次启动耗时、单帧耗时、网格数量和内存；据此决定是否需要瓦片或建筑合批，而不是预先照搬游戏式动态瓦片系统。
6. OSM 衍生资产及展示的 attribution/ODbL 处置已记录；未确认的再分发条件不随意打包原始数据。

## 风险

OSM 建筑覆盖率和高度标签因地点而异；多边形洞、关系、边界截断和坐标基准容易导致几何错误；整批建筑的绘制和可见性求交可能影响运行成本；不同数据提取日期会造成外观变化；派生数据库的 ODbL 条件需结合分发方式判断。这些风险通过小区域 spike、确定性拒绝/回退规则、输入摘要和共享几何来源控制，不以完整复刻某个现实地点为验收目标。
