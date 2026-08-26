#!/usr/bin/env python3
"""Run every suite; exit non-zero if any suite fails or ran no coverage."""
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    if not isinstance(summary.get("requires"), (str, type(None))):
        return None
    return summary


def _report_safely():
    """Make sure captured suite output can always be reported."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        if os.environ.get("PYTHONIOENCODING") or sys.stdout.isatty():
            reconfigure(errors="replace")
        else:
            reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass


def _run_suite(suite, summaries):
    summary_path = Path(summaries) / f"{suite.stem}.json"
    env = dict(os.environ, DAEDALUS_TEST_SUMMARY=str(summary_path))
    result = subprocess.run([sys.executable, str(suite)], cwd=ROOT,
                            stdin=subprocess.DEVNULL, check=False, env=env,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    return result, _read_summary(summary_path)


def main() -> int:
    _report_safely()
    suites = sorted((ROOT / "tests").glob("test_*.py"))
    if not suites:
        print("no suites found", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as summaries:
        workers = min(len(suites), os.cpu_count() or 1)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_run_suite, suite, summaries): suite
                for suite in suites
            }
            results = {}
            for future in as_completed(futures):
                suite = futures[future]
                result, summary = future.result()
                output = result.stdout + result.stderr
                block = f"=== {suite.name} ===\n{output}"
                if not block.endswith("\n"):
                    block += "\n"
                print(block, end="", flush=True)
                results[suite] = result.returncode, summary

    failed, empty, unrun = [], [], []
    passed = skipped = 0
    for suite in suites:
        returncode, summary = results[suite]
        if returncode != 0 or summary is None:
            # A suite that died before reporting its counts is not a
            # verified one either, whatever its exit code said.
            failed.append(suite.name)
            continue
        passed += summary["passed"]
        skipped += summary["skipped"]
        if summary["passed"] == 0:
            # A suite that named an external dependency it needs is the
            # one case where nothing running is a fact about the machine
            # rather than about the suite. It is still not coverage, so
            # it is reported by name below rather than folded into the
            # pass line.
            target = unrun if summary["requires"] else empty
            target.append(
                f'{suite.name} ({summary["skipped"]} skipped'
                + (f', needs {summary["requires"]}'
                   if summary["requires"] else '') + ')')
    print()
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    counts = f"{len(suites)} suites, {passed} passed, {skipped} skipped"
    if unrun:
        print("NOT RUN HERE: " + ", ".join(unrun))
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
