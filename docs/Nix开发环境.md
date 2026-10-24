# Nix Flake 开发环境

Flake 同时提供可编辑开发环境和固定源码的运行包。Python 依赖直接由 `uv2nix` 读取现有 `uv.lock`，不另写一份版本列表，也不在进入 shell 时运行 `uv sync`。

## 1. 支持范围与版本

提供以下三个系统的 `devShells`、`packages`、`apps` 和 `checks`：

- `x86_64-linux`
- `aarch64-linux`
- `aarch64-darwin`

这表示 Nix 输出已定义，不代表三种平台都已完成实际运行和人工视觉验收。三个系统均完成求值检查；`aarch64-linux` 完成构建、可编辑导入、打包命令运行，以及 Xvfb/Mesa 下的 85 项自动化测试。2026-10-24 另在 `aarch64-darwin` 原生 macOS 图形会话完成开发环境、打包命令和 85 项自动化测试，详见下方验证记录；`x86_64-linux` 仍需原生机器验证。

| 组件 | 版本来源 |
| --- | --- |
| Python 3.12 | `flake.lock` 固定的 Nixpkgs；满足项目 `>=3.12,<3.15` 和工具的 Python 3.12 目标 |
| NumPy、Pillow、Ruff、mypy 及间接 Python 依赖 | 原有 `uv.lock` 的精确版本与文件哈希 |
| Godot | Nixpkgs 中的 **4.7.2-stable**，另有求值断言防止静默升级 |
| uv、Git、nixfmt、Linux 的 Xvfb 与 Mesa | `flake.lock` 固定的 Nixpkgs |
| Hatchling 等构建后端，以及 editable 构建需要的 editables | `flake.lock` 固定的 `pyproject-build-systems` |

构建后端不在现有 `uv.lock` 的运行/开发依赖中，因此通过固定的构建系统输入提供。Flake 优先使用锁定 wheel，Nix 负责 Linux 动态库修补，无需系统级 pip 安装或手工设置 NumPy/Pillow 的库路径。

Nixpkgs URL 固定到提供目标 Godot 版本的提交；`flake.lock` 再固定全部输入和内容哈希。此次接入没有修改 `pyproject.toml` 或 `uv.lock`。

## 2. 前提

需要已安装的 Nix，并启用 `nix-command` 和 `flakes`。若尚未启用，可为单次命令加上：

```sh
nix --extra-experimental-features 'nix-command flakes' develop
```

首次使用需要联网下载 Flake 输入、Python wheel 和 Nix 包。之后在相关内容均已缓存的情况下可离线使用；本方案不依赖项目外的自定义二进制缓存配置。

默认图形运行仍需要可用的桌面会话和 OpenGL 驱动。Nix 不负责安装宿主机显卡驱动或创建桌面会话。非 NixOS Linux 上使用宿主机 GPU 时，可能还需管理员准备匹配的驱动接入；不应通过修改 Godot 版本绕过图形问题。

## 3. 进入开发环境

从仓库根目录执行：

```sh
nix develop

python --version
"$CHONGMING_GODOT" --version
chongming-center --scenario scenarios/center_clean.json --check
```

默认 shell 提供：

- 可编辑安装的项目、NumPy、Pillow 和 `uv.lock` 中的全部开发依赖；
- Godot 4.7.2 stable，自动设置 `CHONGMING_GODOT`；
- uv、Git 和 nixfmt；Linux 额外提供 `xvfb-run`。

`python`、`ruff`、`mypy` 和三个 `chongming-*` 命令都可直接使用；也兼容已有的 `uv run ...` 调用：

```sh
uv run chongming-center --scenario scenarios/center_clean.json --check
python -c 'import chongming; print(chongming.__file__)'
```

导入路径应指向工作区的 `src/chongming/`，修改 Python 源码不需要重建环境。更改依赖、入口或打包元数据后，应重新进入 shell。

shell 通过 `REPO_ROOT` 定位 Git 工作区，并清除继承的 `PYTHONPATH`。请先进入本仓库，不要从其他仓库运行 `nix develop /path/to/chongming`。无 Git 的源码目录需从包含 `pyproject.toml` 的根目录启动。

