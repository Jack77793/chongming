"""Render repeatable vegetation review views through the system's Godot worker.

Only semantic tree configuration and camera poses are authored here; all plant
geometry and leaf instances are generated in Godot. Run from the repository root.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import replace
from pathlib import Path

from PIL import Image

from chongming.contracts import scene as s
from chongming.modules.scene import (
    CameraProjectionModule,
    SceneLogicModule,
    TrajectoryModule,
    VisibilityModule,
)
from chongming.rendering.godot import GodotRenderer
from chongming.scenarios.authoring import compile_authoring, demo_authoring
from chongming.scenarios.demo import optical_rotation, state


def review_snapshot() -> s.LogicalSceneSnapshot:
    document = demo_authoring()
    document["world_objects"] = []
    document["vegetation"] = [
        {
            "id": name,
            "position_enu_m": position,
            "height_m": height,
            "crown_radius_m": radius,
            "sway_amplitude_m": 0.25,
        }
        for name, position, height, radius in (
            ("review-oak", [0, 0, 0], 9, 2.7),
            ("review-oak-b", [-6, 5, 0], 7.8, 2.4),
            ("review-oak-c", [5.5, 7, 0], 10.5, 3.0),
        )
    ]
    spec = compile_authoring(document)
    logic = SceneLogicModule(TrajectoryModule(), CameraProjectionModule(), VisibilityModule())
    logic.initialize(spec.scene_definition, spec.perturbations, spec.random_seed)
    scene = logic.sample(s.SimulationTime("vegetation-review", 1_000_000_000, 30))
    return replace(
        scene,
        world_objects=(),
        flying_objects=(),
        illumination=s.IlluminationSpec((-0.5, -0.6, 0.8), 100_000, (0.42, 0.63, 0.86)),
        terrain=replace(scene.terrain, appearance=s.SurfaceAppearance((0.17, 0.20, 0.09), 0.9)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/vegetation-review"))
    parser.add_argument("--samples", type=int, default=12)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("samples must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    scene = review_snapshot()
    renderer = GodotRenderer()
    try:
        report = {}
        for name, origin, aim in (
            ("tree", (10.0, -15.0, 5.5), (0.0, 0.0, 4.4)),
            ("grove", (17.0, -27.0, 8.0), (0.0, 3.0, 4.0)),
            ("leaves", (3.2, -4.3, 6.2), (0.0, 0.0, 6.0)),
        ):
            dx, dy, dz = (aim[k] - origin[k] for k in range(3))
            camera = replace(
                scene.cameras[0],
                optical_state=state(
                    origin, optical_rotation(math.atan2(dx, dy), math.atan2(dz, math.hypot(dx, dy)))
                ),
                actual_intrinsics=replace(
                    scene.cameras[0].actual_intrinsics,
                    image_width_px=1024,
                    image_height_px=768,
                    focal_x_px=1050.0,
                    focal_y_px=1050.0,
                    principal_x_px=511.5,
                    principal_y_px=383.5,
                ),
            )
            raw, first_ms = renderer.render(scene, camera)
            Image.frombytes("RGB", (1024, 768), raw).save(args.output / f"{name}.png")
            times = [renderer.render(scene, camera)[1] for _ in range(args.samples)]
            report[name] = {
                "first_request_ms": round(first_ms, 2),
                "warm_p50_ms": round(statistics.median(times), 2),
                "warm_max_ms": round(max(times), 2),
                "samples": len(times),
            }
        # Same process, camera and lighting: expose graphics-session overhead
        # rather than attributing all wall-clock time to plant geometry.
        empty_scene = replace(scene, vegetation=())
        renderer.render(empty_scene, camera)
        control_times = [renderer.render(empty_scene, camera)[1] for _ in range(args.samples)]
        report["empty_control"] = {
            "warm_p50_ms": round(statistics.median(control_times), 2),
            "warm_max_ms": round(max(control_times), 2),
            "samples": len(control_times),
        }
        (args.output / "timing.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        diagnostics = renderer._diagnostics()
        if "SCRIPT ERROR" in diagnostics or "ERROR:" in diagnostics:
            raise RuntimeError(diagnostics)
    finally:
        renderer.close()


if __name__ == "__main__":
    main()
