#!/usr/bin/env python3
"""Integration test for Godot-native scene controller.

This script validates that the new Godot fullstack implementation produces
outputs consistent with the original Python system. It:
1. Runs scenarios through both systems
2. Compares truth.jsonl outputs
3. Verifies numerical accuracy (tolerance 1e-9)
4. Generates clear pass/fail reports

Phase 1 verification: basic scene controller functionality.

Usage:
    # Check scenario validation only (no output comparison)
    python3 scripts/test_godot_integration.py --check-only

    # Generate baseline outputs from Python system
    python3 scripts/test_godot_integration.py --regenerate-baseline

    # Compare Godot output against baseline
    python3 scripts/test_godot_integration.py

    # Test specific scenarios
    python3 scripts/test_godot_integration.py empty.json constant_velocity.json

Requirements:
    - CHONGMING_GODOT environment variable or godot4/godot in PATH
    - Python environment with chongming package installed
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Tolerance for floating point comparisons
FLOAT_TOLERANCE = 1e-9

# Fields to compare in truth records
COMPARE_FIELDS = {
    "time_s",
    "time_ns",
    "step",
    "frame_period_s",
}

# Nested fields within logical_scene
SCENE_COMPARE_FIELDS = {
    "stations": ["id", "position_enu_m"],
    "targets": ["id", "position_enu_m", "velocity_enu_mps"],
}


class ComparisonError(Exception):
    """Raised when outputs differ beyond tolerance."""

    pass


def find_godot() -> Path:
    """Locate Godot 4.7.2 executable."""
    import os
    import shutil

    env_godot = os.environ.get("CHONGMING_GODOT")
    if env_godot:
        return Path(env_godot)

    for name in ("godot4", "godot"):
        path = shutil.which(name)
        if path:
            return Path(path)

    raise RuntimeError(
        "Godot 4.7.2 not found. Set CHONGMING_GODOT or install in PATH."
    )


def run_godot_scenario(
    scenario: Path, output_dir: Path, godot_script: Path
) -> dict[str, Any]:
    """Run scenario through Godot-native controller."""
    godot = find_godot()

    cmd = [
        str(godot),
        "--headless",
        "--script",
        str(godot_script),
        "--",
        "--scenario",
        str(scenario),
        "--output",
        str(output_dir),
        "--mode",
        "finite",
        "--no-video",
    ]

    print(f"Running Godot: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        raise RuntimeError(f"Godot execution failed with code {result.returncode}")

    return {"stdout": result.stdout, "stderr": result.stderr}


def run_python_scenario(scenario: Path, output_dir: Path) -> dict[str, Any]:
    """Run scenario through original Python system."""
    cmd = [
        sys.executable,
        "-m",
        "chongming.scenarios.run",
        str(scenario),
        str(output_dir),
        "--no-video",
    ]

    print(f"Running Python: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        raise RuntimeError(f"Python execution failed with code {result.returncode}")

    return {"stdout": result.stdout, "stderr": result.stderr}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL file as list of records."""
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")

    records = []
    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"JSON parse error at {path}:{line_num}: {e}")

    return records


def compare_floats(a: float, b: float, tolerance: float = FLOAT_TOLERANCE) -> bool:
    """Compare floats with tolerance."""
    return abs(a - b) <= tolerance


def compare_vec3(
    a: list[float], b: list[float], tolerance: float = FLOAT_TOLERANCE
) -> bool:
    """Compare 3D vectors with tolerance."""
    if len(a) != 3 or len(b) != 3:
        return False
    return all(compare_floats(a[i], b[i], tolerance) for i in range(3))


