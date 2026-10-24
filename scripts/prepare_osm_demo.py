"""Rebuild the small CC0 landscape/DEM fixture without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from chongming.contracts import scene as s
from chongming.scenarios.osm import asset_bytes, prepare_osm
from chongming.scenarios.serialization import _decode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("scenarios/assets/osm_landscape_demo.json")
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    assets = root / "scenarios/assets"
    config = replace(
        _decode(json.loads((assets / "osm_import.json").read_text()), s.OSMImportConfig),
        forest_spacing_m=20.0,
        maximum_forest_trees=24,
    )
    elevation = s.OSMElevation(
        (-120.0, -120.0),
        60.0,
        (
            (-2.0, -1.0, 0.0, 1.0, 2.0),
            (-1.0, 0.0, 1.0, 2.0, 3.0),
            (0.0, 1.0, 8.0, 3.0, 4.0),
            (1.0, 2.0, 3.0, 4.0, 5.0),
            (2.0, 3.0, 4.0, 5.0, 6.0),
        ),
        "repository:scripts/prepare_osm_demo.py (synthetic CC0 grid)",
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "2026-10-20",
        "synthetic-relative",
        0.0,
        "CC0-1.0",
        "Synthetic terrain, CC0-1.0",
    )
    imported = prepare_osm(
        (assets / "osm_landscape_demo.osm").read_bytes(),
        config,
        source_url="repository:scenarios/assets/osm_landscape_demo.osm (synthetic fixture)",
        data_date="2026-10-20",
        license_name="CC0-1.0",
        attribution="Synthetic landscape, CC0-1.0; not an OpenStreetMap extract",
        elevation=elevation,
    ).imported
    content = asset_bytes(imported)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(json.dumps({"asset": str(args.output), "sha256": hashlib.sha256(content).hexdigest()}))


if __name__ == "__main__":
    main()
