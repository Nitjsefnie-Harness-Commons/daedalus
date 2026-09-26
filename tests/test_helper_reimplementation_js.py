#!/usr/bin/env python3
"""The JavaScript half of the re-implementation rule, over a fake tree.

Every control here runs `js_reimplementations` on a `{path: text}` map
built in this file, so each case is a program a tests module could
contain and is compiled before it is read — `ast.parse` accepts a string
whose JavaScript does not, which is exactly where a string-literal
reader has no second chance.

The live-tree controls, the three allowance directions and the branch
boundary are in `test_helper_reimplementation.py`, beside the rule; this
file is the fabricated tree that says which forms the reader admits,
which it deliberately does not, and where the size floor sits.

ONE CONSTRAINT ON HOW THE FIXTURES ARE WRITTEN, and it is the control's
own reading of this file that forces it. Every fabricated document is
assembled from pieces by `_document`, and no string constant here is a
complete JavaScript program: the head carries the opening brace and the
body and the closing brace are separate pieces. A constant that held a
whole `function eventTarget(...) { ... }` would be, to the live scan
that reads this file as part of the tests tree, a re-implementation of
`_worker_sources`' own helper living in a file this branch edits — and a
row may not name such a file. Splitting the fixture is the same rule the
recogniser is built on: a program is an expression, not a bag of
strings, so a fixture has to be one too.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_helper_reimplementation import (  # noqa: E402
    JS_FLOOR, _text_in, js_declarations, js_reimplementations,
    reimplementations)

# A three-statement body, which measures five lines from brace to brace
# and so is above JS_FLOOR whatever the head around it says.
THREE_LINES = """\
  const seen = [];
  seen.push(listener);
  return seen;"""

# One head per admitted form. Each stops at its opening brace, which is
# what keeps every constant here short of a complete declaration.
HEADS = (
    'function eventTarget(listener) {',
    'async function eventTarget(listener) {',
    'const eventTarget = function (listener) {',
    'const eventTarget = function inner(listener) {',
    'let eventTarget = (listener) => {',
    'var eventTarget = async (listener) => {',
    'const eventTarget = listener => {',
)


def _mod(*lines):
    return ''.join(line + '\n' for line in lines)


def _document(head, body=THREE_LINES, close='}'):
    """A tests module carrying one fabricated JavaScript program.

    The pieces are concatenated, which is how a harness builds one, and
    the file holds them apart so no single string constant here is a
    complete declaration — see this module's docstring for why.
    """
    parts = '\n'.join(part for part in (head, body, close) if part)
    return f'_HARNESS = r"""\n{parts}\n"""\n'


CASES = [
    ('tests/_declaration.py', 'function', _document(HEADS[0])),
    ('tests/_async.py', 'async function', _document(HEADS[1])),
    ('tests/_function_expr.py', 'const = function', _document(HEADS[2])),
    ('tests/_named_expr.py', 'const = function name',
     _document(HEADS[3])),
    ('tests/_arrow.py', 'let = arrow', _document(HEADS[4])),
    ('tests/_async_arrow.py', 'var = async arrow', _document(HEADS[5])),
    ('tests/_bare_arrow.py', 'const = bare arrow', _document(HEADS[6])),
]


def _owner(body=THREE_LINES):
    return _document(HEADS[0], body)


def _reporter(body=THREE_LINES):
    return _document(HEADS[0], body)


def _found(sources, **kwargs):
    return {(item.path, item.name) for item in js_reimplementations(
        sources, **kwargs)}


def _fabricate(cases):
    """Compile every fabricated case, then read it.

    A fabricated case is a program a tests module could contain, so it
    has to compile: `ast.parse` accepts a string whose JavaScript does
    not, and that is the whole surface this reader works in.
    """
    sources = {}
    for path, _label, text in cases:
        compile(text, path, 'exec')
        sources[path] = text
    return sources


def test_every_admitted_declaration_form_is_recognised(tmp):
    del tmp
    _fabricate(CASES)
    for path, label, text in CASES:
        names = [item.name for item in
                 js_declarations({path: text}).get(path, [])]
        assert names == ['eventTarget'], (label, names)


def test_the_deliberately_unread_forms_declare_nothing(tmp):
    """Each of these is a bypass of this rule, named in `_js_functions`
    rather than left as a reader that silently stops.
    """
    del tmp
    unread = [
        # A method shorthand binds no name a call can reach.
        ('method shorthand', '''\
_HARNESS = r"""
const chrome = {
  onRemoved: {
    addListener(listener) {
      const seen = [];
      seen.push(listener);
      return seen;
    },
  },
};
"""'''),
        # A class method is the same shape one scope in.
        ('class method', '''\
_HARNESS = r"""
class FakeEvent {
  addListener(listener) {
    const seen = [];
    seen.push(listener);
    return seen;
  }
}
"""'''),
        # An anonymous function expression binds nothing.
        ('IIFE', '''\
_HARNESS = r"""
(function () {
  const seen = [];
  seen.push(1);
  return seen;
})();
"""'''),
        # A declaration nested inside another is not a top-level one.
        ('nested declaration', '''\
_HARNESS = r"""
function outer() {
  function eventTarget(listener) {
    const seen = [];
    seen.push(listener);
    return seen;
  }
  return eventTarget;
}
"""'''),
        # A `var` in a for head is a binding, not a declaration.
        ('var in a for head', '''\
_HARNESS = r"""
for (var eventTarget = 1; ;) {
  break;
}
"""'''),
        # A computed property reaches no name this reader enumerates.
        ('computed property', '''\
_HARNESS = r"""
const target = { ['event' + 'Target']() {
  return 1;
} };
"""'''),
    ]
    for label, text in unread:
        path = f'tests/{label}'
        compile(text, path, 'exec')
        names = [item.name for item in
                 js_declarations({path: text}).get(path, [])]
        assert 'eventTarget' not in names, (label, names)


def test_a_body_that_never_closes_is_refused_loudly(tmp):
    """The fail-closed half, and the difference from a fragment.

    A document that runs out inside a body is a fragment — a list of
    lines a later step joins — and is dropped. A body that opens with
    program still after it is the reader losing the plot, and raises
    naming the constant, because everything after it would be read at
    the wrong nesting.
    """
    del tmp
    fragment = _mod('_LINES = [', "    'function eventTarget(listener) {',",
                    ']')
    # Assembled, not written out: a constant holding an unclosed body
    # would fail the live scan that reads this file.
    opened = _document(HEADS[0],
                       '  const seen = [];\n  const after = 1;\n'
                       'const afterTwo = 2;', close='')
    compile(fragment, 'fragment', 'exec')
    compile(opened, 'opened', 'exec')
    assert js_declarations({'tests/fragment.py': fragment}) == {}
    try:
        js_declarations({'tests/opened.py': opened})
    except AssertionError as exc:
        assert 'eventTarget' in str(exc), exc
    else:
        raise AssertionError('the reader accepted a body it cannot close')


def test_the_owner_set_is_read_as_a_set(tmp):
    del tmp
    solo = _owner().replace('eventTarget', 'soloTarget')
    pair = _owner().replace('eventTarget', 'pairedTarget')
    sources = _fabricate([
        ('tests/_solo.py', 'sole owner', solo),
        ('tests/_pair.py', 'one of two', pair),
        ('tests/_second.py', 'one of two', pair),
        ('tests/test_paired.py', 'reporter', pair),
    ])
    found = _found(sources)
    # The sole owner of a name is its definition; two owners leave each
    # of them a re-implementation of the other, and neither is clean.
    assert found == {
        ('tests/_pair.py', 'pairedTarget'),
        ('tests/_second.py', 'pairedTarget'),
        ('tests/test_paired.py', 'pairedTarget'),
    }, sorted(found)
    per_owner = _found(
        sources, owner_is_the_definition=lambda path, name, owners:
        path in owners.get(name, ()))
    assert not [key for key in per_owner if key[0] == 'tests/_pair.py'], (
        sorted(per_owner))


def test_the_residue_table_is_not_what_detects(tmp):
    """The allowance table can hide a finding; it cannot produce one.

    `js_reimplementations` never reads it — the coverage controls are its
    only readers — so a site the rule finds is found whatever the table
    holds. Dropping the owner limb is the shape that proves it: a site
    the table cannot be holding, because the table is not consulted,
    appears.
    """
    del tmp
    sources = _fabricate([
        ('tests/_owner.py', 'owner', _owner()),
        ('tests/test_reader.py', 'reader', _reporter()),
    ])
    assert _found(sources) == {('tests/test_reader.py', 'eventTarget')}
    without_owner = _found(
        sources, owner_is_the_definition=lambda path, name, owners: False)
    assert ('tests/_owner.py', 'eventTarget') in without_owner, (
        without_owner)


def test_the_two_recognisers_do_not_read_each_other(tmp):
    """A same-named Python `def` is not a JavaScript declaration, and a
    JavaScript `function` is not a Python one. Each control is blind to
    the other language's half of the same file, which is the whole reason
    this rule exists.
    """
    del tmp
    python_only = _mod(
        'def eventTarget(listener):',
        '    """A Python binding of the same name."""',
        '    return [listener]')
    js_only = f'''\
_HARNESS = r"""
function eventTarget(listener) {{
{THREE_LINES}
}}
"""'''
    sources = {
        # The owner carries both languages' half of the name, so each
        # control has an owner to find and neither can borrow the other.
        'tests/_owner.py': _mod(
            'def eventTarget(listener):', '    return 1', '', 'HARNESS = r"""',
            'function eventTarget(listener) {', '  const seen = [];',
            '  return seen;', '}', '"""'),
        'tests/test_py.py': python_only,
        'tests/test_js.py': js_only,
    }
    for path, text in sources.items():
        compile(text, path, 'exec')
    js_found = _found(sources)
    assert js_found == {('tests/test_js.py', 'eventTarget')}, sorted(js_found)
    py_found = {(item.path, item.name)
                for item in reimplementations(sources)}
    assert py_found == {('tests/test_py.py', 'eventTarget')}, sorted(py_found)
    assert ('tests/test_js.py', 'eventTarget') not in py_found, py_found


