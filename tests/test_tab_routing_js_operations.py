#!/usr/bin/env python3
"""Operation-discovery and value-transfer boundaries for the JS scanner."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402
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
        ('quoted-compound-write-setter', _SETTER
         + "obj['hook'] += 1;\n" + _SEND, True, True),
        ('quoted-compound-getter-then-setter', "let send = ordinary;\n"
         "const obj = { get hook() { send = extCmd; return 1; },\n"
         "  set hook(v) { send = ordinary; } };\n"
         "obj['hook'] += 1;\n" + _SEND, False, False),
        ('quoted-prefix-update-setter', _SETTER
         + "++obj['hook'];\n" + _SEND, True, True),
        ('dynamic-compound-write-setter', _SETTER
         + "const key = 'hook';\nobj[key] += 1;\n" + _SEND, True, True),
        ('dynamic-logical-write-setter', _SETTER
         + "const key = 'hook';\nobj[key] ||= 1;\n" + _SEND, True, True),
        ('dynamic-prefix-update-setter', _SETTER
         + "const key = 'hook';\n++obj[key];\n" + _SEND, True, True),
        ('dynamic-getter-then-setter', "let send = ordinary;\n"
         "const key = 'hook';\n"
         "const obj = { get hook() { send = extCmd; return 1; },\n"
         "  set hook(v) { send = ordinary; } };\n"
         "obj[key] += 1;\n" + _SEND, False, False),
        ('quoted-hash-compound-setter', "let send = ordinary;\n"
         "const obj = { get '#hook'() { return 0; },\n"
         "  set '#hook'(v) { send = extCmd; } };\n"
         "obj['#hook'] += 1;\n" + _SEND, True, True),
        ('quoted-hash-simple-setter', "let send = ordinary;\n"
         "const obj = { set '#hook'(v) { send = extCmd; } };\n"
         "obj['#hook'] = 1;\n" + _SEND, True, True),
        ('conditional-compound-receiver-present', "let send = ordinary;\n"
         "const choose = true;\n"
         "const obj = choose ? { get hook() { return 0; },\n"
         "  set hook(v) { send = extCmd; } } : null;\n"
         "if (obj) obj.hook += 1;\n" + _SEND, True, True),
        ('conditional-compound-receiver-absent', "let send = ordinary;\n"
         "const choose = false;\n"
         "const obj = choose ? { get hook() { return 0; },\n"
         "  set hook(v) { send = extCmd; } } : null;\n"
         "if (obj) obj.hook += 1;\n" + _SEND, False, True),
        ('comma-key-conditional-receiver-present', "let send = ordinary;\n"
         "const choose = true, key = 'hook';\n"
         "const obj = choose ? { get hook() { return 0; },\n"
         "  set hook(v) { send = extCmd; } } : null;\n"
         "if (obj) obj[key] += 1;\n" + _SEND, True, True),
        ('dynamic-key-ignores-sibling-demoter', "let send = ordinary;\n"
         "const key = 'hook';\n"
         "const obj = { get hook() { return 0; },\n"
         "  set hook(v) { send = extCmd; } };\n"
         "function sibling() { const key = 'other'; void key; }\n"
         "void sibling;\nobj[key] += 1;\n" + _SEND, True, True),
        ('dynamic-key-ignores-sibling-promoter', "let send = ordinary;\n"
         "const key = 'other';\nconst obj = { other: 0,\n"
         "  set hook(v) { send = extCmd; } };\n"
         "function sibling() { const key = 'hook'; void key; }\n"
         "void sibling;\nobj[key] += 1;\n" + _SEND, False, False),
        ('global-quoted-compound-ignores-sibling-demoter',
         "let send = ordinary;\n"
         "globalThis.box = { get hook() { return 0; },\n"
         "  set hook(v) { send = extCmd; } };\n"
         "function sibling() { const box = { set hook(v) {\n"
         "  send = ordinary; } }; void box; }\nvoid sibling;\n"
         "box['hook'] += 1;\n" + _SEND, True, True),
        ('global-quoted-compound-ignores-sibling-promoter',
         "let send = ordinary;\n"
         "globalThis.box = { get hook() { return 0; },\n"
         "  set hook(v) { send = ordinary; } };\n"
         "function sibling() { const box = { set hook(v) {\n"
         "  send = extCmd; } }; void box; }\nvoid sibling;\n"
         "box['hook'] += 1;\n" + _SEND, False, False),
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
        ('setter-parameter-carries-sender', "let f = ordinary;\n"
         "const obj = { set hook(value) { f = value; } };\n"
         "obj.hook = extCmd;\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('setter-parameter-carries-ordinary', "let f = extCmd;\n"
         "const obj = { set hook(value) { f = value; } };\n"
         "obj.hook = ordinary;\n"
         "f('focus-tab', { tab: chromeTab });\n", False, False),
        ('logical-setter-carries-sender-dot', "let f = ordinary;\n"
         "const obj = { get hook() { return null; },\n"
         "  set hook(value) { f = value; } };\n"
         "obj.hook ||= extCmd;\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('logical-setter-carries-sender-quoted', "let f = ordinary;\n"
         "const obj = { get hook() { return null; },\n"
         "  set hook(value) { f = value; } };\n"
         "obj['hook'] ||= extCmd;\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('logical-setter-carries-sender-dynamic', "let f = ordinary;\n"
         "const key = 'hook';\n"
         "const obj = { get hook() { return null; },\n"
         "  set hook(value) { f = value; } };\n"
         "obj[key] ||= extCmd;\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('logical-setter-short-circuits', "let f = ordinary;\n"
         "const key = 'hook';\n"
         "const obj = { get hook() { return extCmd; },\n"
         "  set hook(value) { f = value; } };\n"
         "obj[key] ||= ordinary;\n"
         "f('focus-tab', { tab: chromeTab });\n", False, False),
        ('compound-setter-parameter-unprovable', "let f = ordinary;\n"
         "const obj = { get hook() { return 1; },\n"
         "  set hook(value) { f = value; } };\n"
         "obj.hook += extCmd;\n"
         "if (typeof f === 'function') "
         "f('focus-tab', { tab: chromeTab });\n", False, True),
        ('unknown-key-may-select-setter', "let send = ordinary;\n"
         "function chooseKey() { return 'hook'; }\n"
         "const key = chooseKey();\n"
         "const obj = { set hook(v) { send = extCmd; }, other: 0 };\n"
         "obj[key] = 1;\n" + _SEND, True, True),
        ('unknown-key-may-select-getter', "let send = ordinary;\n"
         "function chooseKey() { return 'hook'; }\n"
         "const key = chooseKey();\n"
         "const obj = { get hook() { send = extCmd; return 0; },\n"
         "  other: 0 };\nvoid obj[key];\n" + _SEND, True, True),
        ('unknown-expression-may-select-class-setter',
         "let send = ordinary;\n"
         "function key() { return '#hook'; }\n"
         "class Obj { set '#hook'(v) { send = extCmd; } }\n"
         "const obj = new Obj();\nobj[key()] = 1;\n" + _SEND,
         True, True),
        ('escaped-quoted-setter-key', "let send = ordinary;\n"
         "const obj = { set '#hook'(v) { send = extCmd; } };\n"
         "obj['\\x23hook'] = 1;\n" + _SEND, True, True),
        ('empty-quoted-setter-key', "let send = ordinary;\n"
         "const obj = { set ''(v) { send = extCmd; } };\n"
         "obj[''] = 1;\n" + _SEND, True, True),
        ('quoted-class-accessor', "let send = ordinary;\n"
         "class Obj { #hook = 0;\n"
         "  set '#hook'(v) { send = extCmd; } }\n"
         "const obj = new Obj();\nobj['#hook'] = 1;\n" + _SEND,
         True, True),
        ('quoted-derived-class-accessor', "let send = ordinary;\n"
         "class Base {}\nclass Obj extends Base {\n"
         "  set '#hook'(v) { send = extCmd; } }\n"
         "const obj = new Obj();\nobj['#hook'] = 1;\n" + _SEND,
         True, True),
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
        ('quoted-compound-write-on-data-inert', "let send = ordinary;\n"
         "const obj = { hook: 1 };\nobj['hook'] += 1;\nvoid obj;\n"
         + _SEND, False, False),
        ('dynamic-compound-write-on-data-inert', "let send = ordinary;\n"
         "const key = 'hook';\nconst obj = { hook: 1 };\n"
         "obj[key] += 1;\nvoid obj;\n" + _SEND, False, False),
        ('quoted-public-key-does-not-reach-private-setter',
         "let send = ordinary;\n"
         "class Obj { get #hook() { return 0; }\n"
         "  set #hook(v) { send = extCmd; } }\n"
         "const obj = new Obj();\nobj['#hook'] += 1;\n" + _SEND,
         False, False),
        ('quoted-key-does-not-reach-derived-private-setter',
         "let send = ordinary;\nclass Base {}\n"
         "class Obj extends Base { set #hook(v) { send = extCmd; } }\n"
         "const obj = new Obj();\nobj['#hook'] = 1;\n" + _SEND,
         False, False),
        ('untracked-dynamic-operation-inert', "let send = ordinary;\n"
         "function chooseKey() { return 'E'; }\n"
         "const key = chooseKey();\nMath[key] += 1;\n" + _SEND,
         False, False),
        ('unresolved-dynamic-key-read-inert', "let send = ordinary;\n"
         "function chooseKey() { return 'hook'; }\n"
         "const key = chooseKey();\nconst obj = { hook: ordinary };\n"
         "const value = obj[key];\nvoid value;\n" + _SEND, False, False),
        ('unresolved-key-read-setter-only-inert',
         "let send = ordinary;\nfunction chooseKey() { return 'hook'; }\n"
         "const key = chooseKey();\n"
         "const obj = { set hook(v) { send = extCmd; } };\n"
         "void obj[key];\n" + _SEND, False, False),
        ('unresolved-dynamic-key-write-inert', "let send = ordinary;\n"
         "function chooseKey() { return 'hook'; }\n"
         "const key = chooseKey();\n"
         "const obj = { hook: 0, other: 0 };\nobj[key] = 1;\n"
         + _SEND, False, False),
        ('unresolved-key-write-getter-only-inert',
         "let send = ordinary;\nfunction chooseKey() { return 'hook'; }\n"
         "const key = chooseKey();\n"
         "const obj = { get hook() { send = extCmd; return 1; } };\n"
         "obj[key] = 1;\n" + _SEND, False, False),
        ('unresolved-key-expression-data-write-inert',
         "let send = ordinary;\nfunction key() { return 'hook'; }\n"
         "const obj = { hook: 0, other: 0 };\nobj[key()] = 1;\n"
         + _SEND, False, False),
        ('escaped-quoted-data-key-inert', "let send = ordinary;\n"
         "const obj = { '#hook': 0 };\n"
         "obj['\\x23hook'] = 1;\n" + _SEND, False, False),
        ('escaped-key-selects-only-ordinary-setter',
         "let send = ordinary;\n"
         "const obj = { set '#hook'(v) { send = ordinary; },\n"
         "  set other(v) { send = extCmd; } };\n"
         "obj['\\x23hook'] = 1;\n" + _SEND, False, False),
        ('empty-quoted-data-key-inert', "let send = ordinary;\n"
         "const obj = { '': 0 };\nobj[''] = 1;\n" + _SEND,
         False, False),
        ('empty-key-selects-only-ordinary-setter',
         "let send = ordinary;\n"
         "const obj = { set ''(v) { send = ordinary; },\n"
         "  set other(v) { send = extCmd; } };\n"
         "obj[''] = 1;\n" + _SEND, False, False),
        ('static-quoted-class-accessor-inert', "let send = ordinary;\n"
         "class Obj { static set '#hook'(v) { send = extCmd; } }\n"
         "const obj = new Obj();\nobj['#hook'] = 1;\n" + _SEND,
         False, False),
        ('getter-only-class-write-inert', "let send = ordinary;\n"
         "class Obj { get '#hook'() { send = extCmd; return 1; } }\n"
         "const obj = new Obj();\nobj['#hook'] = 1;\n" + _SEND,
         False, False),
        ('provably-null-optional-read-inert', "let send = ordinary;\n"
         "const obj = null;\nvoid obj?.hook;\n" + _SEND, False, False),
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
         False, False),
        ('template-shadow-promotes-locally', "let send = ordinary;\n"
         "function run() {\n  let send = ordinary;\n"
         "  const obj = { hook: `${send = extCmd}` };\n  void obj;\n"
         "  send('focus-tab', { tab: chromeTab });\n}\nrun();\n",
         True, True),
        ('template-shadow-leaves-outer-clean', "let send = ordinary;\n"
         "function run() {\n  let send = ordinary;\n"
         "  const obj = { hook: `${send = extCmd}` };\n  void obj;\n}\n"
         "run();\nsend('focus-tab', { tab: chromeTab });\n", False, False),
        ('template-nullish-promotes', "let send = null;\n"
         "const obj = { hook: `${send ??= extCmd}` };\nvoid obj;\n" + send,
         True, True),
        ('template-logical-or-promotes', "let send = null;\n"
         "const obj = { hook: `${send ||= extCmd}` };\nvoid obj;\n" + send,
         True, True),
        ('template-compound-taints', "let send = 0;\n"
         "const obj = { hook: `${send += extCmd}` };\nvoid obj;\n"
         "if (typeof send === 'function') " + send, False, True),
        ('template-postfix-update-taints', "let send = 0;\n"
         "const obj = { hook: `${send++}` };\nvoid obj;\n"
         "if (typeof send === 'function') " + send, False, True),
        ('template-prefix-update-taints', "let send = 0;\n"
         "const obj = { hook: `${++send}` };\nvoid obj;\n"
         "if (typeof send === 'function') " + send, False, True),
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


def test_carried_candidate_uses_sender_diagnostic(tmp):
    source = ("const choose = true;\n"
              "const obj = { get run() {\n"
              "  return choose ? extCmd : ordinary;\n} };\n"
              "const f = obj.run;\n"
              "f('focus-tab', { tab: chromeTab });\n")
    path = Path(tmp) / 'carried-candidate.js'
    runtime, guard = _runtime_and_guard(source, path)
    violations = js_tab_routing_violations(path, path.name)
    assert (runtime, guard) == (True, True), (runtime, violations)
    assert any('cannot resolve' in item for item in violations), violations
    assert any('`tab` in a typed command send' in item
               for item in violations), violations


def test_callable_return_provenance_matches_runtime(tmp):
    cases = [
        ('function-factory-sender',
         "function make() { return extCmd; }\n", True, True),
        ('function-factory-ordinary',
         "function make() { return ordinary; }\n", False, False),
        ('arrow-factory-sender',
         "const make = () => extCmd;\n", True, True),
        ('arrow-factory-ordinary',
         "const make = () => ordinary;\n", False, False),
        ('method-factory-sender',
         "const obj = { make() { return extCmd; } };\n", True, True),
        ('method-factory-ordinary',
         "const obj = { make() { return ordinary; } };\n", False, False),
    ]
    sources = []
    for label, factory, routed, guard in cases:
        call = "obj.make()" if label.startswith('method') else "make()"
        source = (factory + f"const f = {call};\n"
                  "f('focus-tab', { tab: chromeTab });\n")
        sources.append((label, source, routed, guard))
    sources.extend([
        ('getter-direct-sender',
         "const obj = { get run() { return extCmd; } };\n"
         "const f = obj.run;\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('getter-direct-ordinary',
         "const obj = { get run() { return ordinary; } };\n"
         "const f = obj.run;\n"
         "f('focus-tab', { tab: chromeTab });\n", False, False),
        ('chained-getter-extraction-sender',
         "const inner = { get send() { return extCmd; } };\n"
         "const outer = { get value() { return inner; } };\n"
         "let send = ordinary;\nconst f = outer.value.send;\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('chained-getter-extraction-ordinary',
         "const inner = { get send() { return ordinary; } };\n"
         "const outer = { get value() { return inner; } };\n"
         "let send = extCmd;\nconst f = outer.value.send;\n"
         "f('focus-tab', { tab: chromeTab });\n", False, False),
        ('staged-getter-extraction-sender',
         "const inner = { get send() { return extCmd; } };\n"
         "const outer = { get value() { return inner; } };\n"
         "let send = ordinary;\nconst carried = outer.value;\n"
         "const f = carried.send;\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('three-hop-getter-extraction-sender',
         "const leaf = { get send() { return extCmd; } };\n"
         "const inner = { get leaf() { return leaf; } };\n"
         "const outer = { get value() { return inner; } };\n"
         "const f = outer.value.leaf.send;\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('three-hop-getter-extraction-ordinary',
         "const leaf = { get send() { return ordinary; } };\n"
         "const inner = { get leaf() { return leaf; } };\n"
         "const outer = { get value() { return inner; } };\n"
         "const f = outer.value.leaf.send;\n"
         "f('focus-tab', { tab: chromeTab });\n", False, False),
        ('conditional-middle-getter-sender',
         "const inner = { get send() { return extCmd; } };\n"
         "const clean = { get send() { return ordinary; } };\n"
         "const choose = true;\nconst outer = { get value() {\n"
         "  return choose ? inner : clean;\n} };\n"
         "const f = outer.value.send;\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('conditional-middle-getter-ordinary',
         "const inner = { get send() { return extCmd; } };\n"
         "const clean = { get send() { return ordinary; } };\n"
         "const choose = false;\nconst outer = { get value() {\n"
         "  return choose ? inner : clean;\n} };\n"
         "const f = outer.value.send;\n"
         "f('focus-tab', { tab: chromeTab });\n", False, True),
        ('computed-terminal-getter-sender',
         "const inner = { get send() { return extCmd; } };\n"
         "const outer = { get value() { return inner; } };\n"
         "const f = outer.value['send'];\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('computed-terminal-getter-ordinary',
         "const inner = { get send() { return ordinary; } };\n"
         "const outer = { get value() { return inner; } };\n"
         "const f = outer.value['send'];\n"
         "f('focus-tab', { tab: chromeTab });\n", False, False),
        ('aliased-factory-sender',
         "function make() { return extCmd; }\n"
         "const carried = make;\nconst f = carried();\n"
         "f('focus-tab', { tab: chromeTab });\n",
         True, True),
        ('aliased-factory-ordinary',
         "function make() { return ordinary; }\n"
         "const carried = make;\nconst f = carried();\n"
         "f('focus-tab', { tab: chromeTab });\n",
         False, False),
        ('getter-carried-factory-sender',
         "function make() { return extCmd; }\n"
         "const box = { get make() { return make; } };\n"
         "const carried = box.make;\nconst f = carried();\n"
         "f('focus-tab', { tab: chromeTab });\n",
         True, True),
        ('getter-carried-factory-ordinary',
         "function make() { return ordinary; }\n"
         "const box = { get make() { return make; } };\n"
         "const carried = box.make;\nconst f = carried();\n"
         "f('focus-tab', { tab: chromeTab });\n",
         False, False),
        ('getter-invoked-factory-sender',
         "let send = ordinary;\n"
         "const box = { get make() { return () => extCmd; } };\n"
         "const f = box.make();\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('getter-invoked-factory-ordinary',
         "let send = ordinary;\n"
         "const box = { get make() { return () => ordinary; } };\n"
         "const f = box.make();\n"
         "f('focus-tab', { tab: chromeTab });\n", False, False),
        ('getter-invoked-parameter-factory-sender',
         "let send = ordinary;\n"
         "const box = { get make() { return value => value; } };\n"
         "const f = box.make(extCmd);\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('getter-invoked-parameter-factory-ordinary',
         "let send = ordinary;\n"
         "const box = { get make() { return value => value; } };\n"
         "const f = box.make(ordinary);\n"
         "f('focus-tab', { tab: chromeTab });\n", False, False),
        ('getter-invoked-conditional-factory-sender',
         "let send = ordinary;\nconst choose = true;\n"
         "const box = { get make() {\n"
         "  return choose ? (() => extCmd) : (() => ordinary);\n} };\n"
         "const f = box.make();\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('getter-invoked-conditional-factory-ordinary',
         "let send = ordinary;\nconst choose = false;\n"
         "const box = { get make() {\n"
         "  return choose ? (() => extCmd) : (() => ordinary);\n} };\n"
         "const f = box.make();\n"
         "f('focus-tab', { tab: chromeTab });\n", False, True),
        ('conditional-factory-sender',
         "const choose = true;\n"
         "function make() { return choose ? extCmd : ordinary; }\n"
         "const f = make();\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('conditional-factory-ordinary',
         "const choose = false;\n"
         "function make() { return choose ? extCmd : ordinary; }\n"
         "const f = make();\n"
         "f('focus-tab', { tab: chromeTab });\n", False, True),
        ('parameter-factory-sender',
         "function make(value) { return value; }\n"
         "const f = make(extCmd);\n"
         "f('focus-tab', { tab: chromeTab });\n", True, True),
        ('parameter-factory-ordinary',
         "function make(value) { return value; }\n"
         "const f = make(ordinary);\n"
         "f('focus-tab', { tab: chromeTab });\n", False, False),
    ])
    path = Path(tmp) / 'callable-returns.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _, _ in sources]
    expected = [(label, routed, guard)
                for label, _, routed, guard in sources]
    assert observed == expected, observed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsops_')


if __name__ == '__main__':
    raise SystemExit(main())
