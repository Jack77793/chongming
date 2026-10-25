# Nix Flake 开发环境

Flake 为 `x86_64-linux`、`aarch64-linux`、`aarch64-darwin` 提供开发 shell 和格式化工具。图形行为仍需在目标宿主环境验证。

分工：Nix 提供 **Python 解释器**、**Godot 4.7.2 stable**、uv 和格式化工具；PyPI 上的运行与开发依赖由 `uv` 按 `uv.lock` 安装到仓库内的 `.venv/`。Flake 提供 `devShells`、`formatter` 与一个 `nix run` 预览入口，不提供 `nix build` 产物，也不提供 `nix flake check` 项。

## 开发环境

需要已安装 Nix，并启用 `nix-command` 和 `flakes`。首次使用需联网。在仓库根目录执行：

```sh
nix develop
uv run --frozen chongming-center --scenario scenarios/center_clean.json --check
```

环境提供：

- `python3` —— Nixpkgs 的 Python 3.12，`uv` 直接复用它（`UV_PYTHON_DOWNLOADS=never`，不会自行下载解释器）。
- `uv` —— 读取 `pyproject.toml` 与 `uv.lock` 安装依赖；`uv run` 会在需要时自动创建/更新 `.venv/`。
- `godot` —— Nixpkgs 的 `pkgs.godot_4_7`（4.7.2 stable），路径已写入 `CHONGMING_GODOT`。
- `ruff` —— **来自 Nixpkgs**：PyPI 的 ruff 是通用 Linux 二进制，在 NixOS 上无法启动（缺少 `/lib64/ld-linux-*`）。
- `treefmt`（nixfmt + Prettier + Ruff）、`git`、`xvfb-run`（仅 Linux）。

`LD_LIBRARY_PATH` 由 shell 设为 `gcc-lib` 与 `zlib` 的路径，供 PyPI 的 manylinux wheel 使用；缺少它时 `import numpy` 会报 `libstdc++.so.6: cannot open`。`.venv/` 已被 `.gitignore` 和 `treefmt.toml` 排除。

依赖解析输入（`exclude-newer = "3 days ago"`）写在 `pyproject.toml` 的 `[tool.uv]` 中，并已记录进 `uv.lock`；日常统一用 `uv run --frozen …`（不解析、不改写锁）。需要更新依赖时在 shell 内显式执行 `uv lock`、`uv add`。默认环境是仓库根目录下的 `.venv/`。

## 运行

```sh
uv run --frozen chongming-preview --scenario scenarios/review.json
uv run --frozen chongming-center --scenario scenarios/center_clean.json --check
uv run --frozen chongming-scene --scenario scenarios/center_clean.json --output results/example --no-video
uv run --frozen chongming-osm --help
```

不进入 shell 也可用 `nix run` 直接启动预览，它等价于在仓库根目录执行 `uv run --frozen chongming-preview`：

```sh
nix run . -- --scenario scenarios/review.json
nix run .#preview -- --scenario scenarios/review.json
```

`nix run` 只包装 `uv`：必须在仓库目录内运行（`uv run` 需向上找到 `pyproject.toml`），首次使用需联网。其他命令在 shell 内用 `uv run --frozen chongming-center|chongming-scene|chongming-osm …` 执行。

参数直接跟在命令之后，详见 [场景说明](../scenarios/README.md)。场景路径相对当前目录，输出目录必须尚不存在。`uv run` 有约 1–2 秒的启动开销；需要频繁调用时可在 shell 内先 `uv sync --frozen`，再用 `.venv/bin/python -m chongming.development …`。

## 格式化

统一格式化会修改工作区，也可只处理指定文件：

```sh
nix fmt
nix fmt -- flake.nix
```

`treefmt.toml` 使用 nixfmt 格式化 Nix、Ruff 格式化 Python、Prettier 格式化 JSON、HTML、JS、CSS 和 Markdown，不执行 lint 自动修复。

生成目录、缓存和 Godot 文件不参与格式化；`scenarios/assets/` 中按原始字节校验的资产、生成锁文件也被排除，避免破坏摘要。直接使用 Prettier 时由 `.prettierignore` 保护这些文件。

## 检查

flake 不提供 `checks`，检查在开发 shell 内执行：

```sh
nix develop --command bash -c '
  ruff check .
  uv run --frozen mypy src tests
  uv run --frozen python -m unittest tests.test_architecture -v
  for scenario in scenarios/*.json; do uv run --frozen chongming-center --scenario "$scenario" --check; done
  treefmt --ci --walk filesystem
'
```

`treefmt --ci` 会改写不合规文件并报告失败，因此不要在需要保留工作区内容时用它做 dry-run。跨平台只做求值可用 `nix flake show --all-systems`（不构建）。

## 图形环境与完整测试

实际场景运行需要可用的图形会话和 OpenGL 3.3 驱动，`--no-video` 和 `--no-browser` 不能免除这个要求。Nix 不安装宿主显卡驱动；非 NixOS Linux 可能需要另行配置驱动接入。不能使用 Godot `--headless`，其 dummy renderer 无法产生真实像素。

在原生图形会话运行完整测试：

```sh
nix develop --command uv run --frozen python -m unittest discover -s tests -v
```

Linux 无桌面环境使用 Xvfb 与 Mesa/llvmpipe 软件渲染：

```sh
nix develop .#software --command xvfb-run -a uv run --frozen python -m unittest discover -s tests -v
```

`software` shell 强制软件渲染，并在 `LD_LIBRARY_PATH` 中追加 Mesa；适合自动化测试，不能替代真实 GPU 性能或人工视觉验收。

## 维护与限制

- 依赖版本以 `uv.lock` 为唯一来源，跨 Nix/非 Nix 用户一致；Python 解释器和 Godot 由 `flake.lock` 固定的 Nixpkgs 决定。修改依赖或替换 Godot 版本需先取得维护者许可。
- `pyproject.toml` 的 `[tool.uv]` 固定 `exclude-newer = "3 days ago"`，即不采用发布不足三天的版本；修改该值后需重新执行 `uv lock`。
- 该方案依赖网络：干净检出后首次 `uv run` 需要 PyPI。`uv.lock` 保证内容哈希，但不提供 Nix store 级的离线获取。
- Nix 与 uv 的工具版本各管一段：`ruff` 来自 Nixpkgs，`mypy` 来自 `uv.lock`，两者补丁版本可能不同。
- wheel 与 NixOS 的动态链接差异集中在 `LD_LIBRARY_PATH`；若未来 wheel 要求更高 glibc（如 `manylinux_2_35`），旧系统会失败。
- 打包运行的 manifest 尚不能完整记录工作区 Git、源码和锁文件摘要，不应单独作为完整源码溯源证据。
