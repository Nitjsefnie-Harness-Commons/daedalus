#!/usr/bin/env python3
"""Writes an async body defers past a synchronous caller's send."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsroute_harness import (paired as _paired,  # noqa: E402
                              runtime_and_guard as _runtime_and_guard)


def test_await_deferred_writes_fail_closed(tmp):
    head = ("let promote = ordinary;\n"
            "promote = () => extCmd('focus-tab', { tab: chromeTab });\n")
    demotion = "promote = () => ordinary;"
    cases = [
        ('async-arrow',
         head + "const f = async () => { await 0; " + demotion + " }; "
         "f(); promote();\n", True),
        ('async-method',
         head + "const obj = { async run() { await 0; " + demotion
         + " } }; obj.run(); promote();\n", True),
        ('async-declaration',
         head + "async function f() { await 0; " + demotion
         + " } f(); promote();\n", True),
        ('deferred-call',
         head + "function demote() { " + demotion + " } "
         "const f = async () => { await 0; demote(); }; "
         "f(); promote();\n", True),
    ]
    path = Path(tmp) / 'await-boundary.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = _paired(cases)
    assert observed == expected, observed


def test_conditional_await_over_report_is_deliberate(tmp):
    head = ("let promote = ordinary;\n"
            "promote = () => extCmd('focus-tab', { tab: chromeTab });\n")
    demotion = "promote = () => ordinary;"
    cases = [
        ('await-not-taken',
         head + "const c = false;\n"
         "const f = async () => { if (c) { await 0; } " + demotion
         + " };\nf(); promote();\n", (False, True)),
    ]
    path = Path(tmp) / 'await-conditional.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = _paired(cases)
    assert observed == expected, observed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsawait_')


if __name__ == '__main__':
    raise SystemExit(main())