### 不要在 Nix shell 内重新同步虚拟环境

开发环境位于只读 Nix store，不创建或依赖仓库中的 `.venv`。shell 设置：

- `UV_NO_SYNC=1`：`uv run` 不再同步依赖；
- `UV_FROZEN=1`：运行时不重新解析或改写锁文件；
- `UV_PYTHON_DOWNLOADS=never`：不下载另一套 Python；
- `UV_PYTHON`、`UV_PROJECT_ENVIRONMENT`、`VIRTUAL_ENV`：指向 Nix 环境。

因此，已有文档中的 `uv sync` 在 Nix 路线下应跳过。也不要向此环境执行 `pip install` 或 `uv pip install`。原来的非 Nix、`.venv` 开发方式仍然保留，两条路线不需要相互覆盖。

## 4. 直接运行与构建

不必先进入开发 shell：

```sh
# 默认入口也是 preview；需要图形环境
nix run . -- --scenario scenarios/review.json
nix run .#preview -- --scenario scenarios/photography.json --no-browser

# 只校验 JSON，不需要显示环境
nix run .#center -- --scenario scenarios/center_clean.json --check

# 中心端交互工具，默认无视频且暂停，但仍启动 Godot
nix run .#center -- \
  --scenario scenarios/center_clean.json \
  --output results/nix-center-test

# 有限场景导出；--no-video 仍需图形上下文
nix run .#scene -- \
  --scenario scenarios/center_clean.json \
  --output results/nix-scene-test \
  --no-video
```

参数在 `--` 后传给原有 CLI，语义与 [场景说明](../scenarios/README.md) 相同。场景路径相对当前工作目录解析，输出目录必须尚不存在。`nix run` 使用构建时的源码快照，而不是 devShell 的 editable 导入；本地未被 Git 跟踪的新文件不会自动进入 Flake 源码。

运行包装器固定使用 Nix 提供的 Godot，无需依赖宿主机的同名命令或手工设置 `CHONGMING_GODOT`。

```sh
nix build
./result/bin/chongming-center --scenario scenarios/center_clean.json --check
```

可用包：`chongming` / `default`、`godot`、`python-env`（仅运行依赖）和 `dev-tools`（含锁定的开发依赖）。`result` 构建链接和 Godot `.godot/` 缓存已加入忽略规则。

## 5. Linux 无桌面测试：软件渲染 shell

Linux 另有 `software` shell，显式提供 Mesa/llvmpipe 的库和驱动路径并强制软件渲染，不改变默认 shell 的 GPU 选择：

```sh
nix develop .#software
xvfb-run -a python -m unittest discover -s tests -v
```

也可单次执行：

```sh
nix develop .#software -c \
  xvfb-run -a python -m unittest discover -s tests -v
```

这里是 Xvfb 提供的虚拟显示加真实 OpenGL 软件渲染，**不是 Godot `--headless` 的 dummy renderer**。仅使用 `xvfb-run` 而未提供可用 OpenGL 驱动，仍可能启动失败。软件渲染适合自动化测试，不适合据此报告实际 GPU 性能。

`--no-video` 只关闭像素生产，`--no-browser` 只关闭浏览器自动启动，两者都不能取代显示/驱动准备。macOS 不提供这个 Linux 专用 shell，使用原生图形会话。

## 6. 检查

```sh
# 格式化 flake.nix；也可通过 nix fmt -- --check flake.nix 只检查
nix fmt

# 求值三个系统的全部输出，不构建或运行检查
nix flake check --all-systems --no-build

# 构建并运行当前系统的无显示检查
nix flake check --keep-going
```

Flake checks 包含：

- `environment`：核对已安装包与 `uv.lock` 的全部版本、Godot 版本及打包后的 Godot/Web 资源；
- `architecture`：7 项无显示架构测试；
- `scenarios`：校验仓库内全部场景 JSON；
- `lint`、`format`、`types`：原有 Ruff / mypy 命令；
- `nix-format`：Nix 格式检查。

