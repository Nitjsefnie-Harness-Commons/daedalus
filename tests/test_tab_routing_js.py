#!/usr/bin/env python3
"""Sender-alias boundaries for the JavaScript tab-routing scanner."""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402


_RUNTIME_PREFIX = """const calls = [];
const extCmd = (...args) => calls.push(args);
const ordinary = () => undefined;
const chromeTab = 41;
"""


def _runtime_and_guard(source, path):
    script = (_RUNTIME_PREFIX + source
              + "\nconst routed = calls.some(args => args.some(\n"
              + "  value => value && value.tab === chromeTab));\n"
              + "process.stdout.write(routed ? '1' : '0');\n")
    path.write_text(script, encoding='utf-8')
    node = shutil.which('node')
    assert node, 'node is required to execute JavaScript routing controls'
    ran = subprocess.run([node, str(path)], capture_output=True, text=True,
                         timeout=30)
    assert ran.returncode == 0, (ran.returncode, ran.stdout, ran.stderr)
    guard = bool(js_tab_routing_violations(path, path.name))
    return ran.stdout == '1', guard


def test_sender_aliases_follow_source_order(tmp):
    cases = [
        ('direct', "async function go(chromeTab) {\n"
         "  return await extCmd('focus-tab', { tab: chromeTab });\n}\n", 1),
        ('alias', "async function go(chromeTab) {\n"
         "  const send = extCmd;\n"
         "  return await send('focus-tab', { tab: chromeTab });\n}\n", 1),
        ('transitive', "const s1 = extCmd;\nconst s2 = s1;\n"
         "s2('focus-tab', { tab: chromeTab });\n", 1),
        ('rebound', "let send = extCmd;\nsend = ordinary;\n"
         "send('focus-tab', { tab: chromeTab });\n", 0),
        ('unknown', "const send = maybeSend;\n"
         "send('focus-tab', { tab: chromeTab });\n", 0),
        ('nested-block', "const send = extCmd;\nif (chromeTab) {\n"
         "  function nested() {\n"
         "    return send('focus-tab', { tab: chromeTab });\n  }\n"
         "  nested();\n}\n", 1),
        ('unprovable', "const send = choose ? extCmd : ordinary;\n"
         "send('focus-tab', { tab: chromeTab });\n", 1),
        ('run-command-alias', "const send = runCommand;\n"
         "send({ type: 'focus-tab', tab: chromeTab });\n", 1),
        ('run-command-unprovable',
         "const send = choose ? runCommand : ordinary;\n"
         "send({ type: 'focus-tab', tab: chromeTab });\n", 1),
    ]
    source = Path(tmp) / 'sender.js'
    observed = []
    for label, text, _ in cases:
        source.write_text(text, encoding='utf-8')
        observed.append((label, len(js_tab_routing_violations(
            source, source.name))))
    expected = [(label, count) for label, _, count in cases]
    assert observed == expected, observed


