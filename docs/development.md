# 开发指南

## 环境与依赖

在源码根目录使用仓库提供的 Nix 和 uv：

```sh
nix develop
uv sync --frozen
```

Nix 提供 Python 3.12、uv、Godot 4.7.2 stable、Ruff、treefmt 和 Git；Linux 另提供 Xvfb。`flake.lock` 固定 Nixpkgs，`uv.lock` 固定 Python 依赖。Python 包要求为 `>=3.12,<3.15`，开发环境使用 3.12。Hatchling 是构建后端，包位于 `src/chongming/`。

uv 将运行及开发依赖安装到 `.venv/`，默认以 editable 方式安装本项目。日常使用 `uv run --frozen …`，不重新解析或修改锁文件。Nix 设置 `UV_PYTHON_DOWNLOADS=never`，解释器由 Nix 提供。Ruff 使用 shell 中的 `ruff`，不要通过 `uv run ruff` 替换为可能无法在 NixOS 启动的 PyPI 二进制。

未经维护者授权，不执行依赖升级、`uv add`、`uv lock` 或 Nix 输入更新。获准变更时只调整必要依赖，审查锁文件差异并运行相关检查。环境故障应先检查解释器、动态库和图形驱动，不以升级依赖作为默认修复。

Nix shell 为 manylinux wheel 设置 `LD_LIBRARY_PATH`。在 shell 外直接使用 `.venv/bin/python`，可能因缺少 `libstdc++.so.6` 等动态库而失败。首次准备环境及构建隔离环境可能联网；锁文件不等于离线依赖缓存。

Flake 为 `x86_64-linux`、`aarch64-linux`、`aarch64-darwin` 提供 shell 和 formatter，不提供 `nix build` 包或 `nix flake check` 检查项。`nix run . -- --scenario scenarios/review.json` 是预览的 uv 包装入口，须在源码目录运行。

## 图形环境

实际运行使用 Godot 4.7.2 stable 标准版和 Compatibility 后端，需要图形会话及 OpenGL 3.3 驱动。`CHONGMING_GODOT` 指定可执行文件；Nix shell 已设置该变量。Godot `--headless` 使用 dummy renderer，不能生成本项目所需像素。`--no-video`、`--no-browser` 均不免除引擎初始化要求。

Linux 无桌面环境可使用软件渲染：

```sh
nix develop .#software --command xvfb-run -a uv run --frozen chongming-scene \
  --scenario scenarios/empty.json --output results/software-example --no-video
```

`software` shell 配置 Mesa/llvmpipe，不安装宿主显卡驱动。非 NixOS 的 GPU 驱动接入需要按宿主环境配置。软件渲染验证不能替代真实 GPU 性能测量或人工视觉检查；Flake 的平台声明不保证宿主图形环境可用。

## 检查

先执行与改动相关的测试，涉及公共契约、运行或打包时再运行完整回归。在开发 shell 内：

```sh
ruff check src tests scripts
uv run --frozen mypy src tests
uv run --frozen python -m unittest tests.test_architecture -v
for scenario in scenarios/*.json; do
  uv run --frozen chongming-center --scenario "$scenario" --check
done
```

完整测试需要图形环境：

```sh
uv run --frozen python -m unittest discover -s tests -v
```

无桌面 Linux 可从源码根目录执行：

```sh
nix develop .#software --command xvfb-run -a uv run --frozen python \
  -m unittest discover -s tests -v
```

检查失败时区分本轮引入的问题与原有问题，并报告原因及未验证项。不要通过放宽超时、弱化断言或返回假结果使检查通过。

统一格式化入口为 `nix fmt`，由 `treefmt.toml` 调度 nixfmt、Ruff 和 Prettier：

```sh
nix fmt
nix fmt -- docs/development.md
nix fmt -- --no-cache --fail-on-change
```

最后一条命令仍会改写不合规文件，并以失败状态报告改动，不是只读检查。不要用 `--excludes` 临时追加规则，它会覆盖配置中的全局排除项；选择文件时直接传路径。

