#!/usr/bin/env python3
"""Measure suites concurrently so coverage is not the workflow bottleneck."""
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

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


def _run_suite(suite, outputs):
    output_path = Path(outputs) / f"{suite.stem}.output"
    with output_path.open("wb") as output:
        result = subprocess.run(
            [sys.executable, "-m", "coverage", "run", "--parallel-mode",
             str(suite)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, check=False)
    return result.returncode, output_path


def main():
    _report_safely()
    suites = sorted((ROOT / "tests").glob("test_*.py"))
    if not suites:
        print("no suites found — refusing to report 0% as a pass",
              file=sys.stderr)
        return 1

    failed = 0
    with tempfile.TemporaryDirectory() as outputs:
        workers = min(len(suites), os.cpu_count() or 1)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_run_suite, suite, outputs): suite
                for suite in suites
            }
            for future in as_completed(futures):
                suite = futures[future]
                returncode, output_path = future.result()
                output = output_path.read_text(
                    encoding="utf-8", errors="replace")
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

    if failed == len(suites):
        print(f"every one of the {len(suites)} suites failed — refusing to",
              file=sys.stderr)
        print("report a coverage number for a program that did not run.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