def test_sender_alias_scopes_match_runtime(tmp):
    cases = [
        ('parameter-shadow', "const send = extCmd;\n"
         "function inner(send) {\n"
         "  send('focus-tab', { tab: chromeTab });\n}\n"
         "inner(ordinary);\n", False),
        ('arrow-shadow', "const send = extCmd;\n"
         "const inner = send => {\n"
         "  send('focus-tab', { tab: chromeTab });\n};\n"
         "inner(ordinary);\n", False),
        ('inner-local', "const send = extCmd;\n"
         "function inner() {\n  const send = ordinary;\n}\n"
         "inner();\nsend('focus-tab', { tab: chromeTab });\n", True),
        ('invoked-promotion', "let send = ordinary;\n"
         "function promote() { send = extCmd; }\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('invoked-demotion', "let send = extCmd;\n"
         "function demote() { send = ordinary; }\ndemote();\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('uninvoked-promotion', "let send = ordinary;\n"
         "function promote() { send = extCmd; }\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('uninvoked-demotion', "let send = extCmd;\n"
         "function demote() { send = ordinary; }\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('same-name-callable-shadow', "let promote = ordinary;\n"
         "let send = ordinary;\n{ const promote = () => {\n"
         "  send = extCmd;\n}; }\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('same-binding-function-reassignment', "let send = ordinary;\n"
         "var promote = () => { send = extCmd; };\npromote();\n"
         "var promote = () => { send = ordinary; };\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('same-name-helper-shadow',
         "const fields = () => ({ tab: chromeTab });\n"
         "{ function fields() { return {}; } }\n"
         "extCmd('focus-tab', fields());\n", True),
        ('conditional', "let send = extCmd;\nconst flag = false;\n"
         "if (flag) send = ordinary;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('loop', "let send = extCmd;\nconst items = [];\n"
         "for (const item of items) send = ordinary;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('member-call', "const send = extCmd;\n"
         "const box = { send: ordinary };\n"
         "box.send('focus-tab', { tab: chromeTab });\n", False),
        ('method-declaration', "const send = extCmd;\n"
         "const box = { send(type, {tab}) {} };\nvoid box;\n", False),
        ('sibling-shadow', "function first() { const send = extCmd; }\n"
         "function second(send) {\n"
         "  send('focus-tab', { tab: chromeTab });\n}\n"
         "first();\nsecond(ordinary);\n", False),
        ('inherited-alias', "const send = extCmd;\nfunction inner() {\n"
         "  send('focus-tab', { tab: chromeTab });\n}\ninner();\n", True),
        ('block-shadow', "const send = extCmd;\n{\n"
         "  const send = ordinary;\n"
         "  send('focus-tab', { tab: chromeTab });\n}\n", False),
    ]
    path = Path(tmp) / 'scope.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = [(label, value, value) for label, _, value in cases]
    assert observed == expected, observed


