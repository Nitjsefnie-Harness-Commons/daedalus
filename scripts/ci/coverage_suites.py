#!/usr/bin/env python3
"""Measure suites concurrently so coverage is not the workflow bottleneck."""
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from scripts.ci.suite_bound import (
        launch_suite, suite_timeout, timeout_record)
except ImportError:  # pragma: no cover - the script-directory import path
    # The order is load-bearing. Both spellings name one file, and Python
    # gives each a module object of its own, so whichever is tried first is
    # the copy every caller that can reach it shares. The package spelling
    # comes first because a caller with the repository root importable --
    # a test, a tool -- already holds that object, and a second object
    # would be a second copy of the bound, which is the drift this module
    # exists to remove. A workflow step runs the file by path with only
    # its own directory on the path, and reaches the fallback.
    from suite_bound import launch_suite, suite_timeout, timeout_record

ROOT = Path(__file__).resolve().parents[2]


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
        # The stream still works without the safety net, so keep running.
        pass


def _run_suite(suite, outputs, bound):
    name = suite.relative_to(ROOT).as_posix()
    output_path = Path(outputs) / f"{suite.stem}.output"
    returncode, cleanup = launch_suite(
        name,
        [sys.executable, "-m", "coverage", "run", "--parallel-mode",
         str(suite)],
        cwd=ROOT, output_path=output_path, timeout=bound)
    if cleanup:
        with output_path.open("ab") as output:
            output.write(
                timeout_record(name, bound, returncode, cleanup).encode())
    return returncode, output_path, cleanup


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv not in ([], ["--require-all"]):
        print("usage: coverage_suites.py [--require-all]", file=sys.stderr)
        return 2
    require_all = argv == ["--require-all"]

    _report_safely()
    bound = suite_timeout()
    suites = sorted((ROOT / "tests").glob("test_*.py"))
    if not suites:
        print("no suites found — refusing to report 0% as a pass",
              file=sys.stderr)
        return 1

    failed = 0
    timed_out = []
    with tempfile.TemporaryDirectory() as outputs:
        workers = min(len(suites), os.cpu_count() or 1)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_run_suite, suite, outputs, bound): suite
                for suite in suites
            }
            for future in as_completed(futures):
                suite = futures[future]
                try:
                    returncode, output_path, cleanup = future.result()
                    output = output_path.read_text(
                        encoding="utf-8", errors="replace")
                except Exception as exc:
                    returncode, cleanup = 1, ""
                    output = (
                        f"LAUNCH FAILED: {type(exc).__name__}: {exc}\n")
                relative = suite.relative_to(ROOT).as_posix()
                block = f"::group::{relative}\n{output}"
                if output and not output.endswith("\n"):
                    block += "\n"
                if returncode:
                    failed += 1
                    block += (
                        "  (suite did not pass; its coverage still counts)\n"
                    )
                block += "::endgroup::\n"
                print(block, end="", flush=True)
                if cleanup:
                    timed_out.append(relative)

    if timed_out:
        # A killed suite is not a failing suite. Its coverage is truncated
        # at whatever it had measured, so unlike a suite that failed on its
        # own this refuses the run even without --require-all, and says
        # which suites to look at first.
        print(f"TIMED OUT: {', '.join(timed_out)} — each was killed at its "
              f"{bound} s bound and its coverage is truncated",
              file=sys.stderr)
        return 1
    if require_all and failed:
        print(
            f"{failed} of the {len(suites)} suites failed — refusing "
            "partial coverage",
            file=sys.stderr,
        )
        return 1
    if failed == len(suites):
        print(f"every one of the {len(suites)} suites failed — refusing to",
              file=sys.stderr)
        print("report a coverage number for a program that did not run.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
