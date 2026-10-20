"""Render baseline/enhanced/occluded views with one process and report warm costs."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

from chongming.contracts import scene as s
from chongming.modules.scene import (
    CameraProjectionModule,
    SceneLogicModule,
    TrajectoryModule,
    VisibilityModule,
)
from chongming.rendering.godot import GodotRenderer
from chongming.scenarios.demo import state
from chongming.scenarios.serialization import load_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/photography-review"))
    parser.add_argument("--samples", type=int, default=6)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("samples must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    spec = load_scenario(Path("scenarios/photography.json"))
    logic = SceneLogicModule(TrajectoryModule(), CameraProjectionModule(), VisibilityModule())
    logic.initialize(spec.scene_definition, spec.perturbations, spec.random_seed)
    scene = logic.sample(s.SimulationTime("photography-review", 1_000_000_000, 30))
    camera = replace(scene.cameras[0], actual_intrinsics=replace(
        scene.cameras[0].actual_intrinsics,
        image_width_px=1024, image_height_px=576,
        focal_x_px=1024, focal_y_px=1024, principal_x_px=511.5, principal_y_px=287.5,
    ))
    baseline = replace(
        scene,
        illumination=replace(
            scene.illumination, atmospheric_visibility_m=None,
            sun_angular_diameter_deg=0.0, glare_strength=0.0,
        ),
        clouds=(),
        render_effects=s.RenderEffectsSpec(
            vegetation_lod_near_m=scene.render_effects.vegetation_lod_near_m,
            vegetation_lod_far_m=scene.render_effects.vegetation_lod_far_m,
        ),
    )
    no_optics = replace(
        scene,
        illumination=replace(scene.illumination, glare_strength=0.0),
        render_effects=replace(
            scene.render_effects, fxaa_enabled=False, god_rays_strength=0.0
        ),
    )
    # Put an opaque dark panel exactly between camera and solar disk; all effects
    # must respect the rendered depth, with no analytic sun pasted over geometry.
    direction = scene.illumination.sun_direction_enu_unit
    panel_position = tuple(camera.optical_state.position_enu_m[k] + direction[k] * 80 for k in range(3))
    panel = s.WorldObjectSpec(
        s.WorldObjectId("sun-blocker"), "sun blocker", s.WorldObjectKind.OBSTACLE,
        "review", state(panel_position), s.BoxGeometry((3, 3, 3)),
        s.SurfaceAppearance((0.03, 0.03, 0.03), 0.8), True,
    )
    occluded = replace(scene, world_objects=scene.world_objects + (panel,))
    montage = Image.new("RGB", (2048, 1208), "white")
    report = {}
    renderer = GodotRenderer()
    try:
        for index, (name, view) in enumerate((
            ("baseline", baseline), ("atmosphere", no_optics),
            ("photography", scene), ("sun-occluded", occluded),
        )):
            raw, first_ms = renderer.render(view, camera)
            image = Image.frombytes("RGB", (1024, 576), raw)
            image.save(args.output / f"{name}.png")
            x, y = index % 2 * 1024, index // 2 * 604
            montage.paste(image, (x, y + 28))
            ImageDraw.Draw(montage).text((x + 10, y + 8), name, fill="black")
            times = [renderer.render(view, camera)[1] for _ in range(args.samples)]
            report[name] = {"first_request_ms": round(first_ms, 2),
                            "warm_p50_ms": round(statistics.median(times), 2),
                            "warm_max_ms": round(max(times), 2)}
        montage.save(args.output / "comparison.png")
        (args.output / "timing.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        diagnostics = renderer._diagnostics()
        if "SCRIPT ERROR" in diagnostics or "ERROR:" in diagnostics:
            raise RuntimeError(diagnostics)
    except Exception:
        print(renderer._diagnostics(), file=sys.stderr)
        raise
    finally:
        renderer.close()


if __name__ == "__main__":
    main()
