#!/usr/bin/env python3
"""The worker's served command types, checked against every shipped client.

Issue 204: nothing enumerated the command types the extension worker serves,
so a `case` added to `dispatchCommand` that no client sends -- and no route
table row names -- passed every suite. The switch is read lexically, the
clients' call sites are read from the AST, and the two are compared in both
directions. Nothing is derived the same way twice, so neither read can vouch
for the other, and a runtime dispatch pins the one served type no client
names as a literal.

Every reader refuses rather than skips, and each keys on the IDENTITY of the
thing it enumerates rather than on a call's spelling: a `case` label, a
client's type argument, or any reference to a client's send helper. A shape
a reader cannot enumerate fails with its file, line and shape named, so "no
match found" is never read as "nothing to check".

A marker nothing observes is a comment, so every marker here has a synthetic
source that manufactures the input it exists to catch, or a census that
refuses a surface where it cannot fire.
"""
import ast
import re
import sys
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary import (run_extension_capability_routes,  # noqa: E402
                       run_extension_command_result)
from _jsread import (js_bracket_end, js_mask,  # noqa: E402
                     js_split_top_level)
from _repo import ROOT  # noqa: E402
from _worker_routes import ROUTES  # noqa: E402

_DISPATCH = 'dispatchCommand'
# A plain string literal: one quote style, no escape, no interpolation. A
# template or a computed value is a shape the enumeration does not read.
_STRING_LITERAL = re.compile(r"'([^'\\\n]*)'|\"([^\"\\\n]*)\"")
_CODE_COMMAND = {'id': 'code-path-control', 'code': 'return 1'}
# The two helper definitions whose second parameter every proved-literal
# call site fills. Any other indirection is refused, not counted.
_FORWARDING_FILES = frozenset({
    'daedalus_cli/invoke.py',
    'daedalus_mcp/transport.py',
})


def _relative(path):
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _line_of(text, offset):
    return text.count('\n', 0, offset) + 1


def _literal_value(text):
    """The string a plain literal spells, or None for any other shape."""
    match = _STRING_LITERAL.fullmatch(text)
    if match is None:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def _depth_at(mask, offset):
    depth = 0
    for char in mask[:offset]:
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
    return depth


def _function_body(mask, name):
    match = re.search(r'\bfunction\s+' + re.escape(name) + r'\s*\(', mask)
    assert match is not None, f'no {name} function declaration was found'
    brace = mask.index('{', match.end())
    return brace, js_bracket_end(mask, brace)


def _switch_body(source, mask, path):
    """The one switch of `dispatchCommand`, as offsets into `source`."""
    outer, inner = _function_body(mask, _DISPATCH)
    found = [match.start() for match in re.finditer(r'\bswitch\b', mask)]
    inside = len(found) == 1 and outer < found[0] < inner
    named = [f'{path}:{_line_of(source, at)}' for at in found]
    assert inside, (
        f'exactly one switch must sit inside {_DISPATCH}, and every switch '
        f'in the file is read: found {named}')
    close = js_bracket_end(mask, mask.index('(', found[0]))
    brace = mask.index('{', close)
    return brace, js_bracket_end(mask, brace)


def served_types(source=None, path=None):
    """The case labels `dispatchCommand` dispatches on, in source order.

    The arm count is taken raw and again blanked, so a case the masker hid
    is a disagreement rather than an absent case; the labels come from a
    depth-aware walk, so a nested `case` is refused by name. The shipped
    body hides no case, so the first marker needs a synthetic source.
    """
    if source is None:
        read = ROOT / 'extension' / 'background.js'
        source = read.read_text(encoding='utf-8')
        path = _relative(read)
    mask = js_mask(source)
    start, end = _switch_body(source, mask, path)
    body, body_mask = source[start:end], mask[start:end]
    raw_arms = len(re.findall(r'\bcase\b', body))
    tokens = [match.start() for match in re.finditer(r'\bcase\b', body_mask)]
    assert raw_arms == len(tokens), (
        f'{path}: the dispatch switch body holds {raw_arms} case tokens and '
        f'the masked body {len(tokens)}; a case the masker hid is not an '
        'absent case')
    labels = []
    for offset in tokens:
        here = f'{path}:{_line_of(source, start + offset)}'
        assert _depth_at(body_mask, offset) == 1, (
            f'{here}: a case label sits below the switch block; this '
            'enumeration reads only the arms of the switch itself')
        text = body[offset + len('case'):body_mask.index(':', offset)].strip()
        value = _literal_value(text)
        assert value is not None, (
            f'{here}: the case label is not a plain string literal, so the '
            f'served set cannot be enumerated from it: {text!r}')
        labels.append(value)
    defaults = len(re.findall(r'\bdefault\b', body_mask))
    assert defaults == 1, (
        f'{path}: the dispatch switch has {defaults} default arms; exactly '
        'one is the unknown-command arm this guard reads')
    duplicates = sorted({label for label in labels if labels.count(label) > 1})
    assert not duplicates, f'{path}: duplicate case labels: {duplicates}'
    return labels


def _callee_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _parents(tree):
    return {id(child): parent for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)}


