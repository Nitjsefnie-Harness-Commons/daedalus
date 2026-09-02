#!/usr/bin/env python3
"""Pin routing state identity semantics and growth."""
from collections import Counter
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pyroute  # noqa: E402
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402
from _jsroute_receiver import ReceiverIndex  # noqa: E402
from _jsroute_source import record_work  # noqa: E402


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


def _js_member_scan_operations(tmp, count):
    source = Path(tmp) / f'js_member_calls_{count}.js'
    lines = ["const ordinary = () => undefined;\n"]
    for index in range(count):
        lines.append(f"const sender{index} = extCmd;\n")
        lines.append(
            f"const run{index} = () => sender{index}('focus-tab', "
            "{ tab: chromeTab });\n")
        lines.append(f"const box{index} = {{ run: run{index} }};\n")
        lines.append(f"box{index}.run();\n")
    source.write_text(''.join(lines), encoding='utf-8')
    work = Counter()
    violations = js_tab_routing_violations(
        source, source.name, work=work)
    assert len(violations) == count, (count, len(violations))
    return sum(work.values())


def _js_named_scan_operations(tmp, count):
    source = Path(tmp) / f'js_named_calls_{count}.js'
    lines = []
    for index in range(count):
        lines.append(f'const fields{index} = {{ tab: chromeTab }};\n')
        lines.append(f"extCmd('focus-tab', fields{index});\n")
    source.write_text(''.join(lines), encoding='utf-8')
    work = Counter()
    violations = js_tab_routing_violations(
        source, source.name, work=work)
    assert len(violations) == count, (count, len(violations))
    return work


def _js_history_scan_operations(tmp, count):
    source = Path(tmp) / f'js_history_calls_{count}.js'
    lines = ['const box = { run: ordinary };\n']
    for _ in range(count):
        lines.append('box.run = extCmd;\n')
        lines.append("box.run('focus-tab', { tab: chromeTab });\n")
    source.write_text(''.join(lines), encoding='utf-8')
    work = Counter()
    violations = js_tab_routing_violations(
        source, source.name, work=work)
    assert len(violations) == count, (count, len(violations))
    return work


def _history_samples(tmp):
    found = [(count, _js_history_scan_operations(tmp, count))
             for count in (100, 200, 400)]
    return [
        (count, work.get(
            'receiver_history_entries', sum(work.values())))
        for count, work in found]


def _subquadratic_growth(samples):
    return all(
        larger < smaller * 3.5
        for (_, smaller), (_, larger) in zip(samples, samples[1:]))


def _js_net_scan_operations(tmp, count):
    source = Path(tmp) / f'js_net_mentions_{count}.js'
    lines = ["let send = ordinary;\n", "const use = (f) => f;\n"]
    for index in range(count):
        lines.append(
            f"const box{index} = {{ go() {{ send = extCmd; }} }};\n")
        lines.append(f"use(box{index}.go);\n")
    source.write_text(''.join(lines), encoding='utf-8')
    work = Counter()
    js_tab_routing_violations(source, source.name, work=work)
    return work


def test_javascript_net_mentions_scale_without_rescans(tmp):
    """The closing net indexes once; a rescan turns a counter quadratic."""
    found = [(count, _js_net_scan_operations(tmp, count))
             for count in (100, 200, 400)]
    assert all('net_mentions' in work for _, work in found), found
    assert all('net_promotion_tokens' in work for _, work in found), found
    assert all('operation_prefix_bytes' in work
               for _, work in found), found
    for name in ('net_mentions', 'net_span_lookups',
                 'net_container_queries', 'net_promotion_tokens',
                 'operation_prefix_bytes'):
        samples = [(count, work[name]) for count, work in found]
        assert _subquadratic_growth(samples), (name, samples)


def test_javascript_call_replay_scales_without_global_rescans(tmp):
    samples = [
        (count, _js_member_scan_operations(tmp, count))
        for count in (200, 400, 800, 1600)
    ]
    assert _subquadratic_growth(samples), samples
    assert _js_member_scan_operations(tmp, 400) == samples[1][1]


def test_javascript_named_fields_scale_without_prefix_rescans(tmp):
    found = [(count, _js_named_scan_operations(tmp, count))
             for count in (100, 200, 400)]
    assert all('named_event_visits' in work for _, work in found), found
    samples = [(count, work['named_event_visits'])
               for count, work in found]
    assert _subquadratic_growth(samples), samples


def test_javascript_receiver_history_scales_by_indexed_lookups(tmp):
    samples = _history_samples(tmp)
    assert _subquadratic_growth(samples), samples


def test_javascript_growth_gate_rejects_linear_history_mutant(tmp):
    original = ReceiverIndex._latest

    def linear_latest(self, entries, position):
        record_work(self.work, 'receiver_history_queries')
        chosen = None
        needle = (position, ())
        for entry in entries or ():
            if entry > needle:
                break
            chosen = entry[1]
        return chosen

    ReceiverIndex._latest = linear_latest
    try:
        samples = _history_samples(tmp)
    finally:
        ReceiverIndex._latest = original
    assert not _subquadratic_growth(samples), samples


def test_javascript_growth_gate_rejects_quadratic_work(tmp):
    del tmp
    quadratic = [
        (200, 40000), (400, 160000),
        (800, 640000), (1600, 2560000)]
    assert not _subquadratic_growth(quadratic), quadratic


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='routeperf_')


if __name__ == '__main__':
    raise SystemExit(main())
