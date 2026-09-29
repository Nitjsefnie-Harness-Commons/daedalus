#!/usr/bin/env python3
"""The source anchors' own preconditions, which the routes assume.

Not the routes: `tests/test_unresolved_routes.py` plants through these and
asserts what the guard says. What is pinned here is the property those
plants depend on and cannot see — that the anchor is unique, and that
"first" means the earlier line. An anchor that is not unique is the shape
where a mutation edits the wrong occurrence and no assert-plant check can
tell that from a mutation that changed nothing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _source_anchors import (  # noqa: E402
    after_call, first_call_line, the_call_line)

_TWO = ('def _helper(tmp):\n'
        '    seed(tmp)\n'
        '    seed(tmp)\n'
        '    return tmp\n')
_PLANT = "    (tmp / 'x').write_text()"
_CALLS = ('_helper = None\n'
          'def test_control(tmp):\n'
          '    del tmp\n'
          '    _helper(tmp)\n'
          '    _helper(tmp)\n')


def test_after_call_refuses_a_second_occurrence_of_the_anchored_call(tmp):
    """A plant must name one place, or it is a coin toss."""
    del tmp
    try:
        after_call(_TWO, 'seed', _PLANT)
    except AssertionError as error:
        assert 'the seed anchor is not unique' in str(error), error
    else:
        raise AssertionError('the anchor accepted an ambiguous position')


def test_the_call_line_refuses_a_second_occurrence(tmp):
    """The same, for the line the guard is expected to report."""
    del tmp
    try:
        the_call_line(_TWO, 'seed')
    except AssertionError as error:
        assert 'the seed call is not unique' in str(error), error
    else:
        raise AssertionError('the anchor accepted an ambiguous position')


def test_the_first_call_line_is_the_earlier_of_the_calls(tmp):
    """`first` is a promise about order, so it is pinned against order.

    The mention on the first line is there to be skipped: the anchor is a
    call site, and a reader that took the last line, or the first mention
    of the name, would place a plant somewhere else and the routes would
    go red for a reason no assert-plant check can name.
    """
    del tmp
    assert first_call_line(_CALLS, '_helper') == 4


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
