"""What module execution binds in a tests module's JavaScript.

The one copy of the JavaScript half of the re-implementation rule. A
helper is defined once in a shared helper module and imported by every
user; the same rule applied to JavaScript needs a reader for the second
language, because a `function` inside a Python string literal is not a
Python binding, and neither `pylint`'s duplicate-code checker nor this
repository's Python-scope controls can see it.

THE DOCUMENT. A JavaScript program here is not one string but an
expression: a harness builds it by concatenating raw literals with a
shared stub call spliced between them, and a body that opens in the
first literal closes in the last. So the unit is a `+` concatenation of
string constants — its constant operands in the order written, with a
non-constant operand becoming a space, which is bracket-neutral and so
leaves a body balanced across it. A constant not in such an expression
is a document of its own. A bare `ast.Expr` string statement is prose
about the module rather than a value in it, and is the one shape left
out; an f-string's literal parts are read with their doubled braces
undoubled, because the JavaScript they become has single ones.

THE ADMITTED SUBSET. Three declaration forms are recognised, and each
one binds a name a later call can reach:

  * `function name(...) {...}`, with or without `async`;
  * `const|let|var name = function (...) {...}`;
  * `const|let|var name = (...) => {...}` and `= (...) => <expression>`,
    and the same arrows over one bare parameter, `= name => ...`.

Deliberately NOT recognised, each for a stated reason. An unrecognised
form is a bypass, so each of these is named here rather than left as a
reader that silently stops:

  * object and class METHOD SHORTHAND (`addListener(listener) {...}`).
    A method binds no name a call can reach, so it is not a second
    definition of the shared helper's name; a harness that spells the
    helper as an object literal instead of a factory is a different
    mechanism, and this reader cannot see it. A shared factory
    re-implemented as a method on an object is a bypass of THIS rule
    for exactly that reason.
  * an ANONYMOUS function expression, and an IIFE. Neither binds a
    name, so neither can be the shared helper's name.
  * a class declaration, and any other name-binding form (`var` inside
    a `for` head, a function reached through a computed property or a
    spread, a destructuring default). This reader has no object-literal
    and no statement-context model, so it finds declarations at the top
    level of a document and nothing nested inside one.
  * a function whose body opens and never closes while PROGRAM FOLLOWS
    it in the same document. That raises, naming the constant: the
    reader has lost the plot, and every later declaration would be
    read at the wrong nesting.

Two boundaries this reader draws rather than hides. A document that
simply STOPS inside a body is a fragment — a list of lines a later step
joins, a fixture built to attack a wrapper's delimiters — and is
dropped, because there is no program after it to mis-attribute; a
function split across two separate Python expressions is dropped for
the same reason, and is a named bypass. And the regexp/comment
decision after `/` uses the usual previous-token heuristic extended
with the control-statement head rule, so `if (x) /re/.test(y)` reads as
a regexp where `total / count` reads as division.

SIZE. `body_lines` counts the lines from a body's first line to its
last, which is the measure `min-similarity-lines` uses on a Python
function body. It is a property of the declaration; flooring it is the
caller's, and the floor is scoped to this rule because a Python
function-body threshold says nothing about a five-line JavaScript
block.
"""
import ast
import re
from bisect import bisect_right
from collections import namedtuple

IDENTIFIER = re.compile(r'[A-Za-z_$][\w$]*')
PUNCTUATION = set('{}()[];,.<>=+-*/%!&|^~?:')
TWO_CHAR = ('=>', '==', '!=', '<=', '>=', '&&', '||', '??', '++', '--', '+=',
            '-=', '*=', '/=', '**', '...')
OPENERS = {'(': ')', '[': ']', '{': '}'}
CLOSERS = (')', ']', '}')
KEYWORDS_BEFORE_REGEXP = frozenset({
    'return', 'typeof', 'instanceof', 'in', 'of', 'new', 'delete', 'void',
    'throw', 'case', 'do', 'else', 'yield', 'await'})
CONTROL_HEADS = frozenset({'if', 'while', 'for', 'with', 'switch', 'catch'})

Declaration = namedtuple('Declaration', 'name offset body_lines')
_Unreadable = namedtuple('_Unreadable', 'name offset')


class _JsToken:
    __slots__ = ('kind', 'text', 'line')

    def __init__(self, kind, text, line):
        self.kind = kind
        self.text = text
        self.line = line


