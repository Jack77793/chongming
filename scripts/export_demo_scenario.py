"""Write a compact semantic scenario-authoring-v2 demo configuration."""

from __future__ import annotations

import argparse
from pathlib import Path

from chongming.scenarios.authoring import demo_authoring, save_authoring


def main() -> None:
    parser = argparse.ArgumentParser(description="导出可编辑的虚拟场景 JSON")
    parser.add_argument("output", type=Path)
    parser.add_argument("--stations", type=int, default=1, choices=range(1, 5))
    parser.add_argument("--fps", type=int, default=30, choices=range(1, 61))
    parser.add_argument("--duration", type=float, default=24.0, help="有限场景时长（秒）")
    args = parser.parse_args()
    if not 0 < args.duration <= 3600:
        parser.error("duration must be in (0, 3600]")
    save_authoring(
        demo_authoring(stations=args.stations, fps=args.fps, duration_s=args.duration), args.output
    )
    print(args.output)


if __name__ == "__main__":
    main()
