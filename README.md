# chongming

`chongming` 是一个低成本多摄像机系统的研究项目，目标是在白天可见光条件下，对小型空中目标进行二维检测、三维定位和时序跟踪。本仓库已进入虚拟场景实现阶段；实际检测、标定和中心融合链路仍待接入，尚未承诺特定端侧硬件、性能阈值或完整外场闭环。

## 系统概要

- 主相机面向天空，端侧输出二维候选、轨迹和世界射线事件。
- 辅助相机观察地面固定参照，用于端侧运行时校准；识别失败时冻结最后可信状态并明确标记过期。
- 中心端只在本地 PC 运行，接收世界射线并完成跨站关联、三维定位和三维临时 ID 跟踪。
- 虚拟环境的 Python 逻辑层与 Godot 4.7.2 渲染层分立，提供可重复的 3D 场景、主辅 sRGB 图像、虚拟事件、真值和可视化；Godot 随系统启动，按离散快照绘图。

日常主规范见 [`instruction.md`](instruction.md)。架构与执行摘要分别见 [`架构设计.md`](架构设计.md) 和 [`计划.md`](计划.md)；背景材料集中在 [`背景资料/`](背景资料/)。

虚拟场景唯一需求来源是 [虚拟场景需求清单](虚拟场景需求清单.md)。可编辑场景和运行命令见 [测试场景说明](scenarios/README.md)，预览、有限流证据与性能监测见 [虚拟场景预览](docs/虚拟场景预览.md)。

## 开发环境与运行

Nix Flake 提供 Python 3.12、Godot 4.7.2 stable 和格式化工具，PyPI 依赖由 `uv` 按 `uv.lock` 装进仓库内的 `.venv/`：

```sh
nix develop
uv run --frozen chongming-center --scenario scenarios/center_clean.json --check
```

也可不进 shell，直接 `nix run . -- --scenario scenarios/review.json` 启动预览（其余命令在 shell 内用 `uv run --frozen` 执行）。首次运行需联网。运行、格式化、检查和图形环境准备见 [Nix 开发环境](docs/Nix开发环境.md)。不使用 Nix 时执行 `uv sync`，并按 [虚拟场景运行说明](docs/虚拟场景预览.md) 准备 Godot 和图形环境。

## 实施共识

项目按三步推进：先用文档和 UML 完成预研究与模块边界；再用非运行式架构桩把模块、接口、数据结构、依赖和预期行为代码化；最后按计划实现真实逻辑，并每次从虚拟场景启动完整链路。第二阶段的“mock”不是可运行测试替身，不承担业务逻辑，也不要求系统在其参与下运行。三个月时间表仅用于顺利情况下的参考，不把未验证的时间安排当作承诺。

第二阶段架构桩使用 Python 与 `uv`；通用逻辑保持在 Python，视觉/几何算法先用 OpenCV 验证，性能热点再通过稳定端口替换为 Rust 或 C 后端。最终原生语言不在架构桩阶段预先冻结。

## 第三方素材

虚拟场景植被使用 [Shapespark Low-Poly Exterior Plants Kit](https://github.com/shapespark/shapespark-assets/tree/main/shapespark-low-poly-plants-kit)，以 **CC0 1.0 Universal** 释出；随仓库分发的文件、来源和修改说明见 [`src/chongming/rendering/godot/assets/README.md`](src/chongming/rendering/godot/assets/README.md)。OSM 数据和 DEM 的署名分别见 [OSM 地景整合说明](docs/OSM地景整合说明.md)。

## 许可

代码以 [GNU Affero General Public License v3.0](LICENSE) 发布。第三方依赖和素材仍受其各自许可证约束。

## 责任与风险声明

本项目具有被用于军事活动、武器系统集成、大规模监视或侵犯个人隐私的潜在风险。维护者不主动将本项目用于上述用途，也不鼓励他人如此使用；这是一项项目立场声明，不是 AGPLv3 的附加法律条款。

系统设计默认原始视频不离开设备端，但这不等于绝对隐私安全：设备端仍可能采集、缓存、记录或导出视频，部署者必须遵守适用的隐私、数据保护和现场拍摄法规，并取得必要授权。
