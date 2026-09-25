#!/usr/bin/env python3
"""The worker's served command types, checked against every shipped client.

Issue 204: nothing enumerated the command types the worker serves, so a
`case` no client sends and no route-table row names passed every suite. The
switch is read lexically, the clients from the AST, and the two compared
both ways; nothing is derived twice, so neither read can vouch for the
other, and a runtime dispatch pins the one served type no client names.

The readers live in ``_command_type_readers``; this suite observes them. A
marker nothing observes is a comment, so every marker they carry has a
synthetic source here that manufactures the input it exists to catch, and
removing any one of them from the reader turns this suite red.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary import (run_extension_capability_routes,  # noqa: E402
                       run_extension_command_result)
from _command_type_readers import (  # noqa: E402
    clients, dashboard_sent_types, python_sent_types, served_types)
from _worker_routes import ROUTES  # noqa: E402

_CODE_COMMAND = {'id': 'code-path-control', 'code': 'return 1'}


def test_the_dispatch_switch_is_read_and_its_labels_enumerated(tmp):
    """The served set is read whole, and every label is a usable type."""
    del tmp
    labels = served_types()
    assert labels, 'the dispatch switch has no case label'
    assert len(labels) == len(set(labels))
    assert not [label for label in labels
                if not label or label.strip() != label or ' ' in label], \
        f'unusable command type label: {labels}'


def _refusal_from(read, accepted):
    """`read`'s refusal message, or a failure naming what was accepted."""
    try:
        read()
    except AssertionError as error:
        return str(error)
    raise AssertionError(accepted)


def _dispatch_source(*arms, comment=''):
    return (
        'function dispatchCommand(cmd) {\n'
        '  switch (cmd.type) {\n'
        + comment
        + ''.join(f'    {arm}\n' for arm in arms)
        + '    default: return null;\n'
        '  }\n'
        '}\n')


def test_a_case_label_that_is_not_a_plain_literal_is_refused(tmp):
    """A computed label is named with its line, not skipped."""
    del tmp
    source = _dispatch_source(
        "case 'cookies': return handleCookies(cmd);",
        'case COMMANDS.ping: return handlePing(cmd);')
    message = _refusal_from(
        lambda: served_types(source, 'background.js'),
        'a computed case label was accepted')
    assert 'background.js:4' in message, message
    assert 'COMMANDS.ping' in message, message
    assert 'not a plain string literal' in message, message


def test_a_case_hidden_in_a_comment_makes_the_arm_counts_disagree(tmp):
    """The raw/masked arm marker fires, and names what it saw.

    Nothing in the tree exercises it, and deleting the `js_mask` call
    outright leaves every other test green: the two counts then agree.
    """
    del tmp
    source = _dispatch_source(
        "case 'cookies': return handleCookies(cmd);",
        comment='    // a case label in a comment is not an arm\n')
    try:
        served_types(source, 'background.js')
    except AssertionError as error:
        raised = str(error)
    else:
        raised = ''
    assert 'the dispatch switch body holds 2 case tokens' in raised, (
        'the arm marker did not report the comment-hidden case: '
        f'{raised!r}')
    assert 'the masked body 1' in raised, raised


def test_a_second_switch_in_the_background_is_named(tmp):
    """A switch outside `dispatchCommand` fails rather than going unread."""
    del tmp
    source = (
        'function otherSwitch(value) {\n'
        '  switch (value) {\n'
        "    case 'other': return 1;\n"
        '    default: return 0;\n'
        '  }\n'
        '}\n'
        'function dispatchCommand(cmd) {\n'
        '  switch (cmd.type) {\n'
        "    case 'cookies': return handleCookies(cmd);\n"
        '    default: return null;\n'
        '  }\n'
        '}\n')
    try:
        served_types(source, 'background.js')
    except AssertionError as error:
        message = str(error)
        assert 'background.js:2' in message, message
        assert 'background.js:8' in message, message
    else:
        assert False, 'a switch outside dispatchCommand was ignored'


def test_every_served_type_appears_in_the_one_route_table(tmp):
    """The switch and the shared route table agree in both directions."""
    del tmp
    served = set(served_types())
    table = {command_type for _, _, command_type in ROUTES}
    assert served == table, (
        f'served without a route-table row: {sorted(served - table)}; '
        f'route table without a served type: {sorted(table - served)}')


