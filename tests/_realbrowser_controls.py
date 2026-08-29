"""Run real-browser controls without letting wrong skips escape."""
import functools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _realbrowser import BrowserEnvironmentSkipped  # noqa: E402


class ControlRequirementSkipped(_util.Skipped):
    """A browser-free control's own requirement is unavailable."""


def control_requirement_missing(reason):
    raise ControlRequirementSkipped(reason)


def _guarded(test, allowed, label):
    @functools.wraps(test)
    def run(tmp):
        try:
            test(tmp)
        except allowed:
            raise
        except _util.Skipped as why:
            raise AssertionError(
                f'{label}: {type(why).__name__}: {why}') from why

    return run


def run_controls(namespace, tmp_prefix):
    """Run browser-free controls; only their own requirements may skip."""
    tests = [_guarded(test, ControlRequirementSkipped,
                      'a fixture skip escaped a browser-free control')
             for test in _util.collect(namespace)]
    return _util.runner(tests, tmp_prefix=tmp_prefix)


def run_real_browser_tests(namespace, tmp_prefix, requires):
    """Run browser tests; only a browser-environment verdict may skip."""
    tests = [_guarded(test, BrowserEnvironmentSkipped,
                      'a repository component excused itself as a skip')
             for test in _util.collect(namespace)]
    return _util.runner(tests, tmp_prefix=tmp_prefix, requires=requires)