格式化涵盖 Python、Nix、JSON、Web 文件及 Markdown，不执行 lint 自动修复。缓存、输出、锁文件及不匹配的原始 OSM/HGT、Godot 文件不参与格式化。JSON 资产排版不会改变内容摘要；具体规则见[用户指南](user-guide.md#json-资产)。

## 源码与修改边界

| 路径                                                   | 职责                               |
| ------------------------------------------------------ | ---------------------------------- |
| `src/chongming/contracts/`                             | 不可变数据结构、单位、坐标与状态   |
| `src/chongming/ports/`                                 | 算法和生命周期接口                 |
| `src/chongming/modules/`、`composition/`               | 模块实现、架构桩和依赖装配         |
| `src/chongming/scenarios/`                             | 场景编译、资产、OSM 导入、运行证据 |
| `src/chongming/rendering/`、`web/`                     | Godot 资源、渲染适配与预览页面     |
| `src/chongming/calibration.py`、`joint_calibration.py` | 辅助视觉标定与独立真值评估         |
| `scenarios/`                                           | 可编辑示例与小型固定资产           |
| `tests/`、`scripts/`                                   | 回归测试、实验与发行工具           |

优先实现当前功能，保持简单调用关系和明确的数据所有权。不为假想需求新增框架、兼容层或兜底，不重复实现已有边界校验。性能优化以测量为依据；原生后端只替换热点，不改变契约。协作约束见 [AGENTS.md](../AGENTS.md)。

实验结果写入 `output/` 或 `results/`，临时验证放入 `tmp/`；不得覆盖已有结果或将它们纳入发行包。源码文件引用均以上表中的源码树为准；用户归档内的源码位于随附 sdist，需解压后查看。

## 本地发行

工具只构建和验证本地文件，不上传、打标签或创建远程发布。在开发 shell 内使用独立输出目录：

```sh
uv run --frozen python scripts/distribute.py build --output dist/release
uv run --frozen python scripts/distribute.py check --output dist/release --render
```

无图形环境时可省略 `--render` 完成安装与预览 HTML 检查，但这不验证 Godot 运行。Linux 软件渲染的完整检查：

```sh
nix develop .#software --command xvfb-run -a uv run --frozen python \
  scripts/distribute.py check --output dist/release --render
```

按 `pyproject.toml` 的项目版本生成五个文件：

| 文件                                   | 内容                                                                             |
| -------------------------------------- | -------------------------------------------------------------------------------- |
| `chongming-<version>.tar.gz`           | sdist，含源码、测试、脚本、场景、指南、`AGENTS.md`、锁文件、构建配置及 `COPYING` |
| `chongming-<version>-py3-none-any.whl` | 从 sdist 构建的包、Web/Godot 资源、命令入口和许可证，不含根目录场景及指南        |
| `chongming-<version>-user.tar.gz`      | 所有 Markdown、场景及资产、`COPYING`、sdist、wheel 和 `requirements.txt`         |
| `requirements.txt`                     | 从 sdist 的锁文件导出的运行依赖及哈希，不含项目本身与开发依赖                    |
| `SHA256SUMS`                           | 以上四个文件的字节摘要                                                           |

`SHA256SUMS` 位于输出目录，不在用户归档内；分发用户归档时应同时提供其校验值。摘要只能检查完整性，不证明发布者身份。版本号不代表已经公开发布。

构建可分为 `--stage sdist`、`wheel`、`bundle`；后两步分别要求已有 sdist、已有 sdist 与 wheel。重试时使用同一源码快照，源码改变后从 sdist 重新构建。输出目录只能包含本版本预期的五个普通文件；工具拒绝其他内容，不自动清理目录。

检查可选 `--stage contents` 或 `install`，默认 `all` 另从 sdist 重建 wheel 并逐文件比较。完整检查覆盖：

- 归档路径、文件清单、校验和、许可证、包资源和命令入口。
- 临时环境中的普通 wheel 安装、带哈希依赖及 `uv pip check`。
- 独立工作目录下的导入、全部发行场景、预览 HTML；`--render` 另检查真实绘图和有限运行。
- 运行前后包文件摘要一致，不依赖源码目录或向安装包写入缓存。

发行工具按 POSIX 的 `bin/python` 布局执行安装检查；Windows 原生发行验证尚未支持。文档或许可证改名时，同步更新 `pyproject.toml`、`scripts/distribute.py` 和 `tests/test_distribution.py`。发布前确认当前检查结果、许可与资源收录范围。

## 实验复查

在开发 shell 和图形会话中运行，输出目录应按实验分别命名：

```sh
uv run --frozen python scripts/review_photography.py --output output/photography-review --samples 6
uv run --frozen python scripts/review_vegetation.py --output output/vegetation-review --samples 12
uv run --frozen python scripts/review_osm.py --scenario scenarios/osm_landscape.json --output output/osm-review --samples 3
uv run --frozen python scripts/run_hannover_calibration.py --scenario scenarios/center_clean.json \
  --position-offset 0.005 0 0 --max-steps 3 --output output/calibration-review/report.json
```

最后一个脚本也可评估合成场景，不要求 Hannover 数据。它记录普通辅助图像的识别、姿态估计、状态机、墙钟及进程 CPU 耗时，估计完成后才读取真值计算误差。采样不足、没有有效估计与误差为零必须区分。

记录场景、分辨率、硬件、驱动、代码及依赖版本、首次请求和暖机样本。`gpu_ms` 是 Godot 内更新、同步、绘图及回读的墙钟时间；`gpu_submissions` 是像素请求数，不是 GPU 硬件计时或驱动提交数。自动测试检查几何、时间和状态关系，不维护跨 GPU 像素 golden。