def test_the_size_floor_holds_the_class_and_lets_the_one_liners_through(tmp):
    """A near miss on each side of the floor, and the floor is the
    recogniser's own, not `.pylintrc`'s.

    `body_lines` counts from the opening brace's line to the closing
    one's, so a one-statement body measures three and that is the
    class's shortest copy. `JS_FLOOR` is three for that reason, and the
    blocks it excludes are the two-line and one-line ones, which are
    wrappers no harness copies: at two the empty body appears, and at
    one so does the expression-bodied arrow. Neither changes the class.
    """
    del tmp
    sources = _fabricate([
        ('tests/_owner.py', 'owner', _owner()),
        ('tests/test_class.py', 'the class', _reporter()),
        # An empty body measures two: the opening brace's line and the
        # closing one. An expression-bodied arrow measures one.
        ('tests/test_two.py', 'a two-line block',
         _document(HEADS[0], body='')),
        ('tests/test_one.py', 'a one-line block',
         _mod('HARNESS = r"""', 'const eventTarget = () => 1;', '"""')),
    ])
    at_floor = _found(sources)
    assert at_floor == {
        ('tests/test_class.py', 'eventTarget'),
    }, sorted(at_floor)
    # The two-line block is one line short of the floor, and the floor is
    # the only thing keeping it out: at two it appears, and at one so
    # does the wrapper.
    at_two = _found(sources, minimum=2)
    assert ('tests/test_two.py', 'eventTarget') in at_two, sorted(at_two)
    assert ('tests/test_one.py', 'eventTarget') not in at_two, sorted(at_two)
    at_one = _found(sources, minimum=1)
    assert {'tests/test_two.py', 'tests/test_one.py'} <= {
        key[0] for key in at_one}, sorted(at_one)
    assert JS_FLOOR == 3, JS_FLOOR


