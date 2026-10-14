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
    parser.add_argument("--render-every", type=int, default=1)
    args = parser.parse_args()
    if args.samples < 1 or args.warmup < 1 or args.render_every < 1:
        parser.error("samples and warmup must be positive")
    spec = demo_spec(station_count=args.stations, fps=30)
    source = demo_source(spec, render_every=args.render_every)
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
            timing = step.truth.timing
            assert timing is not None
            gpu = timing.gpu_ms
            overhead = max(0.0, timing.render_ms - gpu)
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
                    "rendered": float(step.video.rendered),
                    "gpu_submissions": float(timing.gpu_submissions),
                    "logic_ms": timing.logic_ms,
                    "frame_truth_ms": timing.frame_truth_ms,
                    "virtual_ms": timing.virtual_ms,
                }
            )
    finally:
        close_default_renderer()
    print(
        json.dumps(
            {
                "host": platform.platform(),
                "profile": "standard-v1: 1920x1080 + 640x480 per station; RGB8",
                "stations": args.stations,
                "preview": args.preview,
                "samples": len(rows),
                "render_every": args.render_every,
                "gpu_submissions": int(sum(row["gpu_submissions"] for row in rows)),
                "rendered_steps": int(sum(row["rendered"] for row in rows)),
                "mean_ms": {
                    key: round(statistics.mean(row[key] for row in rows), 2) for key in rows[0]
                },
                "generation_range_ms": [
                    round(min(row["generation_ms"] for row in rows), 2),
                    round(max(row["generation_ms"] for row in rows), 2),
                ],
                "generation_tail_ms": {
                    "p50": round(statistics.median(row["generation_ms"] for row in rows), 2),
                    "p95": round(
                        sorted(row["generation_ms"] for row in rows)[
                            max(0, (95 * len(rows) + 99) // 100 - 1)
                        ],
                        2,
                    ),
                    "max": round(max(row["generation_ms"] for row in rows), 2),
                },
                "rendered_generation_tail_ms": {
                    "p50": round(
                        statistics.median(row["generation_ms"] for row in rows if row["rendered"]),
                        2,
                    ),
                    "p95": round(
                        sorted(row["generation_ms"] for row in rows if row["rendered"])[
                            max(0, (95 * sum(int(row["rendered"]) for row in rows) + 99) // 100 - 1)
                        ],
                        2,
                    ),
                    "max": round(max(row["generation_ms"] for row in rows if row["rendered"]), 2),
                }
                if any(row["rendered"] for row in rows)
                else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
