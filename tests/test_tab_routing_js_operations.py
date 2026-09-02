#!/usr/bin/env python3
"""Operation-discovery and value-transfer boundaries for the JS scanner."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_js import _runtime_and_guard  # noqa: E402


_GETTER = ("let send = ordinary;\n"
           "const obj = { get hook() { send = extCmd; return 1; } };\n")
_SETTER = ("let send = ordinary;\n"
           "const obj = { set hook(v) { send = extCmd; } };\n")
_SEND = "send('focus-tab', { tab: chromeTab });\n"


def test_operation_discovery_matches_runtime(tmp):
    cases = [
        ('assign-source-getter', _GETTER + "Object.assign({}, obj);\n"
         + _SEND, True, True),
        ('assign-paren-source-getter', _GETTER
         + "Object.assign({}, (obj));\n" + _SEND, True, True),
        ('values-paren-getter', _GETTER + "Object.values((obj));\n" + _SEND,
         True, True),
        ('nested-destructure-getter', _GETTER
         + "const { hook: { x } } = obj;\nvoid x;\n" + _SEND, True, True),
        ('spread-after-data-getter', _GETTER
         + "const copy = { x: 1, ...obj };\nvoid copy;\n" + _SEND, True, True),
        ('second-spread-getter', _GETTER
         + "const clean = { x: 1 };\n"
         "const copy = { ...clean, ...obj };\nvoid copy;\n" + _SEND,
         True, True),
        ('spread-into-call-argument', "let send = ordinary;\n"
         "const obj = { *[Symbol.iterator]() { send = extCmd; } };\n"
         "const consume = (value) => value;\nconsume(...obj);\n" + _SEND,
         True, True),
        ('parameter-destructure-getter', _GETTER
         + "function consume({ hook }) { void hook; }\nconsume(obj);\n"
         + _SEND, True, True),
        ('compound-write-setter', _SETTER + "obj.hook += 1;\n" + _SEND,
         True, True),
        ('logical-write-setter', _SETTER + "obj.hook ||= 1;\n" + _SEND,
         True, True),
        ('prefix-update-setter', _SETTER + "++obj.hook;\n" + _SEND,
         True, True),
        ('optional-call-on-method-value', "let send = ordinary;\n"
         "const obj = { run() { send = extCmd; } };\nobj.run?.();\n" + _SEND,
         True, True),
        ('parenthesized-optional-receiver', "let send = ordinary;\n"
         "const obj = { run() { send = extCmd; } };\n(obj)?.run();\n" + _SEND,
         True, True),
        ('captured-shorthand-method', "let send = ordinary;\n"
         "const obj = { run() { send = extCmd; } };\n"
         "const f = obj.run;\nf();\n" + _SEND, True, True),
        ('getter-returned-function-call', "let send = ordinary;\n"
         "const obj = { get run() { return () => { send = extCmd; }; } };\n"
         "const f = obj.run;\nf();\n" + _SEND, True, True),
        ('method-call-via-call', "let send = ordinary;\n"
         "const obj = { run() { send = extCmd; } };\nobj.run.call(obj);\n"
         + _SEND, True, True),
        ('method-call-via-bind', "let send = ordinary;\n"
         "const obj = { run() { send = extCmd; } };\n"
         "const f = obj.run.bind(obj);\nf();\n" + _SEND, True, True),
        ('dollar-receiver-safe', "let send = ordinary;\n"
         "const obj = { run() { send = extCmd; } };\n"
         "const $obj = { run() { send = ordinary; } };\nobj.run();\n" + _SEND,
         True, True),
        ('dollar-receiver-route', "let send = ordinary;\n"
         "const obj = { run() { send = ordinary; } };\n"
         "const $obj = { run() { send = extCmd; } };\nobj.run();\n" + _SEND,
         False, False),
        ('receiver-after-dollar-assignment', "let send = ordinary;\n"
         "const obj = { run() { send = extCmd; } };\nlet $obj;\n"
         "$obj = { run() { send = ordinary; } };\nobj.run();\n" + _SEND,
         True, True),
        ('optional-bare-call-on-carried', "let send = ordinary;\n"
         "const obj = { run() { send = extCmd; } };\nlet f = ordinary;\n"
         "f = obj.run;\nf?.();\n" + _SEND, True, True),
        ('generator-forof-taints', "let send = ordinary;\n"
         "const obj = { *run() { send = extCmd; } };\n"
         "for (const value of obj.run()) { void value; }\n" + _SEND,
         True, True),
        ('array-spread-of-iterator-taints', "let send = ordinary;\n"
         "const obj = { *[Symbol.iterator]() { send = extCmd; } };\n"
         "const list = [...obj];\nvoid list;\n" + _SEND, True, True),
        ('compound-write-unreadable-taints', "let send = ordinary;\n"
         "const base = { x: 1 };\n"
         "const obj = { ...base, set hook(v) { send = extCmd; } };\n"
         "obj.hook ||= 1;\n" + _SEND, True, True),
    ]
    path = Path(tmp) / 'operations.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _, _ in cases]
    expected = [(label, routed, guard) for label, _, routed, guard in cases]
    assert observed == expected, observed


def test_operation_inertness_matches_runtime(tmp):
    """Provable inertness the new limbs must not take away."""
    cases = [
        ('delete-getter-inert', _GETTER + "delete obj.hook;\n" + _SEND,
         False, False),
        ('delete-data-property-inert', "let send = ordinary;\n"
         "const obj = { hook: 1 };\ndelete obj.hook;\nvoid obj;\n" + _SEND,
         False, False),
        ('delete-computed-key-inert', "let send = ordinary;\n"
         "const key = 'hook';\nconst obj = { hook: 1 };\ndelete obj[key];\n"
         "void obj;\n" + _SEND, False, False),
        ('compound-write-on-data-inert', "let send = ordinary;\n"
         "const obj = { hook: 1 };\nobj.hook += 1;\nvoid obj;\n" + _SEND,
         False, False),
        ('prefix-update-on-data-inert', "let send = ordinary;\n"
         "const obj = { hook: 1 };\n++obj.hook;\nvoid obj;\n" + _SEND,
         False, False),
        ('invoked-generator-inert', "let send = ordinary;\n"
         "const obj = { *run() { send = extCmd; } };\n"
         "const iterator = obj.run();\nvoid iterator;\n" + _SEND,
         False, False),
        ('invoked-async-generator-inert', "let send = ordinary;\n"
         "const obj = { async *run() { send = extCmd; } };\n"
         "const iterator = obj.run();\nvoid iterator;\n" + _SEND,
         False, False),
        ('generator-creation-in-literal-inert', "let send = ordinary;\n"
         "const obj = { *run() { send = extCmd; } };\n"
         "const spare = { it: obj.run() };\nvoid spare;\n" + _SEND,
         False, False),
        ('carry-no-later-use-inert', "let send = ordinary;\n"
         "const obj = { run() { send = extCmd; } };\n"
         "const f = obj.run;\nvoid f;\n" + _SEND, False, False),
        ('carry-call-before-transfer-inert', "let send = ordinary;\n"
         "let f = ordinary;\n"
         "const obj = { run() { send = extCmd; } };\n"
         "f();\nf = obj.run;\n" + _SEND, False, False),
        ('unrelated-template-inert', "let send = ordinary;\n"
         "const other = { x: `${1}` };\nvoid other.x;\n" + _SEND,
         False, False),
        ('template-in-uninvoked-method-inert', "let send = ordinary;\n"
         "const other = { f() { return `${send = extCmd}`; } };\n"
         "void other;\n" + _SEND, False, False),
        ('template-in-uninvoked-function-data-inert', "let send = ordinary;\n"
         "const other = { f: function () { return `${send = extCmd}`; } };\n"
         "void other.f;\n" + _SEND, False, False),
    ]
    path = Path(tmp) / 'inertness.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _, _ in cases]
    expected = [(label, routed, guard) for label, _, routed, guard in cases]
    assert observed == expected, observed


def test_round7_boundaries_match_runtime(tmp):
    """Duplicate keys, spreads, accessors, optionality and construction."""
    getter = ("let send = ordinary;\n"
              "const obj = { get hook() { send = extCmd; return 1; } };\n")
    setter = ("let send = ordinary;\n"
              "const obj = { set hook(v) { send = extCmd; } };\n")
    send = _SEND
    cases = [
        ('dup-key-method-last-promotes', "let send = ordinary;\n"
         "const method = { promote() { send = ordinary; },\n"
         "  promote() { send = extCmd; } };\nmethod.promote();\n" + send,
         True, True),
        ('dup-key-method-last-demotes', "let send = extCmd;\n"
         "const method = { promote() { send = extCmd; },\n"
         "  promote() { send = ordinary; } };\nmethod.promote();\n" + send,
         False, False),
        ('dup-key-data-last-promotes', "let send = ordinary;\n"
         "const method = { promote: ordinary,\n"
         "  promote: () => { send = extCmd; } };\nmethod.promote();\n" + send,
         True, True),
        ('dup-key-data-last-demotes', "let send = ordinary;\n"
         "const method = { promote: () => { send = extCmd; },\n"
         "  promote: ordinary };\nmethod.promote();\n" + send, False, False),
        ('template-interpolation-promotes', "let send = ordinary;\n"
         "const obj = { hook: `a${send = extCmd}b` };\nvoid obj;\n" + send,
         True, True),
        ('template-interpolation-demotes', "let send = extCmd;\n"
         "const obj = { hook: `a${send = ordinary}b` };\nvoid obj;\n" + send,
         False, True),
        ('function-data-capture-inert', "let send = ordinary;\n"
         "const obj = { hook: function () { send = extCmd; } };\n"
         "const value = obj.hook;\nvoid value;\n" + send, False, False),
        ('function-data-call-still-routes', "let send = ordinary;\n"
         "const obj = { hook: function () { send = extCmd; } };\n"
         "obj.hook();\n" + send, True, True),
        ('provable-optional-demotion', "let send = extCmd;\n"
         "const method = { demote() { send = ordinary; } };\n"
         "method?.demote();\n" + send, False, False),
        ('provable-optional-promotion', "let send = ordinary;\n"
         "const method = { promote() { send = extCmd; } };\n"
         "method?.promote();\n" + send, True, True),
        ('spread-discharged-by-later-key', "let send = extCmd;\n"
         "const base = { promote() { send = extCmd; } };\n"
         "const method = { ...base, promote() { send = ordinary; } };\n"
         "method.promote();\n" + send, False, False),
        ('spread-after-key-taints', "let send = ordinary;\n"
         "const base = { promote() { send = extCmd; } };\n"
         "const method = { promote() { send = ordinary; }, ...base };\n"
         "method.promote();\n" + send, True, True),
        ('accessor-prefix-not-invented', "let send = ordinary;\n"
         "const obj = { get ru() { send = extCmd; },\n"
         "  run() { } };\nobj.run();\n" + send, False, False),
        ('division-value-read-silent', "let send = ordinary;\n"
         "const obj = { hook: 4 / 2 };\nconst v = obj.hook;\nvoid v;\n"
         + send, False, False),
        ('helper-value-read-silent', "let send = ordinary;\n"
         "const makeValue = () => 1;\n"
         "const obj = { hook: makeValue() };\nconst v = obj.hook;\nvoid v;\n"
         + send, False, False),
        ('escaped-newline-string-read-silent', "let send = ordinary;\n"
         'const obj = { hook: "a\\\nb" };\nconst v = obj.hook;\nvoid v;\n'
         + send, False, False),
        ('bracket-getter-read-taints', getter
         + "const v = obj['hook'];\nvoid v;\n" + send, True, True),
        ('dynamic-bracket-getter-read-taints', getter
         + "const key = 'hook';\nconst v = obj[key];\nvoid v;\n" + send,
         True, True),
        ('optional-dot-getter-read-taints', getter
         + "const v = obj?.hook;\nvoid v;\n" + send, True, True),
        ('optional-bracket-getter-read-taints', getter
         + "const v = obj?.['hook'];\nvoid v;\n" + send, True, True),
        ('bracket-setter-write-taints', setter
         + "obj['hook'] = 1;\n" + send, True, True),
        ('destructuring-getter-read-taints', getter
         + "const { hook } = obj;\nvoid hook;\n" + send, True, True),
        ('object-spread-getter-read-taints', getter
         + "const copy = { ...obj };\nvoid copy;\n" + send, True, True),
        ('object-assign-setter-taints', setter
         + "Object.assign(obj, { hook: 1 });\n" + send, True, True),
        ('object-values-getter-read-taints', getter
         + "const values = Object.values(obj);\nvoid values;\n" + send,
         True, True),
        ('accessor-halves-unrelated', "let send = ordinary;\n"
         "const obj = { get hook() { send = extCmd; return 1; }, x: 1,\n"
         "  set hook(v) { send = ordinary; } };\nvoid obj.hook;\n" + send,
         True, True),
        ('setter-retained-across-getter', "let send = ordinary;\n"
         "const obj = { set hook(v) { send = extCmd; },\n"
         "  get hook() { return 1; } };\nobj.hook = 2;\n" + send,
         True, True),
        ('conditional-receiver-absent', "let send = extCmd;\n"
         "let obj = null;\nconst flag = false;\n"
         "if (flag) obj = { demote() { send = ordinary; } };\n"
         "obj?.demote();\n" + send, True, True),
    ]
    path = Path(tmp) / 'round7.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _, _ in cases]
    expected = [(label, routed, guard) for label, _, routed, guard in cases]
    assert observed == expected, observed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsops_')


if __name__ == '__main__':
    raise SystemExit(main())