def test_every_served_type_is_sent_by_a_client_but_one(tmp):
    """One served type is absent from the clients, and it is named.

    A decidable claim, not a waiver: exactly one, and it must be `eval`, so
    a second is reported by name. The residual is then a two-way equality.
    """
    del tmp
    sent_sets = clients()[0]
    served = set(served_types())
    sent = set().union(*sent_sets.values())
    unnamed = served - sent
    assert len(unnamed) == 1, (
        'exactly one served type is expected to be reached by something '
        f'other than a client type literal; found {sorted(unnamed)}')
    exception = next(iter(unnamed))
    assert exception == 'eval', (
        f'the one served type no client names is {sorted(unnamed)}; the '
        'code path, not a type literal, is what reaches eval')
    residual = served - {exception}
    assert residual == sent, (
        f'served without a client: {sorted(residual - sent)}; '
        f'client without a served type: {sorted(sent - residual)}')


def _eval_path_observation():
    """The code-only command's route observation, or a named failure.

    A worker that stops deriving `eval` from `code` sends the command to the
    default arm, which posts a result the scenario does not declare, so the
    node child exits with no output and the harness raises before this suite
    sees an observation. Named as this control's failure, with the
    mechanism, without claiming the decode error proves it.
    """
    try:
        return run_extension_capability_routes([{
            'symbol': 'handleEval',
            'publishedSymbols': ['handleEval', 'handleCookies'],
            'command': _CODE_COMMAND,
        }])[0]
    except (AssertionError, ValueError) as error:
        raise AssertionError(
            'the code-only command produced no route observation, so this '
            'control could not confirm that the code path reaches the eval '
            'handler; a worker that stops deriving eval from code sends it '
            f'to the default arm instead. The harness reported: {error!r}'
        ) from error


def test_a_type_the_worker_does_not_serve_reaches_the_unknown_arm(tmp):
    """The served-marker limb: an unserved type falls to `default`.

    Split from the eval limb because different edits break these two, and
    when they shared one function the first assertion decided which ran.
    """
    del tmp
    marker = run_extension_command_result(
        {'id': 'marker-control', 'type': 'probe-capability'})
    assert marker['posted'] == [{
        'result': None,
        'error': 'Unknown command type: probe-capability',
    }], marker


def test_the_code_path_and_not_a_type_literal_reaches_eval(tmp):
    """Runtime evidence for the `eval` exception, and nothing else.

    Unconditional on purpose: breaking the `code` -> `eval` derivation is
    reported here even though every served-type test stays green.
    """
    del tmp
    observed = _eval_path_observation()
    assert observed['callCount'] == 1 and observed['answered'], observed
    assert 'calledType' not in observed, (
        f'the eval handler saw a type field on the code command: {observed}')


def test_the_dashboard_command_scan_accounts_for_every_call_site(tmp):
    """The dashboard's sent set is read from every `extCmd` reference."""
    del tmp
    literals = dashboard_sent_types()
    assert literals, 'no dashboard extCmd call site was enumerated'
    unserved = literals - set(served_types())
    assert not unserved, (
        f'the dashboard sends command types the worker does not serve: '
        f'{sorted(unserved)}')


def test_a_client_type_argument_that_is_not_a_literal_is_refused(tmp):
    """An indirect command type in a client is named with its file:line."""
    written = Path(tmp) / 'commands_probe.py'
    written.write_text(
        'from .invoke import ext_cmd\n'
        'TYPE = "cookies"\n'
        'def do_probe():\n'
        "    return ext_cmd('_probe', TYPE)\n",
        encoding='utf-8')
    message = _refusal_from(
        lambda: python_sent_types([written], 'ext_cmd', False),
        'an indirect client command type was accepted')
    assert 'commands_probe.py:4' in message, message
    assert 'not a plain string literal' in message, message


def _python_source(tmp, name, body):
    written = Path(tmp) / name
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(body, encoding='utf-8')
    return written


