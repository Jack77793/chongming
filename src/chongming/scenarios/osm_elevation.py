"""Small immutable DEM helpers used by OSM feature placement."""

from __future__ import annotations

import gzip
import hashlib
import math
import re
from dataclasses import replace
from datetime import date

import numpy as np

from chongming.contracts import scene as s


def elevation_at(imported: s.OSMImport, east: float, north: float) -> float:
    grid = imported.elevation
    if grid is None:
        return imported.config.base_elevation_m
    rows, cols = len(grid.heights_m), len(grid.heights_m[0])
    u = (east - grid.origin_enu_m[0]) / grid.spacing_m
    v = (north - grid.origin_enu_m[1]) / grid.spacing_m
    u = min(max(u, 0.0), cols - 1.0)
    v = min(max(v, 0.0), rows - 1.0)
    x, y = min(int(u), cols - 2), min(int(v), rows - 2)
    fx, fy = u - x, v - y
    a, b = grid.heights_m[y][x], grid.heights_m[y][x + 1]
    c, d = grid.heights_m[y + 1][x], grid.heights_m[y + 1][x + 1]
    # Match the exact diagonal of the terrain mesh, rather than a bilinear surface.
    return a + (b - a) * fx + (d - b) * fy if fx >= fy else a + (d - c) * fx + (c - a) * fy


def validate_elevation(grid: s.OSMElevation) -> None:
    date.fromisoformat(grid.data_date)
    if (
        not grid.source.strip()
        or not grid.vertical_datum.strip()
        or not grid.license.strip()
        or not grid.attribution.strip()
        or not re.fullmatch(r"[0-9a-f]{64}", grid.source_sha256)
    ):
        raise ValueError("invalid DEM provenance")
    if (
        not math.isfinite(grid.spacing_m)
        or grid.spacing_m <= 0
        or len(grid.heights_m) < 2
        or len(grid.heights_m[0]) < 2
        or any(len(r) != len(grid.heights_m[0]) for r in grid.heights_m)
        or len(grid.heights_m) * len(grid.heights_m[0]) > 250_000
        or any(not math.isfinite(h) for row in grid.heights_m for h in row)
        or not all(math.isfinite(v) for v in (*grid.origin_enu_m, grid.reference_height_m))
    ):
        raise ValueError("invalid DEM grid; requires finite rectangular 2x2..250000 samples")


