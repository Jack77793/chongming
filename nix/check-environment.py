"""Check the built environment against uv.lock without a display or network."""

from __future__ import annotations

import os
import subprocess
import tomllib
from importlib import metadata, resources
from pathlib import Path


def main() -> None:
    lock = tomllib.loads(Path("uv.lock").read_text())
    for package in lock["package"]:
        actual = metadata.version(package["name"])
        expected = package["version"]
        if actual != expected:
            raise RuntimeError(f"{package['name']}: expected {expected}, found {actual}")
        print(f"{package['name']}=={actual}")

    rendering = resources.files("chongming.rendering").joinpath("godot")
    for name in (
        "project.godot",
        "worker.tscn",
        "worker.gd",
        "vegetation.gd",
        "atmosphere.gdshader",
        "optics.gdshader",
        "terrain.gdshader",
    ):
        if not rendering.joinpath(name).is_file():
            raise RuntimeError(f"Missing packaged Godot resource: {name}")
    if not resources.files("chongming.web").joinpath("preview.html").is_file():
        raise RuntimeError("Missing packaged preview.html")

    from chongming.rendering.godot import GODOT_VERSION

    godot_version = subprocess.run(
        [os.environ["CHONGMING_GODOT"], "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if not godot_version.startswith(f"{GODOT_VERSION}.stable."):
        raise RuntimeError(f"Unexpected Godot version: {godot_version}")
    print(f"Godot {godot_version}; packaged resources OK")


if __name__ == "__main__":
    main()
