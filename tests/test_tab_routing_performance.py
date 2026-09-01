#!/usr/bin/env python3
"""Pin routing state identity semantics and growth."""
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pyroute  # noqa: E402
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402


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
        assert not _pyroute.py_tab_routing_violations(source, source.name)
    finally:
        _pyroute._copy_state_pair = original
    return copies


def test_evaluated_alias_state_matches_runtime(tmp):
    source = Path(tmp) / 'evaluated_alias.py'
    source.write_text(
        'from fake_route import ext_cmd\n\n'
        'def ordinary(*args, **kwargs):\n'
        "    return ('ordinary', args, kwargs)\n\n"
        'def go(args):\n'
        '    send = ext_cmd\n'
        '    if not args.flag:\n'
        '        send = ordinary\n'
        '    other, send = send, (send := ordinary)\n'
        "    return other('_focus', 'focus-tab', "
        'tab=int(args.chrome_tab))\n',
        encoding='utf-8')
    calls = []

    def ext_cmd(*args, **kwargs):
        calls.append((args, kwargs))

    sender = ModuleType('fake_route')
    sender.ext_cmd = ext_cmd
    sys.modules['fake_route'] = sender
    namespace = {}
    try:
        # pylint: disable-next=exec-used
        exec(compile(source.read_text(encoding='utf-8'), source, 'exec'),
             namespace)
        namespace['go'](SimpleNamespace(flag=True, chrome_tab=17))
    finally:
        sys.modules.pop('fake_route', None)
    violations = _pyroute.py_tab_routing_violations(source, source.name)
    assert calls == [(('_focus', 'focus-tab'), {'tab': 17})], calls
    assert violations == [
        f'{source.name}:11: ext_cmd keyword `tab`'], violations
    assert len(violations) == len(calls)


def test_state_neutral_short_circuits_scale_linearly(tmp):
    narrow = _scanner_copies(tmp, 4)
    wide = _scanner_copies(tmp, 8)
    assert wide <= narrow * 2, (narrow, wide)


def _js_scan_seconds(tmp, count):
    source = Path(tmp) / f'js_calls_{count}.js'
    lines = ["const ordinary = () => undefined;\n"]
    for index in range(count):
        lines.append(f"const callable{index} = () => ordinary();\n")
        lines.append(f"callable{index}();\n")
    source.write_text(''.join(lines), encoding='utf-8')
    started = time.perf_counter()
    assert not js_tab_routing_violations(source, source.name)
    return time.perf_counter() - started


def test_javascript_call_replay_scales_without_global_rescans(tmp):
    narrow = _js_scan_seconds(tmp, 20)
    wide = _js_scan_seconds(tmp, 40)
    assert wide < 1.5, (narrow, wide)
    assert wide <= narrow * 6, (narrow, wide)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='routeperf_')


if __name__ == '__main__':
    raise SystemExit(main())
