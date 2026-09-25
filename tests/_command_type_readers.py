"""The readers behind the worker command-type guard.

Not a suite itself; the controls that observe these readers live in
``tests/test_worker_command_types.py``. Each reader enumerates the IDENTITY
of the thing it counts -- the dispatch switch's case labels, the type
literals the shipped clients transmit -- rather than the spelling of a call,
and refuses any reference shape it cannot enumerate, naming the file, the
line and the shape. Two censuses per client surface keep "no reference
found" from reading as "nothing to check": the send helper must be defined
exactly once, and at least one reference to it must exist.

What is refused, precisely
-------------------------
A client may reach its send helper in four ways, and three are enumerated
and the fourth is refused:

1. ``ext_cmd('_id', 'cookies')`` / ``bridge.ext_cmd(...)`` / ``extCmd(...)``
   -- a direct call whose type argument is a plain string literal. READ.
2. ``from .invoke import ext_cmd`` / ``import { extCmd }`` -- a binding
   clause. COUNTED as a reference, never treated as a call.
3. ``def ext_cmd`` / ``function extCmd`` -- the single definition. COUNTED,
   and the definition census requires exactly one per surface.
4. anything else that names the identifier -- a binding, a collection
   member, a decorator, a parenthesised name, or a STRING equal to the
   helper's name, which is how a dynamic lookup such as
   ``getattr(bridge, 'ext_cmd')`` or ``globalThis['extCmd']`` reaches the
   send path. REFUSED with file, line and shape.

The string rule is a superset: a string that merely MENTIONS the helper
(``'the ext_cmd helper'``) is prose and is not a reference, but a string
EQUAL to the identifier is refused wherever it sits, because that is the only
way a dynamic lookup can name the helper.

The one shape that is neither read nor refused
----------------------------------------------
A COMPUTED name -- ``getattr(bridge, 'ext' + '_cmd')`` or
``globalThis['ext' + 'Cmd']`` -- is neither a literal nor a reference node, so
it is invisible to both the identifier walk and the string rule. This is a
named limit, not an enforced invariant, and it is bounded on both sides:

* A computed command TYPE is already refused -- the direct-call branch
  requires a plain string literal in the type position -- so nothing in a
  shipped client can send a computed type. That half is ENFORCED by the
  code, not merely true of the tree.
* A computed helper NAME is what remains, and no shipped client does it:
  the nine ``+`` expressions across ``daedalus_cli`` and ``daedalus_mcp``
  build URLs, arithmetic and a print, none a helper name. That half is a
  fact about the tree, restated here so the next reader knows it is not
  being checked.
"""
import ast
import re
import sys
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _jsread import (js_bracket_end, js_mask,  # noqa: E402
                     js_split_top_level)
from _repo import ROOT  # noqa: E402

_DISPATCH = 'dispatchCommand'
# A plain string literal: one quote style, no escape, no interpolation. A
# template or a computed value is a shape the enumeration does not read.
_STRING_LITERAL = re.compile(r"'([^'\\\n]*)'|\"([^\"\\\n]*)\"")

# The two helper definitions whose second parameter every proved-literal
# call site fills. Any other indirection is refused, not counted.
FORWARDING_FILES = frozenset({
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
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value.strip() == watched):
                _refuse(
                    f'{relative}:{node.lineno}',
                    f'a string literal names {watched}. A dynamic lookup '
                    f'(getattr(bridge, {watched!r})) can only reach the send '
                    'path through the helper name as a string, and this '
                    'enumeration refuses every such string')
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
        for match in re.finditer(r"(['\"`])extCmd\1", text):
            _refuse(
                f'{relative}:{_line_of(text, match.start())}',
                "a string literal names extCmd. A dynamic lookup "
                "(globalThis['extCmd']) can only reach the send path "
                'through the helper name as a string, and this enumeration '
                'refuses every such string')
    assert definitions == 1, (
        f'the dashboard must define extCmd exactly once; found {definitions}')
    assert references, (
        'the dashboard holds no reference to extCmd at all; a renamed '
        'helper is a refusal, not an empty sent set')
    return literals


def clients():
    """Each shipped client's sent set, with the reading that produced it."""
    cli = python_sent_types((ROOT / 'daedalus_cli').rglob('*.py'),
                            'ext_cmd', False)
    mcp = python_sent_types((ROOT / 'daedalus_mcp').rglob('*.py'),
                            'ext_cmd', True)
    sent = {'cli': cli[0], 'mcp': mcp[0],
            'dashboard': dashboard_sent_types()}
    return sent, cli, mcp
