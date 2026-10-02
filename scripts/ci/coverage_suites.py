#!/usr/bin/env python3
"""Measure suites concurrently so coverage is not the workflow bottleneck."""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if __package__:
    # pylint: disable-next=relative-beyond-top-level
    from .suite_bound import (
        discard_outputs, launch_suite, suite_timeout, timeout_record)
else:
    # A workflow step runs this file by path, so `sys.path[0]` is
    # `scripts/ci` and the repository root is on no path at all. Which
    # spelling resolves is therefore decided by HOW this module was
    # loaded, not by catching an `ImportError`: catching one cannot tell
    # "this spelling is unavailable here" from an `ImportError` raised
    # inside the shared module, and it would load one file under two
    # names — two module objects, two copies of the bound, which is the
    # drift this import exists to prevent.
    from suite_bound import (
        discard_outputs, launch_suite, suite_timeout, timeout_record)

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
    # The same shape, and for the same reason, as the runner's: a removal
    # that cannot finish must not take the `TIMED OUT:` report below with
    # it, because a launcher that raised here reports nothing at all.
    outputs = tempfile.mkdtemp(prefix="daedalus-outputs-")
    try:
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
    finally:
        discard_outputs(outputs)

    if timed_out:
        # A suite that overran is not a suite that failed, and what it
        # leaves behind is what it managed to flush before the bound --
        # which is a different thing on each platform, and the sentence has
        # to be true of both. On POSIX the kill asks first, so a suite that
        # answers the request keeps what it measured and one that does not
        # contributes nothing. On Windows `taskkill /F` is a forced
        # termination with no request and no grace, so a suite killed there
        # never had the chance and contributes nothing either way. So this
        # refuses the run even without --require-all, and says which suites
        # to look at first.
        flushed = ('a forced kill leaves a suite nothing to flush'
                   if sys.platform.startswith('win') else
                   'a suite that took the request to stop keeps what it '
                   'had measured, and one that did not contributes nothing')
        print(f"TIMED OUT: {', '.join(timed_out)} — each was ended at its "
              f"{bound} s bound; {flushed}",
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