def test_a_python_client_aliasing_the_send_helper_is_refused(tmp):
    """Binding the helper to a name and calling that name is refused.

    The reader enumerates the identifier, not a call's spelling, so the
    aliased call is considered -- and refused, only a direct literal call is
    readable.
    """
    written = _python_source(tmp, 'commands_alias.py', (
        'from .invoke import ext_cmd\n'
        '\n'
        '_ALIAS = ext_cmd\n'
        '\n'
        'def do_probe():\n'
        "    return _ALIAS('_probe', 'probe-alias')\n"))
    message = _refusal_from(
        lambda: python_sent_types([written], 'ext_cmd', False),
        'a client that aliases the send helper was accepted')
    assert 'commands_alias.py:3' in message, message
    assert "shape 'assign'" in message, message


def test_a_python_client_bare_alias_with_no_call_is_refused(tmp):
    """A bare binding is the first step of the escape, so it is refused too."""
    written = _python_source(tmp, 'commands_bare.py', (
        'from .invoke import ext_cmd\n'
        '\n'
        'ALIAS = ext_cmd\n'))
    message = _refusal_from(
        lambda: python_sent_types([written], 'ext_cmd', False),
        'a bare binding of the send helper was accepted')
    assert 'commands_bare.py:3' in message, message
    assert "shape 'assign'" in message, message


def test_a_python_surface_with_no_helper_definition_is_refused(tmp):
    """A surface that defines no helper yields no references to read.

    Without this, a rename leaves the reader matching nothing and reporting
    a clean bill of health.
    """
    written = _python_source(tmp, 'commands_nodef.py', (
        'def do_probe():\n'
        "    return something.elsewhere('_probe', 'probe-nodef')\n"))
    message = _refusal_from(
        lambda: python_sent_types([written], 'ext_cmd', False),
        'a client surface with no send-helper definition passed')
    assert 'define ext_cmd exactly once' in message, message


def test_a_dashboard_call_not_spelling_extcmd_before_its_paren_is_refused(tmp):
    """`(extCmd)(...)` reaches the send path without the call spelling.

    Found because the reader looks for the identifier, not for `extCmd(`.
    """
    written = Path(tmp) / 'section.js'
    written.write_text(
        "import { extCmd } from '../api.js';\n"
        "export async function extCmd(type) { return type; }\n"
        "const alias = (extCmd)('probe-dash-alias');\n",
        encoding='utf-8')
    message = _refusal_from(
        lambda: dashboard_sent_types([written]),
        'a parenthesised dashboard send call was accepted')
    assert 'section.js:3' in message, message
    assert 'not a direct call' in message, message


def test_prose_naming_the_send_helper_is_not_a_reference(tmp):
    """The over-recognition direction: prose is not a call.

    A string that MENTIONS the helper, and a comment, are not references.
    A string or comment that QUOTES the name is refused by the string rule
    instead -- the comment below is deliberately unquoted, and quoting it
    would go red. Without the masking, this comment is classified.
    """
    python_written = _python_source(tmp, 'commands_prose.py', (
        '"""The ext_cmd helper sends one command type."""\n'
        '# ext_cmd is named in this comment too\n'
        'from .invoke import ext_cmd\n\n'
        'def ext_cmd(cmd_id, cmd_type, **fields):\n'
        '    return cmd_type\n\n'
        'def do_probe():\n'
        "    return ext_cmd('_probe', 'probe-prose')\n"))
    literals, _, calls = python_sent_types([python_written], 'ext_cmd', False)
    assert literals == {'probe-prose'} and calls == 1, (literals, calls)

    js_written = Path(tmp) / 'section.js'
    js_written.write_text(
        "import { extCmd } from '../api.js';\n"
        "// extCmd is named in this comment too\n"
        "const label = 'extCmd helper';\n"
        'export async function extCmd(type) { return type; }\n'
        "const answer = await extCmd('probe-js-prose');\n",
        encoding='utf-8')
    assert dashboard_sent_types([js_written]) == {'probe-js-prose'}


def test_a_python_string_naming_the_send_helper_is_refused(tmp):
    """`getattr(bridge, 'ext_cmd')` is a lookup no reference walk can see.

    A dynamic lookup can only exist if the name appears as a string
    literal. The shipped clients hold none, so this control is the only
    thing that observes the rule firing.
    """
    written = _python_source(tmp, 'commands_lookup.py', (
        'from .invoke import ext_cmd\n'
        '\n'
        'def ext_cmd(cmd_id, cmd_type, **fields): return cmd_type\n'
        '\n'
        'def do_probe(bridge):\n'
        "    return getattr(bridge, 'ext_cmd')('_probe', 'probe-lookup')\n"))
    message = _refusal_from(
        lambda: python_sent_types([written], 'ext_cmd', False),
        'a string naming the send helper was accepted')
    assert 'commands_lookup.py:6' in message, message
    assert 'string literal' in message, message


