#!/usr/bin/env python3
"""Computed property keys in a typed command send, paired with node.

A `tab` spelled through a binding or a concatenation reaches the same send
as the literal spelling, and a key no reader can name leaves its object
unprovable rather than clean.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402
from test_tab_routing_js import _runtime_and_guard  # noqa: E402


def test_computed_tab_keys_match_runtime(tmp):
    """A key the guard cannot read is never silently clean, and a key it
    reads as `tab` is reported whatever spelling carries it."""
    cases = [
        ('literal-read', "extCmd('focus', { ['tab']: chromeTab });\n",
         True, True),
        ('bound-read', "const k = 'tab';\n"
         "extCmd('focus', { [k]: chromeTab });\n", True, True),
        ('renamed-binding-read', "const field = 'tab';\n"
         "extCmd('focus', { [field]: chromeTab });\n", True, True),
        ('concatenated-read', "extCmd('focus', { ['ta' + 'b']: "
         "chromeTab });\n", True, True),
        ('other-bound-read', "const k = 'other';\n"
         "extCmd('focus', { [k]: chromeTab });\n", False, False),
        ('legal-bound-read', "const k = 'tab';\n"
         "extCmd('focus', { [k]: 'extension' });\n", False, False),
        ('legal-concatenated-read', "extCmd('focus', { ['ta' + 'b']: "
         "'extension' });\n", False, False),
        ('unresolved-read', "const pick = () => 'tab';\n"
         "extCmd('focus', { [pick()]: chromeTab });\n", True, True),
        ('literal-write', "const p = {};\np['tab'] = chromeTab;\n"
         "extCmd('focus', { ...p });\n", True, True),
        ('bound-write', "const k = 'tab';\nconst p = {};\n"
         "p[k] = chromeTab;\nextCmd('focus', { ...p });\n", True, True),
        ('renamed-binding-write', "const field = 'tab';\n"
         "const p = {};\np[field] = chromeTab;\n"
         "extCmd('focus', { ...p });\n", True, True),
        ('concatenated-write', "const p = {};\np['ta' + 'b'] = chromeTab;\n"
         "extCmd('focus', { ...p });\n", True, True),
        ('other-bound-write', "const k = 'other';\nconst p = {};\n"
         "p[k] = chromeTab;\nextCmd('focus', { ...p });\n", False, False),
        ('legal-bound-write', "const k = 'tab';\nconst p = {};\n"
         "p[k] = 'extension';\nextCmd('focus', { ...p });\n", False, False),
        ('unresolved-write', "const pick = () => 'tab';\n"
         "const p = {};\np[pick()] = chromeTab;\n"
         "extCmd('focus', { ...p });\n", True, True),
        ('spaced-bracket-write', "const p = {};\np [ 'tab' ] = "
         "chromeTab;\nextCmd('focus', { ...p });\n", True, True),
        ('compared-bracket-write', "const p = {};\np['tab'] == chromeTab;\n"
         "extCmd('focus', { ...p });\n", False, False),
        ('bound-compared-bracket-write', "const k = 'tab';\n"
         "const p = {};\np[k] == chromeTab;\n"
         "extCmd('focus', { ...p });\n", False, False),
    ]
    path = Path(tmp) / 'computed-key.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _, _ in cases]
    expected = [(label, runtime, guard)
                for label, _, runtime, guard in cases]
    assert observed == expected, observed


def test_computed_tab_key_spellings_reach_one_verdict(tmp):
    """The bound spelling is the literal spelling, not a second one."""
    path = Path(tmp) / 'verdict.js'
    literal = "\nextCmd('focus', { ['tab']: chromeTab });\n"
    bound = "const k = 'tab';\nextCmd('focus', { [k]: chromeTab });\n"
    path.write_text(literal, encoding='utf-8')
    direct = js_tab_routing_violations(path, 'key.js')
    path.write_text(bound, encoding='utf-8')
    indirect = js_tab_routing_violations(path, 'key.js')
    assert direct == indirect and len(direct) == 1, (direct, indirect)
    assert '`tab` in a typed command send' in direct[0], direct


def test_computed_tab_write_spellings_reach_one_verdict(tmp):
    """The bound write is the literal write, not a second one."""
    path = Path(tmp) / 'write-verdict.js'
    literal = "\nconst p = {};\np['tab'] = chromeTab;\n" \
              "extCmd('focus', { ...p });\n"
    bound = "const k = 'tab';\nconst p = {};\np[k] = chromeTab;\n" \
            "extCmd('focus', { ...p });\n"
    path.write_text(literal, encoding='utf-8')
    direct = js_tab_routing_violations(path, 'write.js')
    path.write_text(bound, encoding='utf-8')
    indirect = js_tab_routing_violations(path, 'write.js')
    assert direct == indirect and len(direct) == 1, (direct, indirect)
    assert '`tab` in a typed command send' in direct[0], direct


def test_a_bracket_write_resolves_a_tracked_member(tmp):
    """`o.p['tab'] = x` writes the object `o` holds under `p`, which is the
    object a send spreads when the two are the same name. A member the
    model cannot follow names no object here, so a member write that
    merely ends in a sent name is not one."""
    cases = [
        ('aliased-member', "const p = {};\nconst o = { p };\n"
         "o.p['tab'] = chromeTab;\nextCmd('focus', { ...p });\n",
         True, True),
        ('aliased-under-another-name', "const p = {};\nconst q = {};\n"
         "const o = { p: q };\no.p['tab'] = chromeTab;\n"
         "extCmd('focus', { ...q });\n", True, True),
        ('unrelated-member', "const p = {};\nconst q = { p: {} };\n"
         "q.p['tab'] = chromeTab;\n"
         "extCmd('focus', { type: 'focus' });\n", False, False),
        ('legal-aliased-member', "const p = {};\nconst o = { p };\n"
         "o.p['tab'] = 'extension';\n"
         "extCmd('focus', { ...p });\n", False, False),
    ]
    path = Path(tmp) / 'member-write.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _, _ in cases]
    expected = [(label, runtime, guard)
                for label, _, runtime, guard in cases]
    assert observed == expected, observed


def test_a_tab_value_other_than_extension_is_a_violation(tmp):
    """`extension` is the one value `tab` may carry, so a `tab` carrying
    another string is a violation whatever spelling writes it. The clean
    twins elsewhere all carry `extension`; without this row the predicate
    accepts any word and every one of them still passes."""
    cases = [
        ('literal-read', "extCmd('focus', { tab: 'garbage' });\n"),
        ('bound-read', "const k = 'tab';\n"
         "extCmd('focus', { [k]: 'garbage' });\n"),
        ('near-miss-read', "extCmd('focus', { tab: 'notextension' });\n"),
        ('literal-write', "const p = {};\np['tab'] = 'garbage';\n"
         "extCmd('focus', { ...p });\n"),
        ('bound-write', "const k = 'tab';\nconst p = {};\n"
         "p[k] = 'notextension';\nextCmd('focus', { ...p });\n"),
    ]
    path = Path(tmp) / 'other-value.js'
    for label, source in cases:
        path.write_text(source, encoding='utf-8')
        found = js_tab_routing_violations(path, 'value.js')
        assert len(found) == 1, (label, found)
        assert '`tab` in a typed command send' in found[0], (label, found)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jskeys_')


if __name__ == '__main__':
    raise SystemExit(main())