def _reference_shape(node, parents):
    """The named shape a non-call reference to the helper has."""
    parent = parents.get(id(node))
    if parent is None:
        return 'module scope'
    if isinstance(parent, ast.Call):
        return 'callee' if parent.func is node else 'call argument'
    return type(parent).__name__.lower()


def _refuse(where, what) -> NoReturn:
    """Terminal refusal naming where and what. Never a skip."""
    raise AssertionError(f'{where}: {what}')


def _parameter_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for group in (node.args.posonlyargs, node.args.args,
                          node.args.kwonlyargs):
                names.update(argument.arg for argument in group)
    return names


def python_sent_types(paths, watched, callee_is_attribute):
    """The command types `watched` transmits, from every reference to it.

    A direct call carrying a plain string literal in the type position is
    the only readable shape; every other reference is a refusal naming its
    file, line and shape, so binding the helper and calling the binding
    cannot drop a type out of the sent set. The two censuses -- exactly one
    definition, at least one reference -- keep a rename that leaves this
    reader matching nothing from reading as an empty sent set.
    """
    literals = set()
    forwardings = set()
    call_sites = 0
    definitions = []
    references = 0
    for path in sorted(paths):
        relative = _relative(path)
        text = path.read_text(encoding='utf-8')
        tree = ast.parse(text, filename=relative)
        parents = _parents(tree)
        parameters = _parameter_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == watched:
                    definitions.append(f'{relative}:{node.lineno}')
                continue
            if isinstance(node, ast.alias):
                if node.asname == watched:
                    _refuse(
                        f'{relative}:{node.lineno}',
                        f'{node.name} binds another object to the name '
                        f'{watched}; the import must name {watched} itself')
                elif node.name == watched:
                    references += 1
                continue
            if (isinstance(node, (ast.Name, ast.Attribute))
                    and _callee_name(node) == watched):
                parent = parents.get(id(node))
                if (parent is None or not isinstance(parent, ast.Call)
                        or parent.func is not node):
                    _refuse(
                        f'{relative}:{node.lineno}',
                        f'{watched} is referenced here outside a direct '
                        f'call: shape {_reference_shape(node, parents)!r}. A '
                        'client that binds the helper and calls the binding '
                        'sends a command type this enumeration cannot read')
                references += 1
                at = f'{relative}:{parent.lineno}'
                form = (isinstance(node, ast.Attribute)
                        if callee_is_attribute else isinstance(node, ast.Name))
                assert form, (
                    f'{at}: {watched} is called in a shape this enumeration '
                    'does not read; every call must name the command type as '
                    'its second positional argument')
                assert len(parent.args) >= 2, (
                    f'{at}: {watched} passes no second positional argument, '
                    'so the command type it sends is not enumerable here')
                value = parent.args[1]
                assert isinstance(value, ast.Constant) and isinstance(
                    value.value, str), (
                    f'{at}: the command type passed to {watched} is not a '
                    'plain string literal, so the sent set cannot be '
                    f'enumerated from it: {ast.dump(value)}')
                literals.add(value.value)
                call_sites += 1
                continue
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if not (isinstance(key, ast.Constant)
                            and key.value == 'type'):
                        continue
                    if (isinstance(value, ast.Constant)
                            and isinstance(value.value, str)):
                        literals.add(value.value)
                        continue
                    at = f'{relative}:{value.lineno}'
                    assert isinstance(value, ast.Name) and (
                        value.id in parameters), (
                        f'{at}: a command payload type is neither a plain '
                        'string literal nor a parameter of the function it '
                        'is built in, so the sent set cannot be enumerated '
                        f'from it: {watched} payload {value!r}')
                    forwardings.add(relative)
    assert len(definitions) == 1, (
        f'the {watched} surface must define {watched} exactly once; found '
        f'{sorted(definitions)}')
    assert references, (
        f'the {watched} surface holds no reference to {watched} at all; a '
        'renamed helper is a refusal, not an empty sent set')
    return literals, forwardings, call_sites


def _enclosing_open_brace(mask, offset):
    """The `{` opening the block the token at `offset` sits in, or -1."""
    depth = 0
    for position in range(offset - 1, -1, -1):
        char = mask[position]
        if char in ')]}':
            depth += 1
        elif char in '([{':
            if depth:
                depth -= 1
            elif char == '{':
                return position
            else:
                return -1
    return -1


def _is_import_binding(mask, offset):
    """Whether the occurrence sits in an `import { ... }` clause."""
    brace = _enclosing_open_brace(mask, offset)
    if brace < 0:
        return False
    position = brace - 1
    while position >= 0 and mask[position].isspace():
        position -= 1
    return re.search(r'\bimport$', mask[:position + 1]) is not None


