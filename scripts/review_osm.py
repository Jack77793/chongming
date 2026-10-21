"""Review a local OSM scene's geometry, visibility, real pixels and warm costs."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import textwrap
import time
import tracemalloc
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from chongming.contracts import model as m
from chongming.contracts import scene as s
from chongming.modules.scene import (
    CameraProjectionModule,
    SceneLogicModule,
    TrajectoryModule,
    VisibilityModule,
)
from chongming.rendering.godot import GodotRenderer
from chongming.scenarios.serialization import load_scenario


def rss_kib(pid: int) -> int | None:
    """Optional point-in-time resident memory (not peak or per-building cost)."""
    try:
        return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True).strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=Path("scenarios/osm.json"))
    parser.add_argument("--output", type=Path, default=Path("output/osm-review"))
    parser.add_argument("--samples", type=int, default=6)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("samples must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    tracemalloc.start()
    started = time.perf_counter()
    spec = load_scenario(args.scenario)
    load_ms = (time.perf_counter() - started) * 1000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    landscape = spec.scene_definition.osm_landscape
    if landscape is None:
        parser.error("scenario must reference an OSM landscape asset")
    credits = [landscape.imported.attribution]
    if landscape.imported.elevation is not None:
        credits.append(landscape.imported.elevation.attribution)
    credit_lines = [line for credit in credits for line in textwrap.wrap(credit, width=145)]
    footer_height = len(credit_lines) * 14 + 12
    logic = SceneLogicModule(TrajectoryModule(), CameraProjectionModule(), VisibilityModule())
    logic.initialize(spec.scene_definition, spec.perturbations, spec.random_seed)
    meshes = [
        o.geometry for o in spec.scene_definition.world_objects if o.category.startswith("osm-")
    ]
    terrain_mesh = spec.scene_definition.terrain.heightfield_geometry
    if terrain_mesh is not None:
        meshes.append(terrain_mesh)
    report: dict[str, Any] = {
        "scenario": str(args.scenario),
        "asset_sha256": landscape.asset_sha256,
        "provenance": asdict(landscape.imported),
        "compile_load_ms": load_ms,
        "compile_python_peak_bytes": peak,
        "landscape_meshes": len(meshes),
        "categories": {
            category: sum(o.category == category for o in spec.scene_definition.world_objects)
            for category in sorted({o.category for o in spec.scene_definition.world_objects})
        },
        "vegetation_objects": len(spec.scene_definition.vegetation),
        "vertices": sum(
            len(g.vertices_body_m) for g in meshes if isinstance(g, s.TriangleMeshGeometry)
        ),
        "triangles": sum(
            len(g.triangle_vertex_indices) for g in meshes if isinstance(g, s.TriangleMeshGeometry)
        ),
        "review_resolution": [1024, 576],
        "views": {},
    }
    started = time.perf_counter()
    renderer = GodotRenderer()
    report["godot_version"] = renderer.version
    report["godot_startup_ms"] = (time.perf_counter() - started) * 1000
    montage = Image.new("RGB", (2048, 606 + footer_height), "white")
    try:
        for index, (name, at_ns) in enumerate((("start", 0), ("end", spec.timeline.end_time_ns))):
            times = []
            scene = logic.sample(
                s.SimulationTime(spec.timeline.clock_domain.clock_domain_id, at_ns, index)
            )
            for _ in range(args.samples):
                started = time.perf_counter()
                scene = logic.sample(
                    s.SimulationTime(spec.timeline.clock_domain.clock_domain_id, at_ns, index)
                )
                times.append((time.perf_counter() - started) * 1000)
            camera = next(c for c in scene.cameras if c.camera_role == m.CameraRole.MAIN)
            intrinsics = camera.actual_intrinsics
            camera = replace(
                camera,
                actual_intrinsics=replace(
                    intrinsics,
                    image_width_px=1024,
                    image_height_px=576,
                    focal_x_px=intrinsics.focal_x_px * 1024 / 1920,
                    focal_y_px=intrinsics.focal_y_px * 576 / 1080,
                    principal_x_px=(intrinsics.principal_x_px + 0.5) * 1024 / 1920 - 0.5,
                    principal_y_px=(intrinsics.principal_y_px + 0.5) * 576 / 1080 - 0.5,
                ),
            )
            raw, first_ms = renderer.render(scene, camera)
            warm = [renderer.render(scene, camera)[1] for _ in range(args.samples)]
            image = Image.frombytes("RGB", (1024, 576), raw)
            attributed = Image.new("RGB", (1024, 576 + footer_height), "white")
            attributed.paste(image, (0, 0))
            ImageDraw.Draw(attributed).multiline_text(
                (10, 581), "\n".join(credit_lines), fill="black", spacing=3
            )
            attributed.save(args.output / f"{name}.png")
            x = index * 1024
            montage.paste(image, (x, 30))
            ImageDraw.Draw(montage).text((x + 10, 8), name, fill="black")
            report["views"][name] = {
                "logic_p50_ms": statistics.median(times),
                "first_request_ms": first_ms,
                "warm_p50_ms": statistics.median(warm),
                "warm_max_ms": max(warm),
                "main_visibility": [
                    asdict(v) for v in scene.target_visibility if v.camera_role == m.CameraRole.MAIN
                ],
            }
        ImageDraw.Draw(montage).multiline_text(
            (10, 611), "\n".join(credit_lines), fill="black", spacing=3
        )
        montage.save(args.output / "comparison.png")
        report["python_rss_kib"] = rss_kib(os.getpid())
        if renderer._process is not None:
            report["godot_rss_kib"] = rss_kib(renderer._process.pid)
        diagnostics = renderer._diagnostics()
        if "SCRIPT ERROR" in diagnostics or "ERROR:" in diagnostics:
            raise RuntimeError(diagnostics)
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (args.output / "report.json").write_text(text)
        print(
            json.dumps(
                {
                    "report": str(args.output / "report.json"),
                    "landscape_meshes": report["landscape_meshes"],
                    "triangles": report["triangles"],
                    "views": report["views"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        renderer.close()


if __name__ == "__main__":
    main()
