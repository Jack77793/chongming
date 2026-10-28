"""Godot 4 thin CLI wrapper for scenario execution.

Phase 1.3 refactoring: Python delegates scenario execution to Godot's GDScript
controller (main.gd, scene_controller.gd). All scene control, object motion, and
time-stepping logic now resides in Godot.

For interactive rendering (preview, development), the legacy socket-based
GodotRenderer is re-exported from godot_worker for backward compatibility.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Re-export legacy socket-based renderer for backward compatibility
# Used by preview.py, development.py, and modules/scene.py
from chongming.rendering.godot_worker import (
    GODOT_VERSION,
    GodotRenderer,
    close_default_renderer,
    default_renderer,
    projected_footprints,
    quantize_rgb8,
    render_rgb8,
    srgb_to_linear,
)

__all__ = [
    "GODOT_VERSION",
    "GodotRenderer",
    "check_scenario",
    "close_default_renderer",
    "default_renderer",
    "projected_footprints",
    "quantize_rgb8",
    "render_rgb8",
    "run_scenario",
    "srgb_to_linear",
]


def _find_godot_executable() -> str:
    """Locate Godot 4.7.2 stable executable."""
    configured = os.environ.get("CHONGMING_GODOT")
    if configured:
        return configured
    for name in ("godot4", "godot"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Godot 4 executable not found; set CHONGMING_GODOT or install godot4")


def check_scenario(scenario_path: Path) -> dict[str, Any]:
    """Validate scenario via Godot CLI without execution.

    Args:
        scenario_path: Path to scenario JSON file

    Returns:
        Validation result with metadata from Godot's --mode check

    Raises:
        subprocess.CalledProcessError: If Godot validation fails
    """
    godot = _find_godot_executable()
    main_script = Path(__file__).parent / "godot" / "main.gd"

    if not main_script.exists():
        raise FileNotFoundError(f"Godot CLI entry point not found: {main_script}")

    result = subprocess.run(
        [
            godot,
            "--headless",
            "--script",
            str(main_script),
            "--",
            "--scenario",
            str(scenario_path),
            "--mode",
            "check",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    # Parse Godot's structured output
    validation = {"valid": True, "stdout": result.stdout, "stderr": result.stderr}
    for line in result.stdout.splitlines():
        if "Total steps:" in line:
            validation["total_steps"] = int(line.split(":")[-1].strip())
        elif "Duration:" in line:
            validation["duration_s"] = float(line.split(":")[-1].strip().replace("s", ""))
        elif "FPS:" in line:
            validation["fps"] = float(line.split(":")[-1].strip())

    return validation


def run_scenario(
    scenario_path: Path,
    output_dir: Path,
    *,
    render_every: int = 1,
    no_video: bool = False,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Execute finite scenario via Godot CLI.

    All scene control, motion evaluation, and time-stepping happens in GDScript.
    Python only invokes Godot and validates output.

    Args:
        scenario_path: Path to scenario JSON
        output_dir: Output directory (must not exist)
        render_every: Render every Nth frame (default: 1, all frames)
        no_video: Skip pixel rendering (default: False)
        timeout: Maximum execution time in seconds (None for unlimited)

    Returns:
        Execution result with manifest from output_dir/manifest.json

    Raises:
        FileExistsError: If output directory exists
        subprocess.CalledProcessError: If Godot execution fails
        FileNotFoundError: If manifest.json not created
    """
    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")

    godot = _find_godot_executable()
    main_script = Path(__file__).parent / "godot" / "main.gd"

    if not main_script.exists():
        raise FileNotFoundError(f"Godot CLI entry point not found: {main_script}")

    args = [
        godot,
        "--headless",
        "--script",
        str(main_script),
        "--",
        "--scenario",
        str(scenario_path),
        "--output",
        str(output_dir),
        "--mode",
        "finite",
        "--render-every",
        str(render_every),
    ]

    if no_video:
        args.append("--no-video")

    subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)

    # Read Godot's output manifest
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Godot did not create manifest: {manifest_path}")

    manifest = json.loads(manifest_path.read_text())
    return {"status": "complete", "manifest": manifest, "output_dir": output_dir}