def dashboard_sent_types(paths=None):
    """The command types the dashboard's `extCmd` transmits.

    Scans the blanked source for the IDENTIFIER, not for the `extCmd(`
    spelling, so a reference reaching the send path through a binding or a
    parenthesised name is found and refused rather than skipped. Readable:
    the single definition, a direct call, an `import { ... }` binding.
    `paths` is a seam for the synthetic-source controls.
    """
    if paths is None:
        paths = (ROOT / 'dashboard').rglob('*.js')
    literals = set()
    call_sites = 0
    definitions = 0
    references = 0
    for path in sorted(paths):
        relative = _relative(path)
        text = path.read_text(encoding='utf-8')
        mask = js_mask(text)
        for match in re.finditer(r'\bextCmd\b', mask):
            references += 1
            at = f'{relative}:{_line_of(text, match.start())}'
            if re.search(r'\bfunction\s+$', mask[:match.start()]):
                definitions += 1
                continue
            after = match.end()
            while after < len(mask) and mask[after].isspace():
                after += 1
            following = mask[after] if after < len(mask) else ''
            if following != '(':
                if _is_import_binding(mask, match.start()):
                    continue
                _refuse(
                    at,
                    f'extCmd is referenced and {following!r} follows it, so '
                    'it is not a direct call; a call that reaches the send '
                    'path without naming extCmd immediately before its paren '
                    'is a shape this enumeration does not read')
            call_sites += 1
            open_paren = after
            arguments = js_split_top_level(
                mask, text, open_paren + 1,
                js_bracket_end(mask, open_paren) - 1)
            assert arguments, f'{at}: extCmd was called with no argument'
            first = text[arguments[0][0]:arguments[0][1]].strip()
            value = _literal_value(first)
            assert value is not None, (
                f'{at}: the command type passed to extCmd is not a plain '
                'string literal, so the sent set cannot be enumerated from '
                f'it: {first!r}')
            literals.add(value)
    assert definitions == 1, (
        f'the dashboard must define extCmd exactly once; found {definitions}')
    assert references, (
        'the dashboard holds no reference to extCmd at all; a renamed '
        'helper is a refusal, not an empty sent set')
    return literals


def _clients():
    """Each shipped client's sent set, with the reading that produced it."""
    cli = python_sent_types((ROOT / 'daedalus_cli').rglob('*.py'),
                            'ext_cmd', False)
    mcp = python_sent_types((ROOT / 'daedalus_mcp').rglob('*.py'),
                            'ext_cmd', True)
    sent = {'cli': cli[0], 'mcp': mcp[0],
            'dashboard': dashboard_sent_types()}
    return sent, cli, mcp


def test_the_dispatch_switch_is_read_and_its_labels_enumerated(tmp):
    """The served set is read whole, and every label is a usable type.

    A case token the masker hid, or one buried below the switch block, is
    refused inside the reader rather than shrinking the served set.
    """
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
    """A minimal `dispatchCommand` whose switch holds `arms`."""
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

    The shipped switch body holds no comment naming a case, so nothing in
    the tree exercises this marker: deleting the `js_mask` call outright
    leaves every other test green, because the two counts then agree by
    construction.
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

    The exception is a decidable claim, not a waiver: exactly one type, and
    it must be `eval`, so a second is reported by name. The residual
    comparison is then a true two-way equality naming both missing sides.
    """
    del tmp
    sent_sets, cli, mcp = _clients()
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
    assert (cli[1] | mcp[1]) <= _FORWARDING_FILES, (
        'a client payload forwards its command type through a parameter '
        f'outside the two helper definitions: '
        f'{sorted((cli[1] | mcp[1]) - _FORWARDING_FILES)}')
    assert cli[2] and mcp[2], (
        f'no ext_cmd call site was read: cli={cli[2]}, mcp={mcp[2]}')


def _eval_path_observation():
    """The code-only command's route observation, or a named failure.

    A worker that stops deriving `eval` from `code` sends the command to
    the default arm, which posts a result the scenario does not declare; the
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

    Unconditional on purpose: nothing in this function can prevent the
    observation, so breaking the `code` -> `eval` derivation is reported
    here even though every served-type test stays green.
    """
    del tmp
    observed = _eval_path_observation()
    assert observed['callCount'] == 1 and observed['answered'], observed
    assert 'calledType' not in observed, (
        f'the eval handler saw a type field on the code command: {observed}')


def test_the_dashboard_command_scan_accounts_for_every_call_site(tmp):
    """The dashboard's sent set is read from every `extCmd(` spelling."""
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

    Considered because the reader enumerates the identifier, not the call
    spelling, and refused because only a direct literal call is readable.
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

    Without this, renaming the helper would leave the reader matching
    nothing and report a clean bill of health, which is the failure the
    whole enumeration exists to prevent.
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

    Found because the reader looks for the identifier, not for `extCmd(`,
    and refused because `(` is not what follows the name here.
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
    """The over-recognition direction: prose must not be read as a call.

    A Python string and a JavaScript string naming the helper are invisible
    to their readers, and a JavaScript comment is blanked before the scan.
    Without that masking the string below would be classified and refused.
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
        "const label = 'extCmd';\n"
        'export async function extCmd(type) { return type; }\n'
        "const answer = await extCmd('probe-js-prose');\n",
        encoding='utf-8')
    assert dashboard_sent_types([js_written]) == {'probe-js-prose'}


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='commandtypes_')


if __name__ == '__main__':
    raise SystemExit(main())
