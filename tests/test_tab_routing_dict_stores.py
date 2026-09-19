#!/usr/bin/env python3
"""Callables stored into mappings report when invoked through the mapping."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402

_CALL = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"


def _verdict(tmp, body):
    return _tracked_focus_verdict(tmp, body, counts=True)


def test_subscript_store_reports(tmp):
    body = ('send = ordinary\nbox = {}\n'
            f'box["k"] = lambda: {_CALL}\n'
            'send = ext_cmd\nreturn box["k"]()')
    assert _verdict(tmp, body) == (1, 1)


def test_copied_mapping_invocation_reports(tmp):
    body = ('send = ordinary\nbox = {}\n'
            f'box["k"] = lambda: {_CALL}\n'
            'send = ext_cmd\ncopy = dict(box)\nreturn copy["k"]()')
    assert _verdict(tmp, body) == (1, 1)


def test_copied_key_iteration_stays_clean(tmp):
    body = ('send = ordinary\nbox = {}\n'
            f'box["k"] = lambda: {_CALL}\n'
            'send = ext_cmd\ncopy = dict(box)\nlist(copy)\nlist(box)')
    assert _verdict(tmp, body) == (0, 0)


def test_clean_store_invocation_stays_clean(tmp):
    body = ('send = ordinary\nbox = {}\n'
            'box["k"] = lambda: ordinary()\n'
            'send = ext_cmd\nreturn box["k"]()')
    assert _verdict(tmp, body) == (0, 0)


def test_deleted_holder_copy_invocation_reports(tmp):
    body = ('send = ordinary\nbox = {}\n'
            f'box["k"] = lambda: {_CALL}\n'
            'send = ext_cmd\ncopy = dict(box)\ndel box\nreturn copy["k"]()')
    assert _verdict(tmp, body) == (1, 1)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dictstores_')


if __name__ == '__main__':
    raise SystemExit(main())
