#!/usr/bin/env python3
"""Test SceneController by comparing its output with Python's implementation."""

import json
import subprocess
import sys
from pathlib import Path

# Test scenarios
TEST_SCENARIOS = [
    "scenarios/empty.json",
    "scenarios/constant_velocity.json",
]


def run_godot_check(scenario_path: str) -> tuple[bool, str]:
    """Run Godot check mode and return success status and output."""
    godot = Path(__file__).parent.parent / "src/chongming/rendering/godot"
    cmd = [
        "godot4",
        "--headless",
        "--path",
        str(godot),
        "--script",
        "main.gd",
        "--",
        "--scenario",
        f"../../../../{scenario_path}",
        "--mode",
        "check",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, cwd=Path(__file__).parent.parent
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def main():
    print("Testing Godot SceneController...")
    print("=" * 60)

    passed = 0
    failed = 0

    for scenario in TEST_SCENARIOS:
        print(f"\nTesting: {scenario}")
        success, output = run_godot_check(scenario)

        if success:
            print(f"  ✓ PASSED")
            passed += 1
        else:
            print(f"  ✗ FAILED")
            print(f"  Output: {output[:200]}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