def compare_truth_record(
    godot_record: dict[str, Any],
    python_record: dict[str, Any],
    step: int,
    errors: list[str],
) -> None:
    """Compare single truth records from both systems.

    Args:
        godot_record: Record from Godot system
        python_record: Record from Python system
        step: Step number for error reporting
        errors: List to accumulate error messages
    """
    # Compare top-level fields
    for field in COMPARE_FIELDS:
        if field not in godot_record:
            errors.append(f"Step {step}: Godot missing field '{field}'")
            continue
        if field not in python_record:
            errors.append(f"Step {step}: Python missing field '{field}'")
            continue

        godot_val = godot_record[field]
        python_val = python_record[field]

        if isinstance(godot_val, float) and isinstance(python_val, float):
            if not compare_floats(godot_val, python_val):
                errors.append(
                    f"Step {step}: {field} differs: "
                    f"Godot={godot_val}, Python={python_val}, "
                    f"delta={abs(godot_val - python_val)}"
                )
        elif godot_val != python_val:
            errors.append(
                f"Step {step}: {field} differs: "
                f"Godot={godot_val}, Python={python_val}"
            )

    # Compare logical_scene
    godot_scene = godot_record.get("logical_scene", {})
    python_scene = python_record.get("logical_scene", {})

    # Compare stations
    godot_stations = godot_scene.get("stations", [])
    python_stations = python_scene.get("stations", [])

    if len(godot_stations) != len(python_stations):
        errors.append(
            f"Step {step}: Station count differs: "
            f"Godot={len(godot_stations)}, Python={len(python_stations)}"
        )
    else:
        for i, (g_sta, p_sta) in enumerate(zip(godot_stations, python_stations)):
            compare_entity(g_sta, p_sta, f"station[{i}]", step, errors)

    # Compare targets
    godot_targets = godot_scene.get("targets", [])
    python_targets = python_scene.get("targets", [])

    if len(godot_targets) != len(python_targets):
        errors.append(
            f"Step {step}: Target count differs: "
            f"Godot={len(godot_targets)}, Python={len(python_targets)}"
        )
    else:
        for i, (g_tgt, p_tgt) in enumerate(zip(godot_targets, python_targets)):
            compare_entity(g_tgt, p_tgt, f"target[{i}]", step, errors)


def compare_entity(
    godot_entity: dict[str, Any],
    python_entity: dict[str, Any],
    entity_type: str,
    step: int,
    errors: list[str],
) -> None:
    """Compare stations or targets."""
    # Compare ID
    if godot_entity.get("id") != python_entity.get("id"):
        errors.append(
            f"Step {step}: {entity_type} ID differs: "
            f"Godot={godot_entity.get('id')}, Python={python_entity.get('id')}"
        )

    # Compare position
    g_pos = godot_entity.get("position_enu_m")
    p_pos = python_entity.get("position_enu_m")
    if g_pos is not None and p_pos is not None:
        if not compare_vec3(g_pos, p_pos):
            errors.append(
                f"Step {step}: {entity_type} position_enu_m differs: "
                f"Godot={g_pos}, Python={p_pos}"
            )

    # Compare velocity (if present)
    g_vel = godot_entity.get("velocity_enu_mps")
    p_vel = python_entity.get("velocity_enu_mps")
    if g_vel is not None and p_vel is not None:
        if not compare_vec3(g_vel, p_vel):
            errors.append(
                f"Step {step}: {entity_type} velocity_enu_mps differs: "
                f"Godot={g_vel}, Python={p_vel}"
            )


def compare_truth_files(godot_truth: Path, python_truth: Path) -> list[str]:
    """Compare truth.jsonl files from both systems.

    Returns:
        List of error messages (empty if outputs match)
    """
    errors: list[str] = []

    # Load both files
    try:
        godot_records = load_jsonl(godot_truth)
    except Exception as e:
        errors.append(f"Failed to load Godot truth: {e}")
        return errors

    try:
        python_records = load_jsonl(python_truth)
    except Exception as e:
        errors.append(f"Failed to load Python truth: {e}")
        return errors

    # Compare record counts
    if len(godot_records) != len(python_records):
        errors.append(
            f"Record count differs: Godot={len(godot_records)}, "
            f"Python={len(python_records)}"
        )
        # Continue with min length
        min_count = min(len(godot_records), len(python_records))
    else:
        min_count = len(godot_records)

    # Compare each record
    for i in range(min_count):
        compare_truth_record(godot_records[i], python_records[i], i, errors)

    return errors


