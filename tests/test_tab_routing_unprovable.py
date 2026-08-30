#!/usr/bin/env python3
"""An unprovable sender alias is reported through an opaque `**spread`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute import py_tab_routing_violations  # noqa: E402


def _scan(tmp, body):
    source = Path(tmp) / 'unprovable.py'
    source.write_text('def go(args, tab):\n' + body, encoding='utf-8')
    return py_tab_routing_violations(source, source.name)


def _reported(tmp, body):
    found = _scan(tmp, body)
    assert found, f'unprovable-sender shape read clean:\n{body}'
    assert 'may be ext_cmd' in found[0], found


def _silent(tmp, body):
    found = _scan(tmp, body)
    assert not found, (f'provable-clean shape flagged:\n{body}\n'
                       + '\n'.join(found))


def test_opaque_spread_through_unprovable_sender(tmp):
    _reported(tmp, "    send = wrap(ext_cmd)\n"
                   "    send('x', 'y', **build(args))\n")
    _reported(tmp, "    send = wrap(ext_cmd)\n"
                   "    send('x', 'y', **args.payload)\n")


def test_unprovable_sender_reported_shapes(tmp):
    _reported(tmp, "    send = wrap(ext_cmd)\n"
                   "    send('x', 'y', tab=int(args))\n")
    _reported(tmp, "    send = wrap(ext_cmd)\n"
                   "    fields = {'tab': args.tab}\n"
                   "    send('x', 'y', **fields)\n")
    _reported(tmp, "    send = wrap(ext_cmd)\n"
                   "    fields = {'x': 1}\n"
                   "    fields.update(build(args))\n"
                   "    send('x', 'y', **fields)\n")


def test_unprovable_sender_silent_shapes(tmp):
    _silent(tmp, "    send = wrap(ext_cmd)\n"
                 "    fields = {'x': 1}\n"
                 "    send('x', 'y', **fields)\n")
    _silent(tmp, "    send = wrap(ext_cmd)\n"
                 "    fields = {'tab': 'extension', 'x': 1}\n"
                 "    send('x', 'y', **fields)\n")
    _silent(tmp, "    send = wrap(ext_cmd)\n"
                 "    send('x', 'y')\n")


def test_other_branches_unchanged(tmp):
    assert 'cannot be verified' in _scan(
        tmp, "    ext_cmd('x', 'y', **build(args))\n")[0]
    assert 'cannot be verified' in _scan(
        tmp, "    fields = {}\n"
             "    fields.update(build(args))\n"
             "    ext_cmd('x', 'y', **fields)\n")[0]
    assert 'typed /command payload' in _scan(
        tmp, "    cmd = {'id': '_x', 'type': 'close-tab', **extra}\n"
             "    api('PUT', '/command', cmd)\n")[0]
    assert not _scan(
        tmp, "    cmd = {'id': '_x', 'type': 'close-tab'}\n"
             "    api('PUT', '/command', cmd)\n")


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='unprovable_')


if __name__ == '__main__':
    raise SystemExit(main())