def prepare_hgt(
    content: bytes,
    tile: str,
    config: s.OSMImportConfig,
    *,
    source_url: str,
    data_date: str,
    license_name: str,
    attribution: str,
    spacing_m: float = 60.0,
) -> s.OSMElevation:
    """Resample a fixed EGM96 HGT tile to ENU, relative to anchor ground.

    EGM96 orthometric source heights are never mixed with WGS84 ellipsoidal height.
    The anchor ground sample maps to config.base_elevation_m; reference_height_m
    records that EGM96 sample. HGT may contain composite terrain data; its actual
    provider/license must be recorded separately from the container format.
    """
    from chongming.scenarios.osm import project_enu, validate_config

    validate_config(config)
    match = re.fullmatch(r"([NS])(\d{2})([EW])(\d{3})", tile)
    if match is None or not math.isfinite(spacing_m) or spacing_m < 10:
        raise ValueError("DEM requires tile like N52E009 and spacing >= 10 m")
    lat0 = int(match[2]) * (1 if match[1] == "N" else -1)
    lon0 = int(match[4]) * (1 if match[3] == "E" else -1)
    raw = gzip.decompress(content) if content.startswith(b"\x1f\x8b") else content
    size = math.isqrt(len(raw) // 2)
    if size not in {1201, 3601} or len(raw) != size * size * 2:
        raise ValueError("SRTM HGT requires a 1201x1201 or 3601x3601 big-endian int16 tile")
    raster = np.frombuffer(raw, dtype=">i2").reshape(size, size)

    def sample(lon: float, lat: float) -> float:
        if not (lon0 <= lon <= lon0 + 1 and lat0 <= lat <= lat0 + 1):
            raise ValueError("DEM tile does not cover selection and grid margin")
        u, v = (lon - lon0) * (size - 1), (lat0 + 1 - lat) * (size - 1)
        x, y = min(int(u), size - 2), min(int(v), size - 2)
        values = raster[y : y + 2, x : x + 2].astype(float)
        if np.any(values == -32768):
            raise ValueError("DEM contains void samples in selected area")
        fx, fy = u - x, v - y
        return float(
            (values[0, 0] * (1 - fx) + values[0, 1] * fx) * (1 - fy)
            + (values[1, 0] * (1 - fx) + values[1, 1] * fx) * fy
        )

    anchor = config.anchor
    reference = sample(anchor.longitude_deg, anchor.latitude_deg)
    west, south, east, north = config.bounds_wgs84
    corners = [
        project_enu(lon, lat, anchor)
        for lon, lat in ((west, south), (east, south), (east, north), (west, north))
    ]
    origin = tuple(math.floor(min(p[i] for p in corners) / spacing_m) * spacing_m for i in (0, 1))
    columns = math.ceil((max(p[0] for p in corners) - origin[0]) / spacing_m) + 1
    rows = math.ceil((max(p[1] for p in corners) - origin[1]) / spacing_m) + 1
    if columns * rows > 250_000:
        raise ValueError("DEM exceeds 250000 samples; increase spacing or shrink selection")

    # Invert the same projection by Newton iterations, including its rotation/curvature.
    def inverse(x: float, y: float) -> tuple[float, float]:
        lon, lat = anchor.longitude_deg, anchor.latitude_deg
        for _ in range(4):
            px, py = project_enu(lon, lat, anchor)
            ex, ey = project_enu(lon + 0.0001, lat, anchor)
            nx, ny = project_enu(lon, lat + 0.0001, anchor)
            jacobian = np.array([[ex - px, nx - px], [ey - py, ny - py]]) / 0.0001
            delta = np.linalg.solve(jacobian, np.array([x - px, y - py]))
            lon, lat = lon + float(delta[0]), lat + float(delta[1])
        return lon, lat

    heights = tuple(
        tuple(
            round(
                sample(*inverse(origin[0] + x * spacing_m, origin[1] + y * spacing_m))
                - reference
                + config.base_elevation_m,
                4,
            )
            for x in range(columns)
        )
        for y in range(rows)
    )
    result = s.OSMElevation(
        (origin[0], origin[1]),
        spacing_m,
        heights,
        source_url,
        hashlib.sha256(content).hexdigest(),
        data_date,
        "EGM96",
        reference,
        license_name,
        attribution,
    )
    validate_elevation(result)
    return result


def terrain_from_elevation(base: s.TerrainSpec, grid: s.OSMElevation | None) -> s.TerrainSpec:
    if grid is None:
        return base
    minimum = min(min(row) for row in grid.heights_m)
    columns = len(grid.heights_m[0])
    vertices = tuple(
        (x * grid.spacing_m, y * grid.spacing_m, h - minimum)
        for y, row in enumerate(grid.heights_m)
        for x, h in enumerate(row)
    )
    triangles: list[tuple[int, int, int]] = []
    for y in range(len(grid.heights_m) - 1):
        for x in range(columns - 1):
            i = y * columns + x
            triangles.extend(((i, i + 1, i + columns + 1), (i, i + columns + 1, i + columns)))
    return replace(
        base,
        model=s.TerrainModel.HEIGHTFIELD,
        base_elevation_m=minimum,
        maximum_relief_m=max(max(row) for row in grid.heights_m) - minimum,
        heightfield_geometry=s.TriangleMeshGeometry(vertices, tuple(triangles)),
        heightfield_origin_enu_m=grid.origin_enu_m,
        heightfield_asset_uri=None,
        heightfield_asset_digest=None,
    )