def test_the_document_is_the_concatenation_not_the_module(tmp):
    """A harness's JavaScript is an expression, not a bag of strings.

    A body that opens in the first literal and closes in the last is one
    body, so the splice between them is a non-constant operand and has
    to be bracket-neutral; a docstring is prose and is not part of the
    program; and an f-string's doubled braces are the Python escape, not
    the JavaScript's.
    """
    del tmp
    split = _mod(
        '_HARNESS = (',
        '    r"""',
        HEADS[0],
        '"""',
        '    + something()',
        '    + r"""',
        THREE_LINES,
        '}',
        '""")')
    prose = _mod(
        '"""A module docstring with an unbalanced brace: {"""',
        'HARNESS = r"""',
        'function other(listener) {',
        THREE_LINES,
        '}',
        '"""')
    braces = _mod(
        'def _harness(stall):',
        '    return f"""',
        # Doubled for the f-string; the reader undoes that, so what the
        # program it becomes has is one brace.
        HEADS[0].replace('{', '{{'),
        '  if (stall) {{',
        '    return 0;',
        '  }}',
        '  return 1;',
        '}}',
        '"""')
    for path, text in (('tests/split.py', split), ('tests/prose.py', prose),
                       ('tests/braces.py', braces)):
        compile(text, path, 'exec')
    for path, text in (('tests/split.py', split), ('tests/braces.py', braces)):
        names = [item.name for item in js_declarations({path: text}).get(
            path, [])]
        assert names == ['eventTarget'], (path, names)
    # The docstring is not JavaScript, so its unbalanced brace does not
    # reach the reader and `other` is still read.
    assert [item.name for item in js_declarations(
        {'tests/prose.py': prose})['tests/prose.py']] == ['other']