def test_a_dashboard_string_naming_the_send_helper_is_refused(tmp):
    """`globalThis['extCmd']` is a lookup the blanked scan cannot see."""
    written = Path(tmp) / 'section.js'
    written.write_text(
        "import { extCmd } from '../api.js';\n"
        'export async function extCmd(type) { return type; }\n'
        "const send = globalThis['extCmd'];\n"
        "const answer = await send('probe-js-lookup');\n",
        encoding='utf-8')
    message = _refusal_from(
        lambda: dashboard_sent_types([written]),
        'a dashboard string naming the send helper was accepted')
    assert 'section.js:3' in message, message
    assert 'string literal' in message, message


def test_a_python_client_aliasing_the_import_is_refused(tmp):
    """`from .invoke import ext_cmd as send` binds a second name.

    A call through `send` is a `Name` the callee matcher never sees, so the
    binding is the only place it can be refused.
    """
    written = _python_source(tmp, 'commands_import_alias.py', (
        'from .invoke import ext_cmd as send\n'
        '\n'
        'def ext_cmd(cmd_id, cmd_type, **fields): return cmd_type\n'
        '\n'
        'def do_probe():\n'
        "    return send('_probe', 'probe-import-alias')\n"))
    message = _refusal_from(
        lambda: python_sent_types([written], 'ext_cmd', False),
        'an aliased import of the send helper was accepted')
    assert 'commands_import_alias.py:1' in message, message
    assert 'import alias' in message, message


def test_a_dashboard_aliasing_the_import_is_refused(tmp):
    """`import { extCmd as send }` is the same fifth way, on the JS side."""
    written = Path(tmp) / 'section.js'
    written.write_text(
        "import { extCmd as send } from '../api.js';\n"
        'export async function extCmd(type) { return type; }\n'
        "const answer = await send('probe-js-alias');\n",
        encoding='utf-8')
    message = _refusal_from(
        lambda: dashboard_sent_types([written]),
        'an aliased dashboard import was accepted')
    assert 'section.js:1' in message, message
    assert 'import alias' in message, message


def test_a_python_surface_defining_the_helper_and_nothing_else_is_refused(
        tmp):
    """The reference census: a definition with no reference to it."""
    written = _python_source(tmp, 'commands_unused.py', (
        'def ext_cmd(cmd_id, cmd_type, **fields): return cmd_type\n'))
    message = _refusal_from(
        lambda: python_sent_types([written], 'ext_cmd', False),
        'a surface defining the helper with no reference to it passed')
    assert 'holds no reference' in message, message


def test_a_dashboard_defining_the_helper_and_nothing_else_is_refused(tmp):
    """The dashboard reference census, driven the same way."""
    written = Path(tmp) / 'section.js'
    written.write_text(
        'export async function extCmd(type) { return type; }\n',
        encoding='utf-8')
    message = _refusal_from(
        lambda: dashboard_sent_types([written]),
        'a dashboard defining the helper with no reference to it passed')
    assert 'holds no reference' in message, message


def test_a_surface_referencing_the_helper_but_never_calling_it_is_refused(tmp):
    """The call-site census: an import and a definition, and no call."""
    written = _python_source(tmp, 'commands_nocall.py', (
        'from .invoke import ext_cmd\n'
        '\n'
        'def ext_cmd(cmd_id, cmd_type, **fields): return cmd_type\n'))
    message = _refusal_from(
        lambda: python_sent_types([written], 'ext_cmd', False),
        'a surface with no direct call to the send helper passed')
    assert 'no direct call' in message, message


def test_a_python_call_unpacking_a_starred_argument_is_refused(tmp):
    """`ext_cmd(*pair, 'cookies')` hides the value that lands second.

    The second argument written is not the second argument passed, so
    reading it as the command type is a guess, not a read.
    """
    written = _python_source(tmp, 'commands_starred.py', (
        'from .invoke import ext_cmd\n'
        '\n'
        'def ext_cmd(cmd_id, cmd_type, **fields): return cmd_type\n'
        '\n'
        'def do_probe(pair):\n'
        "    return ext_cmd(*pair, 'probe-starred')\n"))
    message = _refusal_from(
        lambda: python_sent_types([written], 'ext_cmd', False),
        'a call unpacking a starred argument was accepted')
    assert 'commands_starred.py:6' in message, message
    assert 'starred' in message, message


