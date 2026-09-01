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
        ('transitive-concise-invocation', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\n"
         "let inner = () => send('focus-tab', { tab: chromeTab });\n"
         "let outer = () => inner();\npromote();\nouter();\n", True),
        ('block-wrapped-concise-invocation', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\n"
         "let inner = () => send('focus-tab', { tab: chromeTab });\n"
         "function outer() { inner(); }\npromote();\nouter();\n", True),
        ('concise-parameter-promotion', "let send = ordinary;\n"
         "const deferred = send => send('focus-tab', "
         "{ tab: chromeTab });\ndeferred(extCmd);\n", True),
        ('concise-parameter-demotion', "let send = extCmd;\n"
         "const deferred = send => send('focus-tab', "
         "{ tab: chromeTab });\ndeferred(ordinary);\n", False),
        ('parameter-only-promotion',
         "const invoke = send => send('focus-tab', "
         "{ tab: chromeTab });\ninvoke(extCmd);\n", True),
        ('parameter-only-demotion',
         "const invoke = send => send('focus-tab', "
         "{ tab: chromeTab });\ninvoke(ordinary);\n", False),
        ('default-parameter-promotion', "let send = ordinary;\n"
         "const deferred = (send = ordinary) => send('focus-tab', "
         "{ tab: chromeTab });\ndeferred(extCmd);\n", True),
        ('default-parameter-demotion', "let send = extCmd;\n"
         "const deferred = (send = extCmd) => send('focus-tab', "
         "{ tab: chromeTab });\ndeferred(ordinary);\n", False),
        ('destructured-parameter-promotion', "let send = ordinary;\n"
         "const deferred = ({ send = ordinary } = {}) => "
         "send('focus-tab', { tab: chromeTab });\n"
         "deferred( { send: extCmd });\n", True),
        ('destructured-parameter-demotion', "let send = extCmd;\n"
         "const deferred = ({ send = extCmd } = {}) => "
         "send('focus-tab', { tab: chromeTab });\n"
         "deferred( { send: ordinary });\n", False),
        ('instanceof-newline-continuation', "let send = ordinary;\n"
         "let defer = () => ordinary\n"
         "  instanceof (send('focus-tab', { tab: chromeTab }), Function);\n"
         "send = extCmd;\ndefer();\n", True),
        ('in-newline-continuation', "let send = ordinary;\n"
         "let defer = () => ordinary\n"
         "  in { routed: send('focus-tab', { tab: chromeTab }) };\n"
         "send = extCmd;\ndefer();\n", True),
        ('property-invocation-promotion', "let send = ordinary;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst holder = { deferred };\n"
         "send = extCmd;\nholder.deferred();\n", True),
        ('property-invocation-demotion', "let send = extCmd;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst holder = { deferred };\n"
         "send = ordinary;\nholder.deferred();\n", False),
        ('nested-iife-promotion', "let send = ordinary;\n"
         "const deferred = () =>\n"
         "  (function promote() { send = extCmd; })();\n"
         "deferred();\nsend('focus-tab', { tab: chromeTab });\n", True),
        ('nested-iife-demotion', "let send = extCmd;\n"
         "const deferred = () =>\n"
         "  (function demote() { send = ordinary; })();\n"
         "deferred();\nsend('focus-tab', { tab: chromeTab });\n", False),
        ('iife-argument-promotes-body-demotes', "let send = ordinary;\n"
         "const promote = () => { send = extCmd; };\n"
         "(function demote(value) {\n"
         "  send = ordinary;\n})(promote());\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('iife-argument-demotes-body-promotes', "let send = extCmd;\n"
         "const demote = () => { send = ordinary; };\n"
         "(function promote(value) {\n"
         "  send = extCmd;\n})(demote());\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('optional-call-promotion', "let send = ordinary;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nsend = extCmd;\ndeferred?.();\n", True),
        ('optional-call-demotion', "let send = extCmd;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nsend = ordinary;\ndeferred?.();\n", False),
        ('computed-member-promotion', "let send = ordinary;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst box = { run: deferred };\n"
         "send = extCmd;\nbox['run']();\n", True),
        ('computed-member-demotion', "let send = extCmd;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst box = { run: deferred };\n"
         "send = ordinary;\nbox['run']();\n", False),
        ('inline-member-promotion', "let send = ordinary;\n"
         "const box = { run: () => send('focus-tab', "
         "{ tab: chromeTab }) };\nsend = extCmd;\nbox.run();\n", True),
        ('inline-member-demotion', "let send = extCmd;\n"
         "const box = { run: () => send('focus-tab', "
         "{ tab: chromeTab }) };\nsend = ordinary;\nbox.run();\n", False),
        ('anonymous-iife-promotion', "let send = ordinary;\n"
         "const deferred = () => (function () { send = extCmd; })();\n"
         "deferred();\nsend('focus-tab', { tab: chromeTab });\n", True),
        ('anonymous-iife-demotion', "let send = extCmd;\n"
         "const deferred = () => (function () { send = ordinary; })();\n"
         "deferred();\nsend('focus-tab', { tab: chromeTab });\n", False),
        ('arrow-iife-promotion', "let send = ordinary;\n"
         "const deferred = () => (() => { send = extCmd; })();\n"
         "deferred();\nsend('focus-tab', { tab: chromeTab });\n", True),
        ('arrow-iife-demotion', "let send = extCmd;\n"
         "const deferred = () => (() => { send = ordinary; })();\n"
         "deferred();\nsend('focus-tab', { tab: chromeTab });\n", False),
        ('renamed-destructuring-demotion', "let send = extCmd;\n"
         "const deferred = ({ send: invoke }) => invoke('focus-tab', "
         "{ tab: chromeTab });\ndeferred({ send: ordinary });\n", False),
        ('omitted-default-promotion',
         "const deferred = (send = extCmd) => send('focus-tab', "
         "{ tab: chromeTab });\ndeferred();\n", True),
        ('omitted-default-clean',
         "const deferred = (send = ordinary) => send('focus-tab', "
         "{ tab: chromeTab });\ndeferred();\n", False),
        ('missing-property-default-promotion',
         "const deferred = ({ send = extCmd } = {}) => "
         "send('focus-tab', { tab: chromeTab });\ndeferred({});\n", True),
        ('missing-property-default-clean',
         "const deferred = ({ send = ordinary } = {}) => "
         "send('focus-tab', { tab: chromeTab });\ndeferred({});\n", False),
        ('function-call-member-promotion', "let send = ordinary;\n"
         "const promote = () => { send = extCmd; };\n"
         "promote.call(null);\nsend('focus-tab', "
         "{ tab: chromeTab });\n", True),
        ('function-call-member-demotion', "let send = extCmd;\n"
         "const demote = () => { send = ordinary; };\n"
         "demote.call(null);\nsend('focus-tab', "
         "{ tab: chromeTab });\n", False),
        ('object-alias-member-promotion', "let send = ordinary;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst box = { run: deferred };\n"
         "const alias = box;\nsend = extCmd;\nalias.run();\n", True),
        ('object-alias-member-demotion', "let send = extCmd;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst box = { run: deferred };\n"
         "const alias = box;\nsend = ordinary;\nalias.run();\n", False),
        ('optional-computed-member-promotion', "let send = ordinary;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst box = { run: deferred };\n"
         "const key = 'run';\nsend = extCmd;\nbox?.[key]();\n", True),
        ('optional-computed-member-demotion', "let send = extCmd;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst box = { run: deferred };\n"
         "const key = 'run';\nsend = ordinary;\nbox?.[key]();\n", False),
        ('returned-object-member-promotion', "let send = ordinary;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst box = { run: deferred };\n"
         "const make = () => box;\nsend = extCmd;\nmake().run();\n", True),
        ('returned-object-member-demotion', "let send = extCmd;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst box = { run: deferred };\n"
         "const make = () => box;\nsend = ordinary;\nmake().run();\n",
         False),
        ('assigned-object-member-promotion', "let send = ordinary;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst holder = {};\n"
         "holder.deferred = deferred;\nsend = extCmd;\n"
         "holder.deferred();\n", True),
        ('assigned-object-member-demotion', "let send = extCmd;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nconst holder = {};\n"
         "holder.deferred = deferred;\nsend = ordinary;\n"
         "holder.deferred();\n", False),
        ('computed-destructuring-promotion', "let invoke = ordinary;\n"
         "const key = 'send';\n"
         "const deferred = ({ [key]: invoke }) => "
         "invoke('focus-tab', { tab: chromeTab });\n"
         "deferred({ send: extCmd });\n", True),
        ('computed-destructuring-demotion', "let invoke = extCmd;\n"
         "const key = 'send';\n"
         "const deferred = ({ [key]: invoke }) => "
         "invoke('focus-tab', { tab: chromeTab });\n"
         "deferred({ send: ordinary });\n", False),
        ('nested-argument-promotion',
         "let send=ordinary; const promote=()=>{send=extCmd}; "
         "const invoke=(value)=>send('focus-tab',{tab:chromeTab}); "
         "invoke(promote());\n", True),
        ('nested-argument-demotion',
         "let send=extCmd; const demote=()=>{send=ordinary}; "
         "const invoke=(value)=>send('focus-tab',{tab:chromeTab}); "
         "invoke(demote());\n", False),
        ('two-argument-demote-then-promote',
         "let send=ordinary; const promote=()=>{send=extCmd}; "
         "const demote=()=>{send=ordinary}; "
         "const invoke=(value)=>send('focus-tab',{tab:chromeTab}); "
         "invoke(demote(),promote());\n", True),
        ('two-argument-promote-then-demote',
         "let send=extCmd; const promote=()=>{send=extCmd}; "
         "const demote=()=>{send=ordinary}; "
         "const invoke=(value)=>send('focus-tab',{tab:chromeTab}); "
         "invoke(promote(),demote());\n", False),
        ('object-rest-promotion', "const bag = { send: ordinary };\n"
         "const invoke = ({ safe, ...bag }) => "
         "bag.send('focus-tab', { tab: chromeTab });\n"
         "invoke({ safe: true, send: extCmd });\n", True),
        ('object-rest-demotion', "const bag = { send: extCmd };\n"
         "const invoke = ({ safe, ...bag }) => "
         "bag.send('focus-tab', { tab: chromeTab });\n"
         "invoke({ safe: true, send: ordinary });\n", False),
        ('array-rest-promotion',
         "const invoke = (...[call]) => call('focus-tab', "
         "{ tab: chromeTab });\ninvoke(extCmd);\n", True),
        ('array-rest-demotion',
         "const invoke = (...[call]) => call('focus-tab', "
         "{ tab: chromeTab });\ninvoke(ordinary);\n", False),
        ('parenthesized-callee-promotion', "const send = extCmd;\n"
         "(send)('focus-tab', { tab: chromeTab });\n", True),
        ('parenthesized-callee-demotion', "const send = ordinary;\n"
         "(send)('focus-tab', { tab: chromeTab });\n", False),
        ('semicolon-terminated-concise-body', "let send = ordinary;\n"
         "const deferred = () => send('focus-tab', "
         "{ tab: chromeTab });\nsend = extCmd;\ndeferred();\n", True),
        ('comma-sibling-concise-body', "let send = ordinary;\n"
         "let promote = () => { send = extCmd; };\n"
         "let spare = () => 0, defer = () => send('focus-tab', "
         "{ tab: chromeTab });\npromote();\ndefer();\nvoid spare;\n", True),
        ('asi-terminated-concise-body', "let send = ordinary;\n"
         "let defer = () => send('focus-tab', { tab: chromeTab })\n"
         "send = extCmd;\ndefer();\n", True),
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


def test_object_copy_and_receiver_forms_match_runtime(tmp):
    cases = [
        ('declaration-rest-closure-promotion', "let send = ordinary;\n"
         "const rest = { send: () => send('focus-tab', "
         "{ tab: chromeTab }) };\nconst { ...bag } = rest;\n"
         "send = extCmd;\nbag.send();\n", True),
        ('declaration-rest-closure-demotion', "let send = extCmd;\n"
         "const rest = { send: () => send('focus-tab', "
         "{ tab: chromeTab }) };\nconst { ...bag } = rest;\n"
         "send = ordinary;\nbag.send();\n", False),
        ('declaration-rest-direct-promotion',
         "const rest = { send: extCmd };\nconst { ...bag } = rest;\n"
         "bag.send('focus-tab', { tab: chromeTab });\n", True),
        ('declaration-rest-direct-demotion',
         "const rest = { send: ordinary };\nconst { ...bag } = rest;\n"
         "bag.send('focus-tab', { tab: chromeTab });\n", False),
        ('nested-declaration-rest-promotion', "let send = ordinary;\n"
         "const source = { inner: { send: () => send('focus-tab', "
         "{ tab: chromeTab }) } };\n"
         "const { inner: { ...bag } } = source;\n"
         "send = extCmd;\nbag.send();\n", True),
        ('nested-declaration-rest-demotion', "let send = extCmd;\n"
         "const source = { inner: { send: () => send('focus-tab', "
         "{ tab: chromeTab }) } };\n"
         "const { inner: { ...bag } } = source;\n"
         "send = ordinary;\nbag.send();\n", False),
        ('object-spread-promotion', "const source = { send: extCmd };\n"
         "const bag = { ...source };\n"
         "bag.send('focus-tab', { tab: chromeTab });\n", True),
        ('object-spread-demotion', "const source = { send: ordinary };\n"
         "const bag = { ...source };\n"
         "bag.send('focus-tab', { tab: chromeTab });\n", False),
        ('member-extraction-promotion',
         "const box = { run: extCmd };\nconst invoke = box.run;\n"
         "invoke('focus-tab', { tab: chromeTab });\n", True),
        ('member-extraction-demotion',
         "const box = { run: ordinary };\nconst invoke = box.run;\n"
         "invoke('focus-tab', { tab: chromeTab });\n", False),
        ('returned-rest-chain-promotion',
         "const getBag = ({ ...bag }) => bag;\n"
         "getBag({ send: extCmd }).send('focus-tab', "
         "{ tab: chromeTab });\n", True),
        ('returned-rest-chain-demotion',
         "const getBag = ({ ...bag }) => bag;\n"
         "getBag({ send: ordinary }).send('focus-tab', "
         "{ tab: chromeTab });\n", False),
        ('returned-rest-binding-promotion',
         "const getBag = ({ ...bag }) => bag;\n"
         "const bag = getBag({ send: extCmd });\n"
         "bag.send('focus-tab', { tab: chromeTab });\n", True),
        ('returned-rest-binding-demotion',
         "const getBag = ({ ...bag }) => bag;\n"
         "const bag = getBag({ send: ordinary });\n"
         "bag.send('focus-tab', { tab: chromeTab });\n", False),
        ('block-returned-rest-promotion',
         "function getBag(source) {\n  const { ...bag } = source;\n"
         "  return bag;\n}\ngetBag({ send: extCmd }).send('focus-tab', "
         "{ tab: chromeTab });\n", True),
        ('block-returned-rest-demotion',
         "function getBag(source) {\n  const { ...bag } = source;\n"
         "  return bag;\n}\ngetBag({ send: ordinary }).send('focus-tab', "
         "{ tab: chromeTab });\n", False),
        ('rest-member-optional-promotion',
         "const invoke = ({ ...bag }) => bag.send?.('focus-tab', "
         "{ tab: chromeTab });\ninvoke({ send: extCmd });\n", True),
        ('rest-member-optional-demotion',
         "const invoke = ({ ...bag }) => bag.send?.('focus-tab', "
         "{ tab: chromeTab });\ninvoke({ send: ordinary });\n", False),
        ('rest-member-call-promotion',
         "const invoke = ({ ...bag }) => bag.send.call(null, 'focus-tab', "
         "{ tab: chromeTab });\ninvoke({ send: extCmd });\n", True),
        ('rest-member-call-demotion',
         "const invoke = ({ ...bag }) => bag.send.call(null, 'focus-tab', "
         "{ tab: chromeTab });\ninvoke({ send: ordinary });\n", False),
        ('direct-apply-promotion', "const send = extCmd;\n"
         "send.apply(null, ['focus-tab', { tab: chromeTab }]);\n", True),
        ('direct-apply-demotion', "const send = ordinary;\n"
         "send.apply(null, ['focus-tab', { tab: chromeTab }]);\n", False),
        ('nested-group-promotion', "const send = extCmd;\n"
         "((send))('focus-tab', { tab: chromeTab });\n", True),
        ('nested-group-demotion', "const send = ordinary;\n"
         "((send))('focus-tab', { tab: chromeTab });\n", False),
        ('reflect-apply-promotion', "const send = extCmd;\n"
         "Reflect.apply(send, null, ['focus-tab', "
         "{ tab: chromeTab }]);\n", True),
        ('reflect-apply-demotion', "const send = ordinary;\n"
         "Reflect.apply(send, null, ['focus-tab', "
         "{ tab: chromeTab }]);\n", False),
        ('bound-sender-promotion', "const send = extCmd;\n"
         "const bound = send.bind(null);\n"
         "bound('focus-tab', { tab: chromeTab });\n", True),
        ('bound-sender-demotion', "const send = ordinary;\n"
         "const bound = send.bind(null);\n"
         "bound('focus-tab', { tab: chromeTab });\n", False),
        ('global-member-promotion', "let send = ordinary;\n"
         "globalThis.box = { run: () => send('focus-tab', "
         "{ tab: chromeTab }) };\nsend = extCmd;\nbox.run();\n", True),
        ('global-member-demotion', "let send = extCmd;\n"
         "globalThis.box = { run: () => send('focus-tab', "
         "{ tab: chromeTab }) };\nsend = ordinary;\nbox.run();\n", False),
        ('global-computed-member-promotion', "let send = ordinary;\n"
         "globalThis.box = { run: () => send('focus-tab', "
         "{ tab: chromeTab }) };\nsend = extCmd;\nbox['run']();\n", True),
        ('global-computed-member-demotion', "let send = extCmd;\n"
         "globalThis.box = { run: () => send('focus-tab', "
         "{ tab: chromeTab }) };\nsend = ordinary;\nbox['run']();\n", False),
    ]
    path = Path(tmp) / 'receiver.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = [(label, value, value) for label, _, value in cases]
    assert observed == expected, observed


def test_unmodelled_invocation_fails_closed(tmp):
    # Dynamic targets deliberately use the issue's fail-closed allowance:
    # runtime-clean still produces one unprovable guard finding.
    source = "let send = ordinary;\nconst deferred = () => " \
             "send('focus-tab', { tab: chromeTab });\n" \
             "const key = 'run';\nconst box = { run: deferred };\n" \
             "box[key]();\n"
    path = Path(tmp) / 'dynamic-call.js'
    runtime, guard = _runtime_and_guard(source, path)
    violations = js_tab_routing_violations(path, path.name)
    assert (runtime, guard) == (False, True), (runtime, violations)
    assert len(violations) == 1, violations
    assert any('cannot resolve' in item for item in violations), violations


def test_parenthesized_callee_uses_sender_diagnostic(tmp):
    path = Path(tmp) / 'parenthesized-call.js'
    path.write_text(
        "const send = extCmd;\n"
        "(send)('focus-tab', { tab: chromeTab });\n",
        encoding='utf-8')
    violations = js_tab_routing_violations(path, path.name)
    assert len(violations) == 1, violations
    assert '`tab` in a typed command send' in violations[0], violations
    assert 'cannot resolve' not in violations[0], violations


def test_resolved_callee_forms_use_sender_diagnostic(tmp):
    cases = [
        "const send = extCmd;\n"
        "send.apply(null, ['focus-tab', { tab: chromeTab }]);\n",
        "const send = extCmd;\n"
        "((send))('focus-tab', { tab: chromeTab });\n",
        "const send = extCmd;\n"
        "Reflect.apply(send, null, ['focus-tab', "
        "{ tab: chromeTab }]);\n",
        "const send = extCmd;\nconst bound = send.bind(null);\n"
        "bound('focus-tab', { tab: chromeTab });\n",
    ]
    path = Path(tmp) / 'resolved-call.js'
    for source in cases:
        path.write_text(source, encoding='utf-8')
        violations = js_tab_routing_violations(path, path.name)
        assert len(violations) == 1, (source, violations)
        assert '`tab` in a typed command send' in violations[0], violations
        assert 'cannot resolve' not in violations[0], violations


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