def _concatenations(tree, parents):
    """The maximal `a + b + c` string expressions in a module.

    One such expression is one string, so it is the unit a JavaScript
    program is read in: a harness's two raw literals with a shared stub
    call spliced between them are a single document whose pieces are its
    constant operands, in the order they are written. Reading the
    module's constants in source order instead would interleave two
    unrelated programs, and a brace from one would close a function in
    the other.
    """
    found = []
    for node in ast.walk(tree):
        if not _carries_a_string(node) or not _is_concatenation(node):
            continue
        if _is_concatenation(parents.get(id(node))):
            continue
        found.append(node)
    return found


def _is_concatenation(node):
    return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
            and _carries_a_string(node))


def _carries_a_string(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _carries_a_string(node.left) or _carries_a_string(node.right)
    if isinstance(node, ast.JoinedStr):
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _strings_in(node, out, consumed):
    """The constant operands of a concatenation, in order; a non-constant
    operand becomes a space, which is what keeps a body's braces
    balanced across the shared stub spliced between two literals."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        _strings_in(node.left, out, consumed)
        _strings_in(node.right, out, consumed)
    elif isinstance(node, ast.JoinedStr):
        for part in node.values:
            _strings_in(part, out, consumed)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        consumed.add(id(node))
        out.append((_unbraced(node.value), node.lineno))
    else:
        out.append((' ', node.lineno))


def _unbraced(value):
    """An f-string's doubled braces, as the JavaScript sees them.

    The AST holds the literal parts of an f-string with `{{` and `}}`
    still doubled, so a body written inside one has twice the braces the
    program it will become has. Undoing that here is what keeps a
    function's body balanced in a harness built from an f-string.
    """
    return value.replace('{{', '{').replace('}}', '}')


def _parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def documents(source, path='?'):
    """Every JavaScript document in a module, as (text, starts) pairs.

    `starts` is the sorted [(offset, Python lineno)] of each piece, so an
    offset in the joined text maps back to the constant it came from.
    A bare `ast.Expr` string statement is prose about the module rather
    than a value in it, and is the one shape left out.
    """
    tree = ast.parse(source, filename=path)
    prose = {id(statement.value) for statement in ast.walk(tree)
             if isinstance(statement, ast.Expr)}
    parents = _parent_map(tree)
    joined = []
    consumed = set()
    for node in _concatenations(tree, parents):
        pieces = []
        _strings_in(node, pieces, consumed)
        joined.append(_join(pieces))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant)
                and isinstance(node.value, str)):
            continue
        if id(node) in prose or id(node) in consumed:
            continue
        joined.append(_join([(_unbraced(node.value), node.lineno)]))
    return joined


def _join(pieces):
    text = ''
    starts = []
    for value, lineno in pieces:
        starts.append((len(text), lineno))
        text += value + '\n'
    return text, starts


def lineno_at(starts, offset):
    """The Python line the joined text's `offset` came from."""
    offsets = [start for start, _lineno in starts]
    return starts[max(bisect_right(offsets, offset) - 1, 0)][1]


def _lex(text):
    """The token stream for one JavaScript document.

    Tolerant throughout, and deliberately so: a tests module's string
    constants also carry Python source, shell, prose and regular
    expressions, and none of that is what this rule is about. An
    unmodelled character becomes a `junk` token, an unterminated string,
    template, regexp or comment is skipped one character at a time, and
    a bracket that closes the wrong thing abandons the whole bracket
    stack rather than leaving a phantom region behind. None of that is
    an error here; the one thing this reader refuses to guess at is a
    function BODY it cannot find the end of, and `declarations` is where
    that refusal is made.
    """
    tokens = []
    stack = []
    heads = []
    expect = None
    pending = None
    previous = None
    control = False
    closed_head = False
    index = 0
    line = 1
    size = len(text)

    def emit(kind, value, at):
        nonlocal previous
        tokens.append(_JsToken(kind, value, at))
        previous = tokens[-1]

    def allows_regexp():
        if previous is None:
            return True
        if previous.kind == 'name':
            return previous.text in KEYWORDS_BEFORE_REGEXP
        if previous.kind == 'punct':
            if previous.text == ')':
                return closed_head
            return previous.text not in (']', '}', '++', '--')
        return False

    while index < size:
        char = text[index]
        if char == '\n':
            line += 1
            index += 1
            continue
        if char.isspace():
            index += 1
            continue
        if allows_regexp() and text.startswith('//', index):
            stop = text.find('\n', index)
            index = size if stop < 0 else stop
            continue
        if allows_regexp() and text.startswith('/*', index):
            stop = text.find('*/', index + 2)
            if stop < 0:
                index += 2
                continue
            line += text.count('\n', index, stop)
            index = stop + 2
            continue
        if char in '"\'':
            stop, closed = _quoted(text, index, char)
            if not closed:
                index += 1
                continue
            emit('string', text[index:stop + 1], line)
            index = stop + 1
            expect = pending = None
            continue
        if char == '`':
            stop, closed = _template_end(text, index)
            if not closed:
                index += 1
                continue
            line += text.count('\n', index, stop)
            emit('template', text[index:stop + 1], line)
            index = stop + 1
            expect = pending = None
            continue
        if char == '/' and allows_regexp():
            stop, closed = _regexp(text, index)
            if not closed:
                index += 1
                continue
            tail = stop + 1
            while tail < size and text[tail].isalpha():
                tail += 1
            emit('regexp', text[index:tail], line)
            index = tail
            expect = pending = None
            continue
        matched = IDENTIFIER.match(text, index)
        if matched:
            word = matched.group()
            if word == 'function':
                pending, expect = None, 'name'
            elif expect == 'name':
                pending, expect = word, 'paren'
            else:
                expect = pending = None
            control = word in CONTROL_HEADS
            emit('name', word, line)
            index = matched.end()
            continue
        if char.isdigit() or (char == '.'
                              and text[index + 1:index + 2].isdigit()):
            stop = index
            while stop < size and (text[stop].isalnum()
                                   or text[stop] in '._'):
                stop += 1
            emit('number', text[index:stop], line)
            index = stop
            expect = pending = None
            continue
        if text[index:index + 2] in TWO_CHAR:
            emit('punct', text[index:index + 2], line)
            index += 2
            expect = pending = None
            continue
        if char in OPENERS:
            if char == '(':
                heads.append(control)
                control = False
                if expect == 'paren' and pending:
                    expect = pending = None
            stack.append(OPENERS[char])
            emit('punct', char, line)
            index += 1
            continue
        if char in CLOSERS:
            if char == ')':
                closed_head = bool(heads) and heads.pop()
            if not stack or stack.pop() != char:
                stack.clear()
            emit('punct', char, line)
            index += 1
            continue
        if char in PUNCTUATION:
            emit('punct', char, line)
        else:
            emit('junk', char, line)
        index += 1
    return tokens


def _quoted(text, start, quote):
    index = start + 1
    while index < len(text):
        if text[index] == '\\':
            index += 2
            continue
        if text[index] == quote:
            return index, True
        if text[index] == '\n':
            return index, False
        index += 1
    return index, False


def _template_end(text, start):
    index = start + 1
    holes = 0
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if text.startswith('${', index):
            holes += 1
            index += 2
            continue
        if holes and char == '}':
            holes -= 1
            index += 1
            continue
        if char == '`' and not holes:
            return index, True
        index += 1
    return index, False


def _regexp(text, start):
    index = start + 1
    in_class = False
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if char == '[':
            in_class = True
        elif char == ']':
            in_class = False
        elif char == '/' and not in_class:
            return index, True
        elif char == '\n':
            return index, False
        index += 1
    return index, False


def _closing(tokens, index):
    """The index of the token closing the bracket `tokens[index]` opens,
    or None when this reader cannot find one."""
    stack = [OPENERS[tokens[index].text]]
    cursor = index + 1
    while cursor < len(tokens):
        token = tokens[cursor]
        if token.kind == 'punct' and token.text in OPENERS:
            stack.append(OPENERS[token.text])
        elif token.kind == 'punct' and token.text in CLOSERS:
            if token.text != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return cursor
        cursor += 1
    return None


def _arrow(tokens, index):
    """The `{` a function's parameter list is followed by, the token just
    past an expression-bodied arrow's, or None for neither.

    `index` is the parameter list's `(`, so the token that decides is the
    one after the `)` that closes it: `=>` for an arrow, `{` for a
    `function` body.
    """
    after = _closing(tokens, index)
    if after is None or after + 1 >= len(tokens):
        return None
    following = tokens[after + 1]
    if following.kind == 'punct' and following.text == '=>':
        return after + 2
    if following.kind == 'punct' and following.text == '{':
        return after + 1
    return None


def _assigned(tokens, index):
    """The declaration in a `const|let|var NAME = <function>` head."""
    if (index + 2 >= len(tokens) or tokens[index + 1].kind != 'name'
            or tokens[index + 2].kind != 'punct'
            or tokens[index + 2].text != '='):
        return None
    name = tokens[index + 1].text
    cursor = index + 3
    if (cursor < len(tokens) and tokens[cursor].kind == 'name'
            and tokens[cursor].text == 'async'):
        cursor += 1
    if (cursor < len(tokens) and tokens[cursor].kind == 'name'
            and tokens[cursor].text == 'function'):
        cursor += 1
        if (cursor < len(tokens) and tokens[cursor].kind == 'name'
                and tokens[cursor].text != '('):
            cursor += 1
    if (cursor < len(tokens) and tokens[cursor].kind == 'name'
            and cursor + 1 < len(tokens)
            and tokens[cursor + 1].kind == 'punct'
            and tokens[cursor + 1].text == '=>'):
        # `name => ...`: an arrow over one bare parameter, whose
        # parameter list is not written at all.
        body = cursor + 2
    else:
        if (cursor >= len(tokens) or tokens[cursor].kind != 'punct'
                or tokens[cursor].text != '('):
            return None
        body = _arrow(tokens, cursor)
    if body is None:
        return None
    if tokens[body].kind == 'punct' and tokens[body].text == '{':
        end = _closing(tokens, body)
        if end is None:
            if _ends_here(tokens, body):
                return None
            return _Unreadable(name, tokens[index].line), index + 1
        return (Declaration(name, tokens[index].line,
                            tokens[end].line - tokens[body].line + 1), end)
    return Declaration(name, tokens[index].line, 1), body


def _ends_here(tokens, index):
    """Whether the document stops at the bracket `tokens[index]` opens.

    A tests module also holds JavaScript that is a FRAGMENT on purpose: a
    list of lines a later step joins, a fixture built to attack a
    wrapper's delimiters. Such a document runs out mid-body, and there is
    no program after it for the reader to mis-attribute, so the function
    is not a declaration this reader can measure and is not reported. A
    body that opens with program still following it is a different thing
    — the reader has lost the plot and every later declaration would be
    read at the wrong nesting — and that is what raises.
    """
    return all(token.kind == 'junk' for token in tokens[index + 1:])


def _function_at(tokens, index):
    """(declaration, index after it) for a `function` keyword token."""
    cursor = index + 1
    if (cursor < len(tokens) and tokens[cursor].kind == 'punct'
            and tokens[cursor].text == '*'):
        cursor += 1
    name = None
    if cursor < len(tokens) and tokens[cursor].kind == 'name' \
            and tokens[cursor].text != '(':
        name = tokens[cursor].text
        cursor += 1
    if cursor < len(tokens) and tokens[cursor].kind == 'punct' \
            and tokens[cursor].text == '(':
        closed = _closing(tokens, cursor)
        if closed is None:
            return None, index + 1
        cursor = closed + 1
    if cursor >= len(tokens) or tokens[cursor].kind != 'punct' \
            or tokens[cursor].text != '{':
        return None, index + 1
    end = _closing(tokens, cursor)
    if end is None:
        if name is None or _ends_here(tokens, cursor):
            return None, index + 1
        return _Unreadable(name, tokens[index].line), index + 1
    if name is None:
        return None, end + 1
    return (Declaration(name, tokens[index].line,
                        tokens[end].line - tokens[cursor].line + 1), end + 1)


def declarations(text, where='?'):
    """Every recognised declaration in one JavaScript document.

    Each is a `Declaration`: the bound name, the offset the `function`
    keyword or the `const` sits at, and the body's line span — so a
    caller can report the Python line it came from and floor the size.

    A `function name(...) {` whose body this reader cannot find the end
    of raises, naming the constant: that is the one thing the tolerant
    reader refuses to guess at, because a function silently absent from
    the scan that exists to find it is a bypass with no trace.
    """
    tokens = _lex(text)
    found = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind == 'name' and token.text == 'function':
            declaration, index = _function_at(tokens, index)
        elif token.kind == 'name' and token.text in ('const', 'let', 'var'):
            assigned = _assigned(tokens, index)
            if assigned is None:
                index += 1
                continue
            declaration, index = assigned
        else:
            index += 1
            continue
        if isinstance(declaration, _Unreadable):
            raise AssertionError(
                f'{where}: function {declaration.name} opens a body at line '
                f'{declaration.offset} that never closes')
        if declaration is not None:
            found.append(declaration)
    return found
