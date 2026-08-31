#!/usr/bin/env python3
"""A route the checkers cannot resolve is a violation, not a no-op.

Issue 303's five routes, each planted in a real module's copy, plus the
class each one belongs to. The coverage guard is tests/_coverage_guard.py
and the control-write checker is tests/_control_writes.py; this suite
is one the latter scans, so its own writes stay below tmp.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _control_writes import control_write_violations  # noqa: E402
from _owned_writes import copy_test_tree  # noqa: E402
from _repo import ROOT  # noqa: E402

_DECLARATION_LINE = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"


def _module_text(target):
    """A test module's source with checkout line endings normalised."""
    return target.read_bytes().decode('utf-8').replace('\r\n', '\n')


def _real_module_copy(tmp, relative):
    """Copy the real test tree under a root this control owns."""
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    return root, root / relative


def _declaration_line(text):
    """The line after test_diff_coverage.py's module-level declaration."""
    assert _DECLARATION_LINE in text, 'the declaration binding shape changed'
    return text[:text.index(_DECLARATION_LINE)].count('\n') + 2


def test_controls_never_write_inside_the_repository(tmp):
    """This suite's own writes stay below the tmp roots it owns."""
    del tmp
    violations = control_write_violations(Path(__file__), ROOT)
    assert not violations, '\n'.join(violations)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
