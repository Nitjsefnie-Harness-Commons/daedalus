#!/usr/bin/env python3
"""Template interpolations are read as the code the mask exposes.

An interpolation is ordinary code to every walk, so an accessor reached
through one resolves exactly and a template that never opens one runs
nothing. A write whose value the walk cannot follow taints its binding
by the operator that wrote it, inside a template or not.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402
from test_tab_routing_js import _runtime_and_guard  # noqa: E402


_SEND = "send('focus-tab', { tab: chromeTab });\n"


def _assert_rows(tmp, name, rows):
    """Assert the (runtime, guard) pair of every row at once."""
    path = Path(tmp) / name
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _, _ in rows]
    expected = [(label, runtime, guard)
                for label, _, runtime, guard in rows]
    assert observed == expected, observed


def test_template_accessor_operations_match_runtime(tmp):
    """An accessor reached through an interpolation resolves exactly."""
    rows = [
        ('getter-read-promotes', "let send = ordinary;\n"
         "const m = { get hook() { send = extCmd; return 1; } };\n"
         "const s = `${m.hook}`;\nvoid s;\n" + _SEND, True, True),
        ('getter-read-demotes', "let send = extCmd;\n"
         "const m = { get hook() { send = ordinary; return 1; } };\n"
         "const s = `${m.hook}`;\nvoid s;\n" + _SEND, False, False),
        ('setter-write-promotes', "let send = ordinary;\n"
         "const m = { set hook(v) { send = extCmd; } };\n"
         "const s = `${(m.hook = 1)}`;\nvoid s;\n" + _SEND, True, True),
        ('setter-write-demotes', "let send = extCmd;\n"
         "const m = { set hook(v) { send = ordinary; } };\n"
         "const s = `${(m.hook = 1)}`;\nvoid s;\n" + _SEND, False, False),
        ('compound-accessor-promotes', "let send = ordinary;\n"
         "const m = { get hook() { return 1; },\n"
         "  set hook(v) { send = extCmd; } };\n"
         "const s = `${m.hook += 1}`;\nvoid s;\n" + _SEND, True, True),
        ('compound-accessor-demotes', "let send = extCmd;\n"
         "const m = { get hook() { return 1; },\n"
         "  set hook(v) { send = ordinary; } };\n"
         "const s = `${m.hook += 1}`;\nvoid s;\n" + _SEND, False, False),
        ('nested-template-promotes', "let send = ordinary;\n"
         "const m = { get hook() { send = extCmd; return 1; } };\n"
         "const s = `a${`b${m.hook}`}`;\nvoid s;\n" + _SEND, True, True),
        ('nested-template-demotes', "let send = extCmd;\n"
         "const m = { get hook() { send = ordinary; return 1; } };\n"
         "const s = `a${`b${m.hook}`}`;\nvoid s;\n" + _SEND, False, False),
        ('method-call-promotes', "let send = ordinary;\n"
         "const m = { go() { send = extCmd; return 1; } };\n"
         "const s = `${m.go()}`;\nvoid s;\n" + _SEND, True, True),
        ('method-call-demotes', "let send = extCmd;\n"
         "const m = { go() { send = ordinary; return 1; } };\n"
         "const s = `${m.go()}`;\nvoid s;\n" + _SEND, False, False),
        ('literal-mention-runs-nothing', "let send = ordinary;\n"
         "const m = { get hook() { send = extCmd; return 1; } };\n"
         "const s = `m.hook`;\nvoid s;\n" + _SEND, False, False),
        ('literal-mention-keeps-sender', "let send = extCmd;\n"
         "const m = { get hook() { send = ordinary; return 1; } };\n"
         "const s = `m.hook`;\nvoid s;\n" + _SEND, True, True),
    ]
    _assert_rows(tmp, 'template-accessor.js', rows)


def test_unresolvable_writes_taint_inside_and_outside_templates(tmp):
    """A write the value walk cannot follow taints by its operator,
    rather than by where it sits."""
    rows = [
        ('plain-logical-or-promotes', "let send = null;\n"
         "send ||= extCmd;\n" + _SEND, True, True),
        ('plain-nullish-promotes', "let send = null;\n"
         "send ??= extCmd;\n" + _SEND, True, True),
        ('plain-logical-and-promotes', "let send = ordinary;\n"
         "send &&= extCmd;\n" + _SEND, True, True),
        ('template-logical-or-promotes', "let send = null;\n"
         "const obj = { hook: `${send ||= extCmd}` };\nvoid obj;\n"
         + _SEND, True, True),
        ('plain-compound-taints', "let send = 0;\nsend += extCmd;\n"
         "if (typeof send === 'function') " + _SEND, False, True),
        ('plain-postfix-update-taints', "let send = 0;\nsend++;\n"
         "if (typeof send === 'function') " + _SEND, False, True),
        ('plain-prefix-update-taints', "let send = 0;\n++send;\n"
         "if (typeof send === 'function') " + _SEND, False, True),
        ('escaped-interpolation-runs-nothing', "let send = ordinary;\n"
         "const obj = { hook: `\\${(send = extCmd, 1)}` };\nvoid obj;\n"
         + _SEND, False, False),
    ]
    _assert_rows(tmp, 'opaque-write.js', rows)


def test_unterminated_template_still_answers(tmp):
    """Backslashes with no closing backtick is what a backtracking
    template regex never finishes on, so answering at all is the
    assertion - a hung scan has no verdict to compare."""
    path = Path(tmp) / 'unterminated.js'
    path.write_text(
        "let send = extCmd;\n" + _SEND + "const s = `" + '\\' * 5000 + ";\n",
        encoding='utf-8')
    violations = js_tab_routing_violations(path, path.name)
    assert violations, violations


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jstemplate_')


if __name__ == '__main__':
    raise SystemExit(main())
