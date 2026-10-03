#!/usr/bin/env python3
"""Contracts for the COUNTED BOUNDARY: the one point a counted child's
counters are zeroed, the argv that keeps the bridge it spawns instrumented
from birth, and every way the boundary refuses rather than lets an
interpreter's own cost back into the count."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    ROOT,
    _util,
    boundary_loader, boundary_probe, counted_run,
    journeys,
)


def test_the_counted_boundary_is_established_or_the_run_is_refused(tmp):
    """A counted child's count carries its own interpreter startup and the
    compile of its import closure unless a client request zeroes the counters
    after them, so the boundary is established or the run refuses naming why.
    The refusals alone pass against a module that loads the helper and never
    calls it, so the record below is the CALL, and unset loads nothing."""
    module, counters = journeys(), _journey_contract.counters()
    establish, absent = module._establish_counted_boundary, str(
        Path(tmp) / 'absent.so')
    idle = boundary_loader(daedalus_cg_zero_stats=True)
    for boundary, loader, code, cause in (
            (None, idle, 0, ''), ('/cg.so', idle, 0, ''),
            (absent, module.CDLL, 3, 'absent.so'),
            ('/cg.so', boundary_loader(), 3, 'daedalus_cg_zero_stats'),
            ('/cg.so', boundary_loader(daedalus_cg_zero_stats=True,
                                       fails=True), 3, 'would not issue')):
        got, said, spawned = boundary_probe(boundary, loader, establish)
        assert (got, spawned) == (code, []) and (
            cause in said if code else not said), (boundary, got, said)
    assert (idle.served, idle.zeroed) == (['/cg.so'], ['/cg.so']), (
        idle.served, idle.zeroed)
    argv = ['--journey', 'command-round-trip', '--root', tmp]
    got, said, spawned = boundary_probe(
        absent, module.CDLL, establish, lambda: module.main(argv))
    assert (got, spawned) == (3, []) and absent in said, (
        'main must refuse before it spawns anything', got, said)
    value, why, argv = counted_run(counters, 'mcp-exec', ROOT, tmp, None)
    assert value is None and not argv and 'DAEDALUS_CALLGRIND_BOUNDARY' in (
        why or ''), (value, why, argv)
    value, why, argv = counted_run(counters, 'mcp-exec', ROOT, tmp)
    assert argv and argv[0][0] == '/usr/bin/valgrind', (value, why, argv)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeyboundary_')


if __name__ == '__main__':
    raise SystemExit(main())
