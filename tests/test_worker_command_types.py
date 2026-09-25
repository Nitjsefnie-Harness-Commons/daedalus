#!/usr/bin/env python3
"""The worker's served command types, checked against every shipped client.

Issue 204: nothing enumerated the command types the extension worker serves,
so a `case` added to `dispatchCommand` that no client sends -- and no route
table row names -- passed every suite. The switch is read lexically, the
clients' call sites are read from the AST, and the two are compared in both
directions. Nothing is derived the same way twice, so neither read can vouch
for the other: the switch read is a depth-aware walk, the client read is an
AST walk, and a runtime dispatch pins the one served type no client names as
a literal.

Every reader refuses rather than skips. A `case` label or a client's type
argument that is not a plain string literal fails the suite with its shape
and its file:line named, so "no match found" is never read as "nothing to
check". Each reader carries a completeness marker computed by a different
mechanism from the extraction it counts: a raw token count against the
comment-and-string blanked one, plus a depth-aware walk, for the switch; a
raw substring count against a parsed call count for the dashboard.

A marker nothing observes is a comment. The switch's arm marker is policed
by a synthetic source that hides a case in a comment
(`test_a_case_hidden_in_a_comment_makes_the_arm_counts_disagree`). The
dashboard's marker has no such source and is only checked for agreement on
the shipped tree, where the two counts cannot disagree; closing that is left
to a later wave rather than claimed here.
"""
import ast
import re
import sys
from pathlib import Path

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
# call site fills. A payload type that forwards through any other parameter,
# or a module constant, is refused rather than counted as a sent type.
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

    Two markers, both live and both observed. The arm count is taken on the
    raw switch body and again on the comment-and-string blanked one; a case
    the masker hid makes the two disagree, so a regex that stopped matching
    cannot read as an empty switch. The labels themselves come from a
    depth-aware walk, so a `case` buried in a nested switch is refused by
    name instead of being counted and dropped.

    The shipped switch body hides no case, so the first marker is policed by
    a synthetic source in
    `test_a_case_hidden_in_a_comment_makes_the_arm_counts_disagree` rather
    than by anything in the tree.
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


def _parameter_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for group in (node.args.posonlyargs, node.args.args,
                          node.args.kwonlyargs):
                names.update(argument.arg for argument in group)
    return names


def python_sent_types(paths, watched, callee_is_attribute):
    """The command types `watched` transmits, from its call sites.

    A call whose type argument is not a plain string literal is refused by
    name: the clients send exactly one spelling of each type, and an
    indirect one would drop that type from the sent set silently.
    """
    literals = set()
    forwardings = set()
    call_sites = 0
    for path in sorted(paths):
        relative = _relative(path)
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=relative)
        parameters = _parameter_names(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and _callee_name(node.func) == watched):
                call_sites += 1
                at = f'{relative}:{node.lineno}'
                shape = (isinstance(node.func, ast.Attribute)
                         if callee_is_attribute
                         else isinstance(node.func, ast.Name))
                assert shape, (
                    f'{at}: {watched} is called in a shape this enumeration '
                    'does not read; every call must name the command type as '
                    'its second positional argument')
                assert len(node.args) >= 2, (
                    f'{at}: {watched} passes no second positional argument, '
                    'so the command type it sends is not enumerable here')
                value = node.args[1]
                assert isinstance(value, ast.Constant) and isinstance(
                    value.value, str), (
                    f'{at}: the command type passed to {watched} is not a '
                    'plain string literal, so the sent set cannot be '
                    f'enumerated from it: {ast.dump(value)}')
                literals.add(value.value)
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
    return literals, forwardings, call_sites


def dashboard_sent_types():
    """The command types the dashboard's `extCmd` transmits.

    The completeness marker is a raw `extCmd(` substring count over the
    comment-blanked sources against the number of sites this reader parsed,
    so a spelling the reader's own regex does not match -- a comment between
    the name and the paren, say -- is a mismatch rather than an absence.
    """
    literals = set()
    call_sites = 0
    definitions = 0
    substring_total = 0
    for path in sorted((ROOT / 'dashboard').rglob('*.js')):
        relative = _relative(path)
        text = path.read_text(encoding='utf-8')
        mask = js_mask(text)
        substring_total += mask.count('extCmd(')
        for match in re.finditer(r'\bextCmd\s*\(', mask):
            if re.search(r'\bfunction\s+$', mask[:match.start()]):
                definitions += 1
                continue
            call_sites += 1
            at = f'{relative}:{_line_of(text, match.start())}'
            open_paren = mask.index('(', match.start())
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
    assert substring_total == definitions + call_sites, (
        f'the dashboard holds {substring_total} extCmd( spellings but the '
        f'enumeration read {definitions} definition and {call_sites} call '
        'sites; a spelling it cannot parse is not an absence')
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


def test_a_case_label_that_is_not_a_plain_literal_is_refused(tmp):
    """A computed label is named with its line, not skipped."""
    del tmp
    source = (
        'function dispatchCommand(cmd) {\n'
        "  switch (cmd.type) {\n"
        "    case 'cookies': return handleCookies(cmd);\n"
        '    case COMMANDS.ping: return handlePing(cmd);\n'
        '    default: return null;\n'
        '  }\n'
        '}\n')
    try:
        served_types(source, 'background.js')
    except AssertionError as error:
        message = str(error)
        assert 'background.js:4' in message, message
        assert 'COMMANDS.ping' in message, message
        assert 'not a plain string literal' in message, message
    else:
        assert False, 'a computed case label was accepted'


def test_a_case_hidden_in_a_comment_makes_the_arm_counts_disagree(tmp):
    """The raw/masked arm marker fires, and names what it saw.

    The shipped switch body holds no comment naming a case, so nothing in
    the tree exercises this marker. Deleting the `js_mask` call outright
    leaves every other test green, because the two counts then agree by
    construction. This synthetic source is the only thing that observes the
    marker being able to fail, so it is what keeps the masker policed.
    """
    del tmp
    source = (
        'function dispatchCommand(cmd) {\n'
        '  switch (cmd.type) {\n'
        '    // a case label in a comment is not an arm\n'
        "    case 'cookies': return handleCookies(cmd);\n"
        '    default: return null;\n'
        '  }\n'
        '}\n')
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

    The exception is a decidable claim, not a waiver: the served set minus
    the sent set must hold exactly one type and that type must be `eval`,
    so a second one is reported by name rather than absorbed. With that one
    named, the residual comparison is a true two-way set equality and its
    message names both missing sides.
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


def test_a_type_the_worker_does_not_serve_reaches_the_unknown_arm(tmp):
    """The served-marker limb: an unserved type literal falls to `default`.

    Split from the eval limb on purpose. These two properties are broken by
    different edits, and when they shared one function the first assertion
    decided which of them ever ran.
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

    A command carrying only `code` must reach the eval handler, and the
    sentinel must see no `type` field on it. This observation is
    unconditional: nothing in this function can prevent it from running, so
    breaking the `code` -> `eval` derivation in the worker is reported here
    even though every served-type test stays green.
    """
    del tmp
    observed = run_extension_capability_routes([{
        'symbol': 'handleEval',
        'publishedSymbols': ['handleEval', 'handleCookies'],
        'command': _CODE_COMMAND,
    }])[0]
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
    try:
        python_sent_types([written], 'ext_cmd', False)
    except AssertionError as error:
        message = str(error)
        assert 'commands_probe.py:4' in message, message
        assert 'not a plain string literal' in message, message
    else:
        assert False, 'an indirect client command type was accepted'


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='commandtypes_')


if __name__ == '__main__':
    raise SystemExit(main())
