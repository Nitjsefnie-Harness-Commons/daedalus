"""The fixtures that drive `scripts/ci/compare_durations.py` from a test.

Four speed suites stand up a comparison and then read what it decided: they
lay out a durations tree, load the comparator, and run its `main` with the
output captured. Each of those three steps was a private `def` repeated in
every suite that needed it, re-spelled per suite, so a change to how a run is
driven reached some of the comparisons and not the others. They are here so
there is one copy of each to fix.

`_durations_comparator` is the one name here that is not a byte-identical
move: `test_speed_acceptance.py` and `test_speed_measurement.py` called it
`_compare_durations` and `test_speed_summary.py` and `test_speed_verdicts.py`
called it `_comparator`, over the same body. The shorter name is already
held by `tests/test_speed_gate_options.py` with a different body, so a shared
helper that adopted it would make that suite an offender of it as well
(`test_helper_reimplementation.py`).
"""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


def _durations_tree(tmp, side, rounds):
    """Write one round's duration report, as time_tests.py records one."""
    dirs = []
    for index, tests in enumerate(rounds, start=1):
        d = Path(tmp) / f'{side}-{index}'
        d.mkdir(parents=True)
        (d / 'test_suite.json').write_text(json.dumps({'tests': tests}),
                                           encoding='utf-8')
        dirs.append(str(d))
    return dirs


def _durations_comparator():
    return _util.load(ROOT / 'scripts' / 'ci' / 'compare_durations.py')


def _run_comparator(compare, argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with (contextlib.redirect_stdout(stdout),
          contextlib.redirect_stderr(stderr)):
        try:
            code = compare.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, stdout.getvalue() + stderr.getvalue()
