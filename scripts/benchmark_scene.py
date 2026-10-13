"""Measure native RGB8 generation and the optional PNG preview separately.

Run with other preview producers stopped or paused. This synchronous benchmark
excludes HTTP and browser work; it does not claim end-to-end display latency.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time

from chongming.preview import _preview_metadata, _video_payload
from chongming.rendering.metal import close_default_renderer
from chongming.scenarios.demo import demo_source, demo_spec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--preview", choices=("none", "selected", "all"), default="selected")
    args = parser.parse_args()
    if args.samples < 1 or args.warmup < 1:
        parser.error("samples and warmup must be positive")
    spec = demo_spec(baseline=True, station_count=args.stations)
    source = demo_source(spec)
    steps = iter(source.generate(spec))
    rows: list[dict[str, float]] = []
    try:
        for index in range(args.warmup + args.samples):
            began = time.perf_counter()
            step = next(steps)
            generated = time.perf_counter()
            payload = _preview_metadata(step, index + 1, 1) if args.preview != "none" else {}
            metadata_done = time.perf_counter()
            if args.preview != "none":
                station = str(spec.scene_definition.cameras[0].station_id)
                payload["video"] = _video_payload(
                    step, station if args.preview == "selected" else None
                )
            encoded = time.perf_counter()
            if args.preview != "none":
                json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            finished = time.perf_counter()
            if index < args.warmup:
                continue
            gpu = source.imager.last_gpu_render_ms
            overhead = source.imager.last_imager_overhead_ms
            generation = (generated - began) * 1000
            rows.append(
                {
                    "generation_ms": generation,
                    "scene_logic_events_ms": max(0.0, generation - gpu - overhead),
                    "gpu_ms": gpu,
                    "imager_other_wall_ms": overhead,
                    "metadata_ms": (metadata_done - generated) * 1000,
                    "png_base64_ms": (encoded - metadata_done) * 1000,
                    "json_ms": (finished - encoded) * 1000,
                    "serial_total_ms": (finished - began) * 1000,
                }
            )
    finally:
        close_default_renderer()
    print(
        json.dumps(
            {
                "host": platform.platform(),
                "profile": "baseline-v1: 1920x1080 + 640x480 per station; RGB8",
                "stations": args.stations,
                "preview": args.preview,
                "samples": len(rows),
                "mean_ms": {
                    key: round(statistics.mean(row[key] for row in rows), 2) for key in rows[0]
                },
                "generation_range_ms": [
                    round(min(row["generation_ms"] for row in rows), 2),
                    round(max(row["generation_ms"] for row in rows), 2),
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