def test_function_value_timelines_match_runtime(tmp):
    cases = [
        ('hoisted-declaration', "let send = ordinary;\npromote();\n"
         "function promote() { send = extCmd; }\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('later-hoisted-declaration-wins', "let send = ordinary;\n"
         "promote();\nfunction promote() { send = ordinary; }\n"
         "function promote() { send = extCmd; }\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('declaration-before-var-initializer', "let send = ordinary;\n"
         "promote();\nvar promote = () => { send = ordinary; };\n"
         "function promote() { send = extCmd; }\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('var-initializer-overrides-declaration', "let send = ordinary;\n"
         "function promote() { send = ordinary; }\n"
         "var promote = () => { send = extCmd; };\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('function-reassignment-clears', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\n"
         "promote = ordinary;\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('optional-function-demotion', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\nconst flag = false;\n"
         "if (flag) promote = ordinary;\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('let-uninitialized-block-shadow', "let send = ordinary;\n"
         "let promote = () => { send = ordinary; };\n{\n"
         "  let promote;\n  promote = () => { send = extCmd; };\n}\n"
         "promote();\nsend('focus-tab', { tab: chromeTab });\n", False),
        ('comma-let-block-shadow', "let send = ordinary;\n"
         "let promote = () => { send = ordinary; };\n{\n"
         "  let unused = ordinary, promote = () => { send = extCmd; };\n"
         "}\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('bare-comma-let-block-shadow', "let send = ordinary;\n"
         "let promote = () => { send = ordinary; };\n{\n"
         "  let unused, promote;\n"
         "  promote = () => { send = extCmd; };\n}\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('default-parameter-callable', "let send = ordinary;\n"
         "function invoke(promote = () => { send = extCmd; }) {\n"
         "  promote();\n}\ninvoke();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('overridden-default-parameter', "let send = ordinary;\n"
         "function invoke(promote = () => { send = extCmd; }) {\n"
         "  promote();\n}\ninvoke(ordinary);\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('undefined-default-parameter', "let send = ordinary;\n"
         "function invoke(promote = () => { send = extCmd; }) {\n"
         "  promote();\n}\ninvoke(undefined);\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('inline-argument-callable', "let send = ordinary;\n"
         "function invoke(promote = ordinary) { promote(); }\n"
         "invoke(() => { send = extCmd; });\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('comma-var-callable', "let send = ordinary;\n"
         "var unused = ordinary, promote = () => { send = extCmd; };\n"
         "promote();\nsend('focus-tab', { tab: chromeTab });\n", True),
        ('comma-var-function-values', "let send = ordinary;\n"
         "const promoteSender = () => { send = extCmd; };\n"
         "const promoteOrdinary = () => { send = ordinary; };\n"
         "var promote = promoteSender, spare = promoteOrdinary;\n"
         "promote();\nvoid spare;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('function-alias-captures-value', "let send = ordinary;\n"
         "const promoteSender = () => { send = extCmd; };\n"
         "const promoteOrdinary = () => { send = ordinary; };\n"
         "let active = promoteSender;\nlet promote = active;\n"
         "active = promoteOrdinary;\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('function-self-assignment', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\n"
         "promote = promote;\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('hoisted-function-reference', "let send = ordinary;\n"
         "let promote = declared;\npromote();\n"
         "function declared() { send = extCmd; }\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('captured-call-after-initializer', "let send = ordinary;\n"
         "function invoke() { promote(); }\n"
         "let promote = () => { send = extCmd; };\ninvoke();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('captured-call-after-reassignment', "let send = ordinary;\n"
         "let promote = () => { send = ordinary; };\n"
         "function invoke() { promote(); }\n"
         "promote = () => { send = extCmd; };\ninvoke();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('captured-call-before-reassignment', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\n"
         "function invoke() { promote(); }\n"
         "promote = () => { send = ordinary; };\ninvoke();\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('concise-body-alias-call', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\n"
         "let defer = () => send('focus-tab', { tab: chromeTab });\n"
         "promote();\ndefer();\n", True),
        ('uninvoked-concise-body-alias-call', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\n"
         "let defer = () => send('focus-tab', { tab: chromeTab });\n"
         "promote();\nvoid defer;\n", False),
        ('concise-deferred-chain', "let send = ordinary;\n"
         "let promote = ordinary;\n"
         "const deferred = () => promote('focus-tab', { tab: chromeTab });\n"
         "promote = extCmd;\ndeferred();\n", True),
        ('uninvoked-callable-demotion', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\n"
         "function demote() { promote = ordinary; }\n"
         "promote();\nsend('focus-tab', { tab: chromeTab });\n", True),
        ('callable-demotion-after-call', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\n"
         "function invoke() { promote(); promote = ordinary; }\n"
         "invoke();\nsend('focus-tab', { tab: chromeTab });\n", True),
        ('callable-promotion-after-call', "let send = ordinary;\n"
         "let promote = ordinary;\n"
         "function invoke() {\n"
         "  promote();\n  promote = () => { send = extCmd; };\n}\n"
         "invoke();\nsend('focus-tab', { tab: chromeTab });\n", False),
        ('invoked-callable-promotion', "let send = ordinary;\n"
         "const sender = () => { send = extCmd; };\nlet promote = ordinary;\n"
         "function configure() { promote = sender; }\n"
         "function invoke() { promote(); }\nconfigure();\ninvoke();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('invoked-callable-demotion', "let send = ordinary;\n"
         "const sender = () => { send = extCmd; };\nlet promote = sender;\n"
         "function configure() { promote = ordinary; }\n"
         "function invoke() { promote(); }\nconfigure();\ninvoke();\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('uninvoked-callable-promotion', "let send = ordinary;\n"
         "const sender = () => { send = extCmd; };\nlet promote = ordinary;\n"
         "function configure() { promote = sender; }\n"
         "function invoke() { promote(); }\ninvoke();\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('comma-var-invocation', "let send = ordinary;\n"
         "var promote = () => { send = extCmd; }, invoked = promote();\n"
         "void invoked;\nvar promote = () => { send = ordinary; };\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('helper-reassigned-after-call',
         "var fields = () => ({ tab: chromeTab });\n"
         "extCmd('focus-tab', fields());\nvar fields = () => ({});\n",
         True),
        ('helper-reassigned-before-call',
         "var fields = () => ({ tab: chromeTab });\n"
         "fields = () => ({});\nextCmd('focus-tab', fields());\n",
         False),
        ('helper-function-reference',
         "const routedFields = () => ({ tab: chromeTab });\n"
         "const safeFields = () => ({});\n"
         "var fields = routedFields, spare = safeFields;\n"
         "extCmd('focus-tab', fields());\nvoid spare;\n", True),
        ('optional-helper-reassignment',
         "let fields = () => ({ tab: chromeTab });\n"
         "const flag = false;\nif (flag) fields = () => ({});\n"
         "extCmd('focus-tab', fields());\n", True),
    ]
    path = Path(tmp) / 'timeline.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = [(label, value, value) for label, _, value in cases]
    assert observed == expected, observed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