def _js_source(tmp, name, body):
    written = Path(tmp) / name
    written.write_text(body, encoding='utf-8')
    return written


def _dashboard_reads(written):
    return lambda: dashboard_sent_types([written])


def _python_reads(written):
    return lambda: python_sent_types([written], 'ext_cmd', False)


def test_a_case_below_the_switch_block_is_refused(tmp):
    """A `case` token at depth two is not an arm of this switch."""
    del tmp
    source = _dispatch_source("case 'a': return { case: 1 };")
    message = _refusal_from(
        lambda: served_types(source, 'background.js'),
        'a case label below the switch block was accepted')
    assert 'sits below the switch block' in message, message


def test_a_switch_with_two_default_arms_is_refused(tmp):
    """The unknown-command arm is counted, not assumed."""
    del tmp
    source = ('function dispatchCommand(cmd) {\n  switch (cmd.type) {\n'
              "    case 'a': return 1;\n"
              '    default: return 0;\n    default: return 1;\n  }\n}\n')
    message = _refusal_from(
        lambda: served_types(source, 'background.js'),
        'a switch with two default arms was accepted')
    assert 'has 2 default arms' in message, message


def test_duplicate_case_labels_are_refused(tmp):
    """Two arms spelling the same type would read as one."""
    del tmp
    source = _dispatch_source("case 'a': return 1;", "case 'a': return 2;")
    message = _refusal_from(
        lambda: served_types(source, 'background.js'),
        'duplicate case labels were accepted')
    assert "duplicate case labels: ['a']" in message, message


def test_a_source_without_dispatch_command_is_refused(tmp):
    """The switch is located by name, so the name must be there."""
    del tmp
    source = ('function elsewhere(cmd) {\n  switch (cmd.type) {\n'
              "    case 'a': return 1;\n    default: return null;\n  }\n}\n")
    message = _refusal_from(
        lambda: served_types(source, 'background.js'),
        'a source with no dispatchCommand was accepted')
    assert 'no dispatchCommand function declaration' in message, message


def test_a_dashboard_type_argument_that_is_not_a_literal_is_refused(tmp):
    """The command type must be spelled where the reader can see it."""
    written = _js_source(tmp, 'section.js', (
        "import { extCmd } from '../api.js';\n"
        'export async function extCmd(type) { return type; }\n'
        'const answer = await extCmd(someVariable);\n'))
    message = _refusal_from(
        _dashboard_reads(written),
        'a dashboard call with a non-literal type was accepted')
    assert 'not a plain string literal' in message, message


def test_a_dashboard_with_two_definitions_is_refused(tmp):
    """The definition census is a count, not a presence check."""
    written = _js_source(tmp, 'section.js', (
        'export async function extCmd(type) { return type; }\n'
        'export function extCmd(type) { return type; }\n'
        "const answer = await extCmd('probe-two-defs');\n"))
    message = _refusal_from(
        _dashboard_reads(written),
        'a dashboard with two definitions was accepted')
    assert 'define extCmd exactly once' in message, message


def test_a_dashboard_send_call_with_no_argument_is_refused(tmp):
    """A call that passes no type cannot have its type enumerated."""
    written = _js_source(tmp, 'section.js', (
        "import { extCmd } from '../api.js';\n"
        'export async function extCmd(type) { return type; }\n'
        'const answer = await extCmd();\n'))
    message = _refusal_from(
        _dashboard_reads(written),
        'a dashboard send call with no argument was accepted')
    assert 'called with no argument' in message, message


def test_a_python_call_in_the_wrong_callee_form_is_refused(tmp):
    """The CLI surface reads a bare name, not an attribute."""
    written = _python_source(tmp, 'commands_form.py', (
        'def ext_cmd(cmd_id, cmd_type, **fields):\n'
        '    return cmd_type\n\n'
        'class Client:\n'
        '    pass\n\n'
        'def do_probe(client):\n'
        "    return client.ext_cmd('_probe', 'probe-form')\n"))
    message = _refusal_from(
        _python_reads(written),
        'an attribute call in a bare-name surface was accepted')
    assert 'is called in a shape this enumeration does not read' in message, \
        message