def test_a_javascript_row_may_not_name_a_site_the_branch_added(tmp):
    """The boundary bites on a real repository, on both sides.

    A row naming a declaration the branch added is refused; the same
    table naming one the base already carried is not. The site is the
    unit, not the file, because a branch that fixes one defect in a file
    the residue already lives in is not adding a site — which is why
    this form and the Python table's file-scoped one are the same
    principle in the shape each can take.
    """
    repo = Path(tmp) / 'branch'
    repo.mkdir()
    for argv in (['git', 'init', '-q'],
                 ['git', 'config', 'user.email', 't@example.invalid'],
                 ['git', 'config', 'user.name', 'T']):
        subprocess.run(argv, cwd=repo, check=True,
                       env=_util.child_coverage('scrub'))
    (repo / 'tests').mkdir()
    kept = _mod('HARNESS = r"""', 'function kept(listener) {',
                '  const seen = [];', '  return seen;', '}', '"""')
    (repo / 'tests' / 'test_base.py').write_text(kept, encoding='utf-8')
    subprocess.run(['git', 'add', '-A'], cwd=repo, check=True,
                   env=_util.child_coverage('scrub'))
    subprocess.run(['git', 'commit', '-qm', 'base'], cwd=repo, check=True,
                   env=_util.child_coverage('scrub'))
    subprocess.run(['git', 'branch', 'main'], cwd=repo, check=True,
                   env=_util.child_coverage('scrub'))
    (repo / 'tests' / 'test_base.py').write_text(_mod(
        kept, 'HARNESS2 = r"""', 'function added(listener) {',
        '  const seen = [];', '  return seen;', '}', '"""'), encoding='utf-8')
    subprocess.run(['git', 'add', '-A'], cwd=repo, check=True,
                   env=_util.child_coverage('scrub'))
    subprocess.run(['git', 'commit', '-qm', 'branch'], cwd=repo, check=True,
                   env=_util.child_coverage('scrub'))

    run = _text_in(repo)
    merge_base = run(['git', 'merge-base', 'HEAD', 'main'])
    assert merge_base is not None, 'the built repository has no merge base'
    at_base = run(['git', 'show', f'{merge_base.strip()}:tests/test_base.py'])
    assert at_base is not None, 'the base tree could not be read'
    before = {(path, item.name)
              for path, items in js_declarations(
                  {'tests/test_base.py': at_base}).items()
              for item in items}
    assert before == {('tests/test_base.py', 'kept')}, sorted(before)
    table = {('tests/test_base.py', 'kept'): 'this one predates the branch',
             ('tests/test_base.py', 'added'): 'this one is the branch own'}
    introduced = {key for key in table if key not in before}
    assert introduced == {('tests/test_base.py', 'added')}, sorted(introduced)


def main():
    return _util.runner(_util.collect(globals()))


if __name__ == '__main__':
    raise SystemExit(main())