def test_scenario(
    scenario: Path,
    baseline_dir: Path,
    godot_script: Path,
    regenerate_baseline: bool = False,
) -> tuple[bool, str]:
    """Test a single scenario.

    Args:
        scenario: Path to scenario JSON
        baseline_dir: Directory to store/read baseline outputs
        godot_script: Path to Godot main.gd script
        regenerate_baseline: If True, regenerate Python baseline

    Returns:
        (success, message) tuple
    """
    scenario_name = scenario.stem
    baseline_subdir = baseline_dir / scenario_name

    with tempfile.TemporaryDirectory() as tmpdir:
        godot_output = Path(tmpdir) / "godot"
        godot_output.mkdir()

        # Run Godot system
        try:
            run_godot_scenario(scenario, godot_output, godot_script)
        except Exception as e:
            return False, f"Godot execution failed: {e}"

        godot_truth = godot_output / "truth.jsonl"
        if not godot_truth.exists():
            return False, "Godot did not produce truth.jsonl"

        # Handle baseline
        if regenerate_baseline:
            baseline_subdir.mkdir(parents=True, exist_ok=True)
            python_output = baseline_subdir
            try:
                run_python_scenario(scenario, python_output)
            except Exception as e:
                return False, f"Python baseline generation failed: {e}"
        else:
            if not baseline_subdir.exists():
                return (
                    False,
                    f"No baseline found at {baseline_subdir}. "
                    "Run with --regenerate-baseline first.",
                )

        python_truth = baseline_subdir / "truth.jsonl"
        if not python_truth.exists():
            return False, "Baseline missing truth.jsonl"

        # Compare outputs
        errors = compare_truth_files(godot_truth, python_truth)

        if errors:
            error_summary = "\n  ".join(errors[:10])  # Show first 10 errors
            if len(errors) > 10:
                error_summary += f"\n  ... and {len(errors) - 10} more errors"
            return False, f"Output mismatch ({len(errors)} errors):\n  {error_summary}"

        return True, f"✓ Outputs match (tolerance {FLOAT_TOLERANCE})"


def check_scenario(scenario: Path, godot_script: Path) -> tuple[bool, str]:
    """Run --check mode on a scenario.

    Args:
        scenario: Path to scenario JSON
        godot_script: Path to Godot main.gd script

    Returns:
        (success, message) tuple
    """
    godot = find_godot()

    cmd = [
        str(godot),
        "--headless",
        "--script",
        str(godot_script),
        "--",
        "--scenario",
        str(scenario),
        "--mode",
        "check",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        return False, f"Check failed (exit {result.returncode}): {result.stderr}"

    return True, "✓ Validation passed"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integration test for Godot-native scene controller"
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path("scenarios"),
        help="Directory containing scenario files",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("baseline"),
        help="Directory for baseline outputs",
    )
    parser.add_argument(
        "--godot-script",
        type=Path,
        default=Path("src/chongming/rendering/godot/main.gd"),
        help="Path to Godot main.gd script",
    )
    parser.add_argument(
        "--regenerate-baseline",
        action="store_true",
        help="Regenerate Python baseline outputs",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only run --check validation, don't compare outputs",
    )
    parser.add_argument(
        "scenario_names",
        nargs="*",
        help="Specific scenarios to test (default: empty.json, constant_velocity.json)",
    )

    args = parser.parse_args()

    # Validate paths
    if not args.scenarios.exists():
        print(f"Error: Scenarios directory not found: {args.scenarios}")
        return 1

    if not args.godot_script.exists():
        print(f"Error: Godot script not found: {args.godot_script}")
        return 1

    # Determine which scenarios to test
    if args.scenario_names:
        test_scenarios = [args.scenarios / name for name in args.scenario_names]
    else:
        # Default test scenarios
        test_scenarios = [
            args.scenarios / "empty.json",
            args.scenarios / "constant_velocity.json",
        ]

    # Validate scenario files exist
    missing = [s for s in test_scenarios if not s.exists()]
    if missing:
        print(f"Error: Scenario files not found: {', '.join(str(s) for s in missing)}")
        return 1

    print("=" * 70)
    print("Godot Integration Test")
    print("=" * 70)
    print(f"Scenarios: {len(test_scenarios)}")
    print(f"Baseline dir: {args.baseline}")
    print(f"Godot script: {args.godot_script}")
    print(f"Mode: {'check-only' if args.check_only else 'full comparison'}")
    if args.regenerate_baseline:
        print("⚠ Regenerating baseline outputs")
    print("=" * 70)
    print()

    results = []

    for scenario in test_scenarios:
        print(f"Testing: {scenario.name}")

        if args.check_only:
            success, message = check_scenario(scenario, args.godot_script)
        else:
            success, message = test_scenario(
                scenario, args.baseline, args.godot_script, args.regenerate_baseline
            )

        results.append((scenario.name, success, message))
        print(f"  {message}")
        print()

    # Summary
    print("=" * 70)
    print("Summary")
    print("=" * 70)

    passed = sum(1 for _, success, _ in results if success)
    total = len(results)

    for name, success, message in results:
        status = "PASS" if success else "FAIL"
        print(f"  [{status}] {name}")

    print()
    print(f"Total: {passed}/{total} passed")

    if passed == total:
        print("✓ All tests passed")
        return 0
    else:
        print(f"✗ {total - passed} tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())



