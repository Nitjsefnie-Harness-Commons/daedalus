#!/usr/bin/env python3
"""Run every suite; exit non-zero if any suite fails."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    suites = sorted((ROOT / "tests").glob("test_*.py"))
    if not suites:
        print("no suites found", file=sys.stderr)
        return 1
    failed = []
    for suite in suites:
        print(f"=== {suite.name} ===", flush=True)
        result = subprocess.run([sys.executable, str(suite)], cwd=ROOT,
                                stdin=subprocess.DEVNULL, check=False)
        if result.returncode != 0:
            failed.append(suite.name)
    print()
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print(f"OVERALL: PASS ({len(suites)} suites)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
