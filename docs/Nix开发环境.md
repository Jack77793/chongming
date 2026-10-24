# Nix Flake 开发环境

Flake 为 `x86_64-linux`、`aarch64-linux`、`aarch64-darwin` 提供可编辑开发环境和运行包。图形行为仍需在目标宿主环境验证。

## 开发环境

需要已安装 Nix，并启用 `nix-command` 和 `flakes`。首次使用需联网下载依赖。在仓库根目录执行：

```sh
nix develop
chongming-center --scenario scenarios/center_clean.json --check
```

环境提供 Python 3.12、Godot **4.7.2 stable**、运行/开发依赖，以及 uv、Git、treefmt、nixfmt 和 Prettier。Python 依赖由 uv2nix 读取 `uv.lock`；Nixpkgs 和构建工具由 `flake.lock` 固定。Godot 版本有精确断言，不会静默升级。

项目可编辑导入指向工作区 `src/chongming/`，修改 Python 源码无需重建；更改依赖或打包配置后需重新进入 shell。

shell 已设置 `CHONGMING_GODOT`，兼容 `uv run ...`，无需手工设置 Godot 路径。环境位于只读 Nix store，不使用仓库的 `.venv`；请跳过其他文档中的 `uv sync`，也不要在其中执行 `pip install` 或 `uv pip install`。

## 运行与构建

不必先进入开发 shell：

```sh
nix run .#preview -- --scenario scenarios/review.json
nix run .#center -- --scenario scenarios/center_clean.json --check
nix run .#scene -- --scenario scenarios/center_clean.json --output results/example --no-video
nix build
```

默认 `nix run .` 也是预览入口，构建结果位于 `result/bin/`。参数在 `--` 后传给原有 CLI，详见 [场景说明](../scenarios/README.md)。场景路径相对当前目录，输出目录必须尚不存在。

`nix run` 使用构建时的源码快照和固定的 Godot，不是开发 shell 的可编辑导入；未被 Git 跟踪的新文件不会自动进入 Flake 源码。

## 格式化与检查

统一格式化会修改工作区，也可只处理指定文件：

```sh
nix fmt
nix fmt -- flake.nix nix/check-environment.py
```

`treefmt.toml` 使用 nixfmt 格式化 Nix、Ruff 格式化 Python、Prettier 格式化 JSON、HTML、JS、CSS 和 Markdown，不执行 lint 自动修复。

生成目录、缓存和 Godot 文件不参与格式化；`scenarios/assets/` 中按原始字节校验的资产、生成锁文件也被排除，避免破坏摘要。直接使用 Prettier 时由 `.prettierignore` 保护这些文件。

只检查格式、不改写工作区：

```sh
system="$(nix eval --impure --raw --expr builtins.currentSystem)"
nix build --no-link -L ".#checks.${system}.treefmt"
```

该检查在沙箱源码副本中执行。当前 treefmt 没有 `--dry-run`；`--ci` 和 `--fail-on-change` 仍会改写输入文件，不能直接用作工作区 dry-run。

运行当前系统的全部无显示检查：

```sh
nix flake check --keep-going
```

检查覆盖依赖与资源、架构、场景、Ruff/mypy 和格式化，不包含需要图形环境的完整测试。跨平台只求值、不构建或运行检查时使用：

```sh
nix flake check --all-systems --no-build
```

## 图形环境与完整测试

实际场景运行需要可用的图形会话和 OpenGL 3.3 驱动，`--no-video` 和 `--no-browser` 不能免除这个要求。Nix 不安装宿主显卡驱动；非 NixOS Linux 可能需要另行配置驱动接入。不能使用 Godot `--headless`，其 dummy renderer 无法产生真实像素。

在原生图形会话运行完整测试：

```sh
nix develop -c python -m unittest discover -s tests -v
```

Linux 无桌面环境使用 Xvfb 与 Mesa/llvmpipe 软件渲染：

```sh
nix develop .#software -c xvfb-run -a python -m unittest discover -s tests -v
```

`software` shell 强制软件渲染，不改变默认 shell 的 GPU 选择；适合自动化测试，不能替代真实 GPU 性能或人工视觉验收。

## 维护与限制

- `uv.lock` 管 Python 依赖，`flake.lock` 管 Nix 输入。修改依赖版本需先取得维护者许可，不要为解决环境问题随意升级依赖或替换 Godot。
- 更新依赖后重新执行检查与完整测试；跨平台求值通过不等于各平台实际运行通过。
- 打包运行的 manifest 尚不能完整记录工作区 Git、源码和锁文件摘要，不应单独作为完整源码溯源证据。