完整 85 项测试需要图形上下文，因此不作为普通 `nix flake check` 的一部分。在原生图形会话运行：

```sh
nix develop -c python -m unittest discover -s tests -v
```

**接入时的已知基线问题：**原有 Python 文件有 10 个不符合锁定 Ruff 的格式要求，测试目录有 54 项 mypy 错误。因此 `format` 和 `types` 检查当前失败，`nix flake check` 不会全绿。本次未降低检查要求、升级工具或修改这些无关文件；它们需要另行修复。`--no-build` 通过只代表求值成功，不代表上述检查已经执行。

### aarch64-darwin 验证记录（2026-10-24）

实测环境为 Apple Silicon、macOS 26.7.1、Nix 2.34.9；Nix 提供 Python 3.12.15 和 Godot `4.7.2.stable.nixpkgs.ed1daf0bf`。使用现有锁文件，没有升级依赖。

| 验证项 | 结果 |
| --- | --- |
| `nix flake check --all-systems --no-build` | 三平台输出全部求值通过 |
| `nix build .#chongming .#dev-tools --no-link --print-out-paths` | 本机运行包与开发工具构建成功 |
| `nix develop`、shell 内 `uv run` | 成功；项目导入指向工作区 `src/chongming/`，使用 Nix Python 和锁定工具 |
| `nix fmt -- --check flake.nix` | 通过 |
| `nix flake check --keep-going` | `environment`、`architecture`、`scenarios`、`lint`、`nix-format` 通过；`format`、`types` 因上述基线问题失败 |
| `nix develop -c python -m unittest discover -s tests -v` | 85 项测试全部通过，无跳过，耗时约 31 秒；包含原生 Godot 图形与植被测试 |
| `nix run .#center`、`.#scene`、`.#preview` | 三个入口及实际运行通过，细节如下 |

打包运行使用 `center_clean.json` 的临时副本，将时长缩短到 0.08 秒，保留两站、30 FPS 和原有目标。有限场景运行产生 3 个逻辑组、12 个相机帧和 6 条射线；启用视频时导出 12 张可解码、像素非均匀的 RGB PNG，主辅分辨率分别为 1920×1080 和 640×480。停用视频时逻辑帧和射线计数一致，没有 PNG 或绘图请求。中心工具运行到有限终点，保存输入/真值证据，全部模块正常关闭。

预览实际启动本地 HTTP 服务，首页、主辅 PNG 接口和暂停控制通过，发送 SIGINT 后退出码为 0。还在传入无效 `CHONGMING_GODOT` 的情况下验证了打包入口：包装器能覆盖该值并使用 Nix 固定的 Godot。上述检查覆盖自动化图形运行，不代表已完成人工画质或所有桌面窗口行为验收。

**打包证据的已知限制：**`nix run .#scene` 输出的 manifest 中，`code_revision`、`worktree_dirty`、`dependency_lock_digest` 为 `null`，`source_digests` 为空。当前 [`_provenance()`](../src/chongming/scenarios/run.py) 按 Python 模块位置寻找源码根目录，而安装包位于 Nix store，无法据此找到工作区、Git 元数据和 `uv.lock`。Godot 版本、平台和已安装依赖版本仍有记录；源码与锁文件的追溯信息需要另行完善打包元数据传递，不能将当前 manifest 视作完整的源码溯源证据。

## 7. 依赖维护

- `uv.lock` 管 Python 运行/开发包；`flake.lock` 管 Nixpkgs、Python 解释器、uv、Godot 和构建工具输入。
- 本次新增 `flake.lock`，没有更新原有锁文件。不要为解决构建问题顺手执行 `uv lock --upgrade` 或替换 Godot。
- 修改依赖版本前先取得维护者许可，再有意修改相应声明/锁文件并审查 diff；不要在只读 devShell 中同步依赖。
- 更换 Nixpkgs 时仍必须满足 Godot 精确版本断言。若确需改变该要求，应先讨论项目规范与跨平台验证，不能只改断言让构建通过。
- 更新后重新执行三平台求值、环境版本检查、完整测试和图形人工验收；只能在具备对应原生或远程构建机时声称其构建通过。
