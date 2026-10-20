# OSM2World 与 Godot 地景管线调研

调研日期：2026-10-19。结论基于 OSM2World 官方文档及源码 `f6518b35117cf7864e9c6f68c3b303a006e27b8f`、Godot 4.7.2 文档和当前虚拟场景架构。

状态说明：这是早期备选路线的调研记录。后续决定不采用 OSM2World，主要考虑其工具链和通用模型格式中转；当前方向改为参考 [godot-openstreetmap](https://github.com/RodZill4/godot-openstreetmap)，优先从固定 OSM 数据构建建筑外轮廓。本文保留为技术背景，不代表当前实施方案。

## 结论

OSM2World 可以显著减少道路、建筑、道路标线、屋顶、基础设施等静态地景的制作成本，并且可与 Godot 直接衔接。推荐把它作为**离线资产编译器**：固定一份 OSM 输入和 OSM2World 版本/配置，导出 GLB，校验摘要后交给 Godot 加载；运行虚拟场景时不启动 Java、不过网络请求。

不建议让 OSM2World 成为运行时服务。它的转换过程与 OpenStreetMap/Overpass、Java 工具链、样式素材、地理投影和较重的几何构建有关；加入每次实验启动会增加不确定性、延迟和依赖面。当前官方 `latest` 二进制下载约 473 MB，也不适合内嵌为仓库运行时依赖。

OSM 数据不能替代受保护真值：导出的网格适合画面，但中心视线遮挡仍需要 Python 拥有稳定的简化几何代理。首个集成应选择建筑/道路为主的小区域，继续由场景 JSON 显式配置站点、飞行器和植被。

## OSM2World 如何工作

1. 输入 OSM XML/PBF、Overpass、MBTiles、GeoDesk 或 Overpass JSON；`convert` 命令还可以按经纬度边界或瓦片裁剪。
2. 按 OSM tag 生成语义对象和网格，例如地面、道路、建筑/屋顶、路面标线、设施、植被和电力设施；未知高度等字段使用配置默认值。
3. 使用局部米制地图投影。内部坐标是 `X=东、Y=上、Z=北`，1 单位约等于 1 m；源码也提供局部切平面 ENU 投影。
4. 按配置选择样式、纹理、海拔计算和 LOD（0–4），输出 OBJ、glTF、GLB、图像或自己的 PBF。GLB 输出转换为 `X=东、Y=上、Z=-北`，恰好与当前 Godot 世界映射一致。

基本命令形式如下，具体可执行文件包装方式取决于官方发行包：

```sh
osm2world convert \
  --input area.osm.pbf \
  --input_bbox "lat1,lon1 lat2,lon2" \
  --lod 2 \
  --config standard.properties \
  --output area.glb
```

OSM2World 也能输出按瓦片和 LOD 组织的 GLB tileset，但当前虚拟场景通常只有固定小范围，第一版用单个裁剪 GLB 更易复现。大场景确认单 GLB 成为内存或绘制瓶颈后，再考虑分块和距离流式加载。

## 与 Godot 的边界

Godot 原生推荐 glTF 2.0，并能导入 `.gltf`/`.glb`；OBJ 也可用，但材质能力较弱。OSM2World 的 GLB 因此是首选交换格式。当前 worker 没有 Godot 编辑器导入步骤，最终实现宜用 `GLTFDocument`/`GLTFState` 在启动时从已校验本地文件导入，生成 Node3D 后缓存，而不是把 GLB 放进 `res://` 依赖 `.godot/imported` 状态。

建议资产描述包含：

```json
{
  "terrain_asset": {
    "kind": "osm2world_glb",
    "uri": "assets/site.glb",
    "sha256": "...",
    "origin_wgs84": [31.5, 121.8, 0],
    "position_enu_m": [0, 0, 0]
  }
}
```

导入时必须检查 GLB 使用米制比例、局部原点与场景锚点一致、材质/纹理完整、面数与 draw call 上限、透明材质，以及 Godot 中东西/南北/上下方向。不要按目测手动旋转补偿；OSM2World GLB 已经是 Godot 对应的右手 `X/Y/-Z` 映射。

渲染层只接收资产 URI、摘要和局部变换。逻辑层另外从同一 OSM 区域生成或保存低复杂度的建筑盒体/道路边界等代理，让图像、发包规则和真值有可审计关系。OSM2World 网格本身不参与逐三角形在线真值求交。

## 风险与取舍

- OSM 的三维标签覆盖不均。很多建筑只有 footprint，没有可靠高度、屋顶或材质；它适合快速获得“合理城市”，不能保证照片级复刻真实地点。
- 地形海拔是独立问题。OSM2World 支持 SRTM/海拔处理，但需要固定数据来源、版本与缺失值策略；首个 spike 可保留当前平地或自有 heightfield。
- OSM2World 程序本体采用 MIT 许可；默认样式仓库说明配置和附加纹理为 CC0/公有领域。OSM 数据受 ODbL 约束，输出/截图需要按 OpenStreetMap attribution 指引标注并保留来源、日期、裁剪边界与许可证证据。
- 官方 `latest` 会漂移。生产资产应固定 OSM2World 版本/提交、样式提交、Java 主版本、CLI 参数和输入摘要；生成出的 GLB 也保存 SHA-256。
- Godot 加载大型 GLB 的首次成本需要单独测量。OSM2World 自带 LOD 是导出复杂度等级，不等于 Godot 运行时自动 LOD；必要时应输出多个 LOD/瓦片并由 Godot 做距离选择。

## 推荐 spike

选择一个约 0.5–1 km²、建筑和道路较丰富但植被不多的 OSM 区域，固定 `.osm.pbf` 输入，导出 LOD 2 单 GLB。完成以下验收再正式接入：

- Godot 运行时成功导入，材质/纹理无丢失，方向、比例和 ENU 锚点正确。
- 与当前程序化地形、树木和天空共同绘制，无重复地面或 z-fighting。
- 1024×576 和主相机 1080p 分别测首次加载、暖机帧、内存、三角形和 draw call。
- 同一 GLB/配置两次运行产生相同场景结构；worker 不访问网络。
- 从 OSM footprint 构造的 Python 建筑代理与视觉建筑抽样对齐，并明确道路/细节是否参与遮挡。
- manifest 记录 OSM/样式/工具版本、区域、attribution 和所有摘要。

本轮未把 OSM2World 加入运行时，也未提交第三方资产。尝试下载官方 `latest` 二进制进行端到端探针时，约 473 MB 文件在 60 秒内只下载约 5 MB，因此没有完成真实 GLB 导入测试；源码已确认格式与坐标兼容，实际发行包/材质兼容性仍需上述 spike 验证。
