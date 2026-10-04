#!/usr/bin/env python3
"""Run every suite; exit non-zero if any suite fails or ran no coverage."""
import json
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    # Loading this file by path, as `tests/test_suite_runner.py` does, does
    # not put its directory on the path, and the shared bound is imported
    # by PACKAGE name. The repository root goes on, not `scripts/ci`:
    # importing a sibling by bare name out of that directory is the
    # spelling `scripts/ci/coverage_suites.py` uses only for a step that
    # runs the file by path, because it loads one file under one name and
    # a package import here is what keeps it to one.
    sys.path.insert(0, str(ROOT))

from scripts.ci.suite_bound import (  # noqa: E402
    CLEANUP_TIMEOUT_S,
    discard_outputs, kill_process_tree, suite_timeout, timeout_record)
# Re-exported, not used here: `tests/test_suite_runner.py` drives
# `_run_suite` with the bound the runner itself resolves, and that is this
# name. A second copy of the number here is what this change removes.
# pylint: disable-next=unused-import
from scripts.ci.suite_bound import DEFAULT_SUITE_TIMEOUT_S  # noqa: E402,F401

_SPAWN_LOCK = threading.Lock()


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
        # The stream still works without the safety net, so keep running.
        pass


def _terminate_and_reap(process):
    process.terminate()
    try:
        process.wait(timeout=CLEANUP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=CLEANUP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # SIGKILL with nothing left holding a pipe, and still not
            # reaped: report and give up rather than mask the failure
            # that reached this helper or hold the suite worker forever.
            print(f'run_tests: pid {process.pid} survived SIGKILL; '
                  'leaving it unreaped', file=sys.stderr)


def _run_suite(suite, summaries, timeout):
    summary_path = Path(summaries) / f"{suite.stem}.json"
    output_path = Path(summaries) / f"{suite.stem}.output"
    env = dict(os.environ, DAEDALUS_TEST_SUMMARY=str(summary_path))
    process = None
    try:
        with _SPAWN_LOCK:
            with output_path.open("wb") as output:
                process = subprocess.Popen(
                    [sys.executable, str(suite)], cwd=ROOT,
                    stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.STDOUT, env=env,
                    start_new_session=sys.platform != "win32",
                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                                   if sys.platform.startswith("win") else 0))
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as expired:
            # The direct child is not the tree: a suite starts children of
            # its own, and killing only the child leaves those running.
            cleanup = kill_process_tree(process)
            _terminate_and_reap(process)
            returncode = process.returncode
            with output_path.open("ab") as output:
                output.write(timeout_record(
                    suite.name, expired.timeout, process.returncode,
                    cleanup).encode())
    except BaseException:
        if process is not None:
            kill_process_tree(process)
            _terminate_and_reap(process)
        raise
    return returncode, _read_summary(summary_path), output_path


def main() -> int:
    _report_safely()
    timeout = suite_timeout()
    suites = sorted((ROOT / "tests").glob("test_*.py"))
    if not suites:
        print("no suites found", file=sys.stderr)
        return 1
    # `mkdtemp` and a `finally` rather than a `TemporaryDirectory`, which
    # removes its tree in `__exit__`: a removal refused for a few seconds
    # would raise out of `main()` and take the aggregate below with it.
    results = {}
    summaries = tempfile.mkdtemp(prefix="daedalus-summaries-")
    try:
        workers = min(len(suites), os.cpu_count() or 1)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_run_suite, suite, summaries, timeout): suite
                for suite in suites
            }
            for future in as_completed(futures):
                suite = futures[future]
                try:
                    returncode, summary, output_path = future.result()
                    output = output_path.read_text(
                        encoding="utf-8", errors="replace")
                except Exception as exc:
                    returncode, summary = 1, None
                    output = (
                        f"LAUNCH FAILED: {type(exc).__name__}: {exc}\n")
                block = f"=== {suite.name} ===\n{output}"
                if not block.endswith("\n"):
                    block += "\n"
                print(block, end="", flush=True)
                results[suite] = returncode, summary
    finally:
        discard_outputs(summaries)

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
    print(flush=True)
    if failed:
        print("FAILED: " + ", ".join(failed), flush=True)
        return 1
    counts = f"{len(suites)} suites, {passed} passed, {skipped} skipped"
    if unrun:
        print("NOT RUN HERE: " + ", ".join(unrun), flush=True)
    if empty:
        # Every test in these suites skipped, so nothing about them was
        # verified. Reporting that as a pass is the one thing the aggregate
        # line must never do: it is what a reader and CI both key on.
        print("NO COVERAGE: " + ", ".join(empty), flush=True)
        print(f"OVERALL: INCOMPLETE ({counts})", flush=True)
        return 1
    print(f"OVERALL: PASS ({counts})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
