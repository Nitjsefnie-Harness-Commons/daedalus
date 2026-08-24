#!/usr/bin/env python3
"""Run every suite; exit non-zero if any suite fails or ran no coverage."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _read_summary(path):
    """The suite's own counts, or None when it never reported them."""
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(summary, dict):
        return None
    if not all(isinstance(summary.get(k), int)
               for k in ("total", "passed", "skipped", "failed")):
        return None
    return summary


def main() -> int:
    suites = sorted((ROOT / "tests").glob("test_*.py"))
    if not suites:
        print("no suites found", file=sys.stderr)
        return 1
    failed, empty = [], []
    passed = skipped = 0
    with tempfile.TemporaryDirectory() as summaries:
        for suite in suites:
            print(f"=== {suite.name} ===", flush=True)
            summary_path = Path(summaries) / f"{suite.stem}.json"
            env = dict(os.environ, DAEDALUS_TEST_SUMMARY=str(summary_path))
            result = subprocess.run([sys.executable, str(suite)], cwd=ROOT,
                                    stdin=subprocess.DEVNULL, check=False,
                                    env=env)
            summary = _read_summary(summary_path)
            if result.returncode != 0 or summary is None:
                # A suite that died before reporting its counts is not a
                # verified one either, whatever its exit code said.
                failed.append(suite.name)
                continue
            passed += summary["passed"]
            skipped += summary["skipped"]
            if summary["passed"] == 0:
                empty.append(f'{suite.name} ({summary["skipped"]} skipped)')
    print()
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    counts = f"{len(suites)} suites, {passed} passed, {skipped} skipped"
    if empty:
        # Every test in these suites skipped, so nothing about them was
        # verified. Reporting that as a pass is the one thing the aggregate
        # line must never do: it is what a reader and CI both key on.
        print("NO COVERAGE: " + ", ".join(empty))
        print(f"OVERALL: INCOMPLETE ({counts})")
        return 1
    print(f"OVERALL: PASS ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
