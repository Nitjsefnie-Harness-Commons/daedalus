#!/usr/bin/env python3
"""Constant-key setdefault over an entry stored under a dynamic key."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402

_CALL = 'send("_focus", "focus-tab", tab=args.chrome_tab)'
_PRELUDE = (f'send = ordinary\n'
            'def maker():\n'
            f'    return lambda: {_CALL}\n'
            'def relay(): return maker()\n'
            'def pair(): return relay(), ordinary\n')
_STORE = 'd[str(args.chrome_tab)] = relay()'
_READ = 'x = d.setdefault("323", ordinary)\nsend = ext_cmd\nreturn x()'


def _verdict(tmp, stores, read=_READ):
    return _tracked_focus_verdict(
        tmp, _PRELUDE + ''.join(f'{store}\n' for store in stores) + read,
        counts=True)


def test_constant_key_read_joins_a_dynamic_key_entry(tmp):
    assert _verdict(tmp, ['d = {}', _STORE]) == (1, 1)


def test_constant_key_read_joins_a_dynamic_entry_beside_its_constant(tmp):
    assert _verdict(tmp, ['d = {"323": ordinary}', _STORE]) == (1, 1)


def test_constant_key_read_of_an_occupied_constant_stays_clean(tmp):
    assert _verdict(tmp, ['d = {}', 'd["323"] = ordinary']) == (0, 0)


def test_constant_key_read_over_a_clean_dynamic_entry_stays_clean(tmp):
    stores = ['d = {}', 'd[str(args.chrome_tab)] = ordinary']
    assert _verdict(tmp, stores) == (0, 0)


def test_constant_key_read_of_an_unoccupied_constant_stays_clean(tmp):
    assert _verdict(tmp, ['d = {}', 'd["7"] = ordinary']) == (0, 0)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='tab_routing_setdefault_')


if __name__ == '__main__':
    raise SystemExit(main())
