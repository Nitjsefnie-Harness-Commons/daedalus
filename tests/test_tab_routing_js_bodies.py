#!/usr/bin/env python3
"""Object bodies, reach boundaries and read classifications, round 1-6."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_js import _runtime_and_guard  # noqa: E402


_METHOD_DEMOTION = ("let send = ordinary;\nlet promote = ordinary;\n"
                    "const method = { demote() { promote = () => ordinary; }"
                    " };\n"
                    "promote = () => extCmd('focus-tab', { tab: chromeTab });"
                    "\n")


def test_object_method_bodies_match_runtime(tmp):
    cases = [
        ('method-demotion-before-call', _METHOD_DEMOTION
         + "method.demote();\npromote();\n", False),
        ('method-demotion-between-calls', _METHOD_DEMOTION
         + "promote();\nmethod.demote();\npromote();\n", True),
        ('uninvoked-method-demotion', _METHOD_DEMOTION
         + "void method;\npromote();\n", True),
        ('method-receiver-alias', _METHOD_DEMOTION
         + "const alias = method;\nalias.demote();\npromote();\n", False),
        ('method-called-in-body', "function run() { method.demote(); }\n"
         + _METHOD_DEMOTION + "run();\npromote();\n", False),
        ('conditional-method-demotion', _METHOD_DEMOTION
         + "const flag = false;\nif (flag) method.demote();\npromote();\n",
         True),
        ('uninvoked-sibling-method', "let promote = ordinary;\n"
         "const method = { demote() { promote = () => ordinary; },\n"
         "  keep() { } };\n"
         "promote = () => extCmd('focus-tab', { tab: chromeTab });\n"
         "method.keep();\npromote();\n", True),
        ('method-promotion', "let send = ordinary;\n"
         "const method = { promote() { send = extCmd; } };\n"
         "method.promote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
    ]
    path = Path(tmp) / 'methods.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = [(label, value, value) for label, _, value in cases]
    assert observed == expected, observed


_HANDOFF = ("let send = ordinary;\n"
            "let route = () => extCmd('focus-tab', { tab: chromeTab });\n")
_PROMOTER = ("let send = ordinary;\n"
             "const method = { promote() { send = extCmd; } };\n")


def test_reach_boundaries_match_runtime(tmp):
    cases = [
        ('alias-invocation-after-demotion', _HANDOFF
         + "const alias = route;\nroute = ordinary;\nalias();\nroute();\n",
         True),
        ('demotion-in-uninvoked-body', _HANDOFF
         + "function outer() { demote(); }\n"
         "function demote() { route = ordinary; }\nroute();\n", True),
        ('sender-argument-hand-off', _HANDOFF
         + "extCmd('relay', {}, {}, route);\nroute = ordinary;\nroute();\n"
         "const handed = calls.find(entry => entry[0] === 'relay')[3];\n"
         "handed();\n", True),
        ('escaped-callback', _HANDOFF
         + "function handOff(value) { kept = value; }\n"
         "let kept = ordinary;\nhandOff(route);\nroute = ordinary;\n"
         "route();\nkept();\n", True),
        ('bracket-member-promotion', _PROMOTER
         + "method['promote']();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('optional-chain-promotion', _PROMOTER
         + "method?.promote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('function-property-promotion',
         "let send = ordinary;\n"
         "const method = { promote: function () { send = extCmd; } };\n"
         "method.promote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('arrow-property-promotion',
         "let send = ordinary;\n"
         "const method = { promote: () => { send = extCmd; } };\n"
         "method.promote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
    ]
    path = Path(tmp) / 'reach.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = [(label, value, value) for label, _, value in cases]
    assert observed == expected, observed


_ROUTER = "let route = () => extCmd('focus-tab', { tab: chromeTab });\n"


def test_round3_boundaries_match_runtime(tmp):
    cases = [
        ('escaped-alias-holder', _ROUTER
         + "let alias = route;\n"
         "extCmd('relay', {}, {}, alias);\n"
         "route = ordinary;\nalias = ordinary;\nroute();\nalias();\n"
         "const handed = calls.find(entry => entry[0] === 'relay')[3];\n"
         "handed();\n", True),
        ('uninvoked-async-demoter', "let send = extCmd;\n"
         "const method = { async demote() { send = ordinary; } };\n"
         "void method;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('uninvoked-generator-demoter', "let send = extCmd;\n"
         "const method = { *demote() { send = ordinary; } };\n"
         "void method;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('uninvoked-accessor-demoter', "let send = extCmd;\n"
         "const method = { get demote() { send = ordinary; } };\n"
         "void method;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('uninvoked-computed-demoter', "let send = extCmd;\n"
         "const method = { ['demote']() { send = ordinary; } };\n"
         "void method;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('uninvoked-async-promoter', "let send = ordinary;\n"
         "const method = { async promote() { send = extCmd; } };\n"
         "void method;\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('uninvoked-computed-promoter', "let send = ordinary;\n"
         "const method = { ['promote']() { send = extCmd; } };\n"
         "void method;\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('unproved-spread-promoter', "let send = ordinary;\n"
         "const base = { promote() { send = extCmd; } };\n"
         "const method = { ...base };\nmethod.promote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('fixpoint-late-promotion-before-outer-call', "let route = ordinary;\n"
         "function outer() { route(); }\n"
         "route = () => extCmd('focus-tab', { tab: chromeTab });\n"
         "outer();\nroute = ordinary;\nroute();\n", True),
        ('optional-null-receiver-demotion', "let send = extCmd;\n"
         "let method = null;\nconst flag = false;\n"
         "if (flag) method = { demote() { send = ordinary; } };\n"
         "method?.demote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('named-property-captures-old-function', _ROUTER
         + "const method = { run: route };\nroute = ordinary;\n"
         "method.run();\nroute();\n", True),
        ('unproved-dynamic-property-capture', "let send = ordinary;\n"
         "const key = 'run';\n" + _ROUTER
         + "const method = { [key]: route };\nroute = ordinary;\n"
         "method.run();\nroute();\n", True),
    ]
    path = Path(tmp) / 'round3.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = [(label, value, value) for label, _, value in cases]
    assert observed == expected, observed


def test_computed_and_accessor_boundaries_match_runtime(tmp):
    cases = [
        ('computed-key-expression-promotes', "let send = ordinary;\n"
         "function chooseKey() { send = extCmd; return 'run'; }\n"
         "const method = { [chooseKey()]() { send = ordinary; } };\n"
         "void method;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('computed-key-expression-demotes', "let send = extCmd;\n"
         "function chooseKey() { send = ordinary; return 'run'; }\n"
         "const method = { [chooseKey()]() { send = extCmd; } };\n"
         "void method;\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('getter-read-promotes', "let send = ordinary;\n"
         "const method = { get hook() { send = extCmd; return 1; } };\n"
         "void method.hook;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('setter-assignment-promotes', "let send = ordinary;\n"
         "const method = { set hook(value) { send = extCmd; } };\n"
         "method.hook = 1;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
    ]
    path = Path(tmp) / 'round4.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = [(label, value, value) for label, _, value in cases]
    assert observed == expected, observed


def test_property_read_classifications_match_runtime(tmp):
    cases = [
        ('data-property-read-stays-silent', "let send = ordinary;\n"
         "const obj = { hook: 1 };\nconst v = obj.hook;\nvoid v;\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('single-quoted-string-read-silent', "let send = ordinary;\n"
         "const obj = { hook: 'x' };\nconst v = obj.hook;\nvoid v;\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('double-quoted-string-read-silent', "let send = ordinary;\n"
         'const obj = { hook: "x" };\nconst v = obj.hook;\nvoid v;\n'
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('template-read-silent', "let send = ordinary;\n"
         "const obj = { hook: `x` };\nconst v = obj.hook;\nvoid v;\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('regex-read-silent', "let send = ordinary;\n"
         "const obj = { hook: /x/ };\nconst v = obj.hook;\nvoid v;\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('function-property-read-still-routes', "let send = ordinary;\n"
         + _ROUTER + "const method = { run: route };\nroute = ordinary;\n"
         "const f = method.run;\nf();\n"
         "void send;\nsend = () => extCmd('focus-tab', { tab: chromeTab });\n"
         "void send;\n", True),
    ]
    path = Path(tmp) / 'reads.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = [(label, value, value) for label, _, value in cases]
    assert observed == expected, observed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsbodies_')


if __name__ == '__main__':
    raise SystemExit(main())
