#!/usr/bin/env python3
"""Bound state growth from state-neutral short-circuit expressions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pyroute  # noqa: E402
import _util  # noqa: E402
from _pyroute import py_tab_routing_violations  # noqa: E402


def _scanner_copies(tmp, width):
    source = Path(tmp) / f'short_circuit_{width}.py'
    condition = ' or '.join(['value'] * width)
    source.write_text(
        'def f(value):\n'
        '    while value:\n'
        f'        if {condition}:\n'
        '            continue\n'
        '        return None\n',
        encoding='utf-8')
    copies = 0
    original = _pyroute._copy_state_pair

    def counted_copy(state):
        nonlocal copies
        copies += 1
        return original(state)

    _pyroute._copy_state_pair = counted_copy
    try:
        assert not py_tab_routing_violations(source, source.name)
    finally:
        _pyroute._copy_state_pair = original
    return copies


def test_state_neutral_short_circuits_scale_linearly(tmp):
    narrow = _scanner_copies(tmp, 4)
    wide = _scanner_copies(tmp, 8)
    assert wide <= narrow * 2, (narrow, wide)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='routeperf_')


if __name__ == '__main__':
    raise SystemExit(main())