def test_a_python_call_with_no_type_argument_is_refused(tmp):
    """The type is the second positional argument; without one it is absent."""
    written = _python_source(tmp, 'commands_onearg.py', (
        'from .invoke import ext_cmd\n\n'
        'def ext_cmd(cmd_id, cmd_type, **fields):\n'
        '    return cmd_type\n\n'
        'def do_probe():\n'
        "    return ext_cmd('_probe')\n"))
    message = _refusal_from(
        _python_reads(written),
        'a call with no type argument was accepted')
    assert 'is given no second positional argument' in message, message


def test_a_python_import_binding_another_name_to_the_helper_is_refused(tmp):
    """`from x import other as ext_cmd` binds the wrong object to the name.

    The alias-target arm is a different condition from the aliased-import
    arm above it, and this is the only input that reaches it.
    """
    written = _python_source(tmp, 'commands_target.py', (
        'from .invoke import other as ext_cmd\n\n'
        'def ext_cmd(cmd_id, cmd_type, **fields):\n'
        '    return cmd_type\n\n'
        'def do_probe():\n'
        "    return ext_cmd('_probe', 'probe-target')\n"))
    message = _refusal_from(
        _python_reads(written),
        'an import binding another object to the helper name was accepted')
    assert 'binds another object to the name ext_cmd' in message, message


def test_a_python_payload_type_that_is_not_a_parameter_is_refused(tmp):
    """A module constant is not a parameter of anything."""
    written = _python_source(tmp, 'commands_dict.py', (
        'from .invoke import ext_cmd\n\n'
        "PROBE_TYPE = 'probe-dict'\n\n"
        'def ext_cmd(cmd_id, cmd_type, **fields):\n'
        '    return cmd_type\n\n'
        'def do_probe():\n'
        "    return {'type': PROBE_TYPE}\n\n"
        'def do_ok():\n'
        "    return ext_cmd('_probe', 'probe-ok')\n"))
    message = _refusal_from(
        _python_reads(written),
        'a payload type that is not a parameter was accepted')
    assert 'neither a plain string literal nor a parameter' in message, message


def test_a_client_forwarding_a_payload_outside_the_helpers_is_refused(tmp):
    """A third file forwarding a payload type is refused, not counted.

    Both mechanisms are needed to reach it: a literal call, so the surface
    clears its call-site census, and a payload whose type is a parameter,
    so the reader classifies it as a forwarding rather than refusing.
    """
    written = _python_source(tmp, 'commands_forward.py', (
        'from .invoke import ext_cmd\n\n'
        'def ext_cmd(cmd_id, cmd_type, **fields):\n'
        '    return cmd_type\n\n'
        'def do_probe(kind):\n'
        "    cmd = {'type': kind}\n"
        "    return ext_cmd('_probe', 'probe-forward') or cmd\n"))
    message = _refusal_from(
        _python_reads(written),
        'a payload forwarded from a third file was accepted')
    assert 'outside the two helper definitions' in message, message


def test_a_python_payload_type_from_a_sibling_scope_is_refused(tmp):
    """A sibling function's parameter name forwards nothing here.

    Parameters are collected per enclosing function, so a payload built in
    `do_probe` may only forward through `do_probe`'s own parameters. A
    file-wide name set would let `other`'s parameter forward a type that no
    scope around the payload ever bound.
    """
    written = _python_source(tmp, 'commands_sibling.py', (
        'from .invoke import ext_cmd\n\n'
        'def ext_cmd(cmd_id, cmd_type, **fields):\n'
        '    return cmd_type\n\n'
        'def other(cmd_type):\n'
        '    return cmd_type\n\n'
        'def do_probe():\n'
        "    return {'type': cmd_type}\n\n"
        'def do_ok():\n'
        "    return ext_cmd('_probe', 'probe-ok')\n"))
    message = _refusal_from(
        _python_reads(written),
        'a payload forwarding through a sibling scope was accepted')
    assert 'neither a plain string literal nor a parameter' in message, message


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='commandtypes_')


if __name__ == '__main__':
    raise SystemExit(main())
