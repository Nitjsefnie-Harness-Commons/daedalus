#!/usr/bin/env python3
"""A program handed to a code-evaluating builtin, and what the walk reads.

A constant string reaching `eval`/`exec`/`compile` is a PROGRAM, not a
lookup key: it belongs in the closure walk as something the walk can either
resolve or must refuse, never in the silence. A name is a code-evaluating
builtin only where no enclosing scope binds it, so the MCP tool named
`exec` is a different function and stays silent; a value reached through a
name the walk cannot follow is a declared limit. Each case below drives one
branch — the shadow test, the program fold, each direct reach form, and the
store that hides the builtin — so a mutation to any single branch turns the
matching case red.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402


def _write_tree(directory, files):
    for name, source in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')


def _assert_refusal(_tmp, source, site, phrase):
    """The scan refuses this composition source, naming the site and why."""
    _write_tree(Path(_tmp), {'composition.py': source})
    try:
        _mcp_import_closure.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError as raised:
        assert f'composition:{site}' in str(raised), raised
        assert phrase in str(raised), raised
    else:
        raise AssertionError('a spelled import was silently skipped')


def _assert_silent(_tmp, source):
    """The scan returns the composition alone: this shape is a declared
    limit or a shadowed builtin, not a refusal."""
    _write_tree(Path(_tmp), {'composition.py': source})
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


def _refused_with_code_eval(_tmp, source, label=''):
    """The scan refuses this composition because a code-evaluating builtin
    is hidden by a store. With no import-by-name operation and no registry
    in the source, that is the only refusal it can produce, so the refusal
    IS the evidence the code-eval axis reached this store form."""
    _write_tree(Path(_tmp), {'composition.py': source})
    try:
        _mcp_import_closure.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError as raised:
        assert 'code-evaluating' in str(raised), raised
    else:
        raise AssertionError(
            f'a code-evaluating builtin was silently hidden in {label}')


def test_a_program_naming_the_operation_refuses_the_scan(_tmp):
    """The issue's own spelling: a constant program that reaches the
    import-by-name operation. The program is read by the one folder, so it
    is refused for the same reason the inline attribute is."""
    _assert_refusal(_tmp, '''
def load(name):
    return eval('importlib.import_module')(name)
''', 3, 'code-evaluating')


def test_a_constant_program_naming_a_helper_is_refused(_tmp):
    """The walk cannot prove what a bare name evaluates to, so a constant
    program is refused whatever it names — the decision, pinned."""
    _assert_refusal(_tmp, '''
def load():
    return eval('some_helper')
''', 3, 'code-evaluating')


def test_each_code_evaluating_builtin_is_reached_by_one_mechanism(_tmp):
    """`eval`, `exec` and `compile` are one operation reached through one
    named set, not three parallel branches: a fourth member needs no new
    branch, and dropping any single name here turns its row red."""
    for name in ('eval', 'exec', 'compile'):
        _assert_refusal(_tmp, f'''
def load(name):
    return {name}('importlib')(name)
''', 3, 'code-evaluating')


def test_a_module_level_shadow_is_not_the_code_evaluating_builtin(_tmp):
    """`daedalus_mcp/server.py`'s `exec = eval_tools['exec']` is a tool, not
    the builtin. Drop the shadow and the same program becomes a refusal, so
    this row is what holds the shadow test rather than decoration."""
    _assert_silent(_tmp, '''
eval_tools = {'exec': print}
exec = eval_tools['exec']


def call():
    return exec('code')
''')


def test_a_from_builtins_import_reaches_the_builtin(_tmp):
    """`from builtins import eval` binds the builtin itself, not a shadow.

    The module-bound rule must not read a from-builtins import as a local;
    the unaliased and the aliased spelling both reach the builtin, the same
    from-import grammar the import operation is recognised through.
    """
    for source, site in (
            ('''
from builtins import eval


def load(name):
    return eval('importlib.import_module')(name)
''', 6),
            ('''
from builtins import eval as run


def load(name):
    return run('importlib.import_module')(name)
''', 6),
            ('''
from builtins import compile


def load():
    return compile('importlib.import_module', 'f', 'eval')
''', 6)):
        _assert_refusal(_tmp, source, site, 'code-evaluating')


def test_a_function_local_shadow_is_not_the_code_evaluating_builtin(_tmp):
    """A `def exec` shadows the builtin only inside its function; a
    parameter and a closure do the same. Each is pinned so a shadow test
    that recognised only the module store fails here."""
    for source in (
            '''
def call():
    def exec(code):
        return code
    return exec('code')
''',
            '''
def call(exec):
    return exec('code')
''',
            '''
def outer():
    exec = make()

    def inner():
        return exec('code')
    return inner
'''):
        _assert_silent(_tmp, source)


def test_a_folded_program_spelling_is_refused(_tmp):
    """The readability decision is the one constant-folder, so a
    concatenation of literals reaches the builtin exactly as the joined
    string does; without the fold this is an unreadable value instead."""
    _assert_refusal(_tmp, '''
def load(name):
    return eval('import_' + 'lib')(name)
''', 3, 'code-evaluating')


def test_a_runtime_assembled_program_is_a_declared_limit(_tmp):
    """A program the folder cannot read as a constant is the value-side
    limit the import name already carries: the walk follows no value it
    cannot fold, whether the folded thing would be a module name or a
    program."""
    for source in ('''
def load(name):
    return eval(name)
''', '''
def load(x):
    return eval(f'{x}')
''', '''
def load(x):
    return compile(''.join([x]))
'''):
        _assert_silent(_tmp, source)


def test_a_store_hiding_a_code_evaluating_builtin_refuses(_tmp):
    """`loader = eval` hands the builtin to a name the walk cannot follow,
    and that name is then handed a program. A reach-call whose RESULT is the
    builtin binds it to a name the same way, and shares this refusal."""
    _assert_refusal(_tmp, '''
def load():
    loader = eval
    return loader('importlib')
''', 3, 'code-evaluating')
    _assert_refusal(_tmp, '''
import builtins

x = getattr(builtins, 'eval')
''', 4, 'code-evaluating')


# Every store form the import-by-name operation axis already reaches, each
# hiding a code-evaluating builtin and nothing else. This is the pin that
# says the code-eval axis rides the SAME store grammar: a form missing here
# is a store the walk follows for the operation but not for the builtin.
EVERY_STORE_FORM = (
    ('assign', 'x = eval\n'),
    ('annassign', 'x: object = eval\n'),
    ('augassign', 'x = 0\nx += eval\n'),
    ('walrus', 'while (x := eval):\n    break\n'),
    ('for', 'for x in (eval,):\n    pass\n'),
    ('async-for',
     'async def f():\n    async for x in (eval,):\n        pass\n'),
    ('comprehension', '_ = [x for x in (eval,)]\n'),
    ('with', 'with eval as x:\n    pass\n'),
    ('async-with',
     'async def f():\n    async with eval as x:\n        pass\n'),
    ('attribute-store', 'class C:\n    def f(self):\n        self.x = eval\n'),
    ('tuple-unpack', '(x,) = (eval,)\n'),
    ('list-unpack', '[x] = [eval]\n'),
    ('starred-unpack', '(x, *rest) = (eval, 1)\n'),
    ('positional-default', 'def f(x=eval):\n    pass\n'),
    ('keyword-default', 'def f(*, x=eval):\n    pass\n'),
    ('lambda-default', 'f = lambda x=eval: x\n'),
)


def test_every_store_form_the_operation_reaches_also_reaches_the_builtin(_tmp):
    """The code-eval store axis is the SAME grammar as the operation axis.

    `x = eval` was refused while `def f(x=eval)` scanned silent, because the
    new check was bolted onto the assignment store instead of the shared
    `_hidden` decision the default path also routes through. Every form the
    operation store axis already reached is listed here; the code-eval axis
    must reach each one, so a store form nobody enumerates is a finding.
    """
    for label, source in EVERY_STORE_FORM:
        _refused_with_code_eval(_tmp, source, label)


def test_a_code_evaluating_builtin_nested_in_a_container_refuses(_tmp):
    """A builtin in a container is as hidden as a bare one.

    The operation store recogniser recurses into a value's children, so
    `{'m': importlib.import_module}` is refused; the code-eval recogniser
    did not, and `{'e': eval}` was silent. The store recogniser now reads a
    value the same recursive way on both axes.
    """
    for source in (
            "d = {'e': eval}\n",
            "d = [eval]\n",
            "d = (eval,)\n",
            "d = {eval}\n",
            "import builtins\n\n\nd = {'e': builtins.eval}\n",
            "d = {'e': eval, 'other': 1}\n"):
        _refused_with_code_eval(_tmp, source)


def test_a_field_less_f_string_program_is_refused(_tmp):
    """A field-less f-string is the constant it looks like.

    The program is readable without knowing any runtime value, so the
    folder must read it; a redundant `f` prefix is otherwise a one-character
    evasion of the pinned repro.
    """
    _assert_refusal(_tmp, '''
def load(name):
    return eval(f'importlib.import_module')(name)
''', 3, 'code-evaluating')


def test_a_store_of_the_operation_to_a_code_eval_name_is_refused_first(_tmp):
    """Binding the import-by-name operation to a code-evaluating name is a
    bypass whatever the name is, so the operation-hidden refusal takes
    priority over shadowing — the message names the operation, not the
    builtin."""
    _assert_refusal(_tmp, '''
import importlib


def load(name):
    exec = importlib.import_module
    return exec(name)
''', 6, 'import-by-name operation')


def test_a_builtins_attribute_reaches_the_builtin(_tmp):
    """`builtins.eval` is a direct reference to the same builtin, read
    through the one recogniser the import operation already uses."""
    _assert_refusal(_tmp, '''
import builtins


def load(name):
    return builtins.eval('importlib')(name)
''', 6, 'code-evaluating')


def test_a_getattr_lookup_reaches_the_builtin(_tmp):
    """A `getattr` off the builtins module hands the builtin out without
    naming it as an attribute; the walk reads the constant lookup and
    refuses an unreadable one rather than letting it through."""
    for source in (
            '''
import builtins


def load(name):
    return getattr(builtins, 'eval')('importlib')(name)
''',
            '''
import builtins


def load(attribute, name):
    return getattr(builtins, attribute)('importlib')(name)
'''):
        _assert_refusal(_tmp, source, 6, 'code-evaluating')


def test_a_store_of_a_code_eval_calls_result_is_the_declared_limit(_tmp):
    """`x = eval(var)` USES the builtin as the call's effective callee and
    stores the call's RESULT, which the declared call-result limit accepts.

    The recognition is a property over the value the store will hold, not a
    check on the immediate callee node: a builtin that becomes the EFFECTIVE
    callee through an expression is still used. So the conditional callee,
    the `or` callee, a comprehension subscripted back to the builtin, and a
    parenthesised callee all deliver nothing, exactly as the bare name does.
    `f(eval(var))` passes eval's RESULT on, which the same limit covers.
    """
    for source in ('''
def load(var):
    x = eval(var)
    return x
''', '''
def load(var):
    z = (eval)(var)
    return z
''', '''
def load(var):
    z2 = f(eval(var))
    return z2
''', 'v = (eval if c else print)(x)\n',
            'v = [eval for _ in [0]][0](x)\n',
            'v = (eval or print)(x)\n',
            'v = (0, eval)[0](x)\n',
            'v = (lambda: eval)()(x)\n'):
        _assert_silent(_tmp, source)


def test_a_code_eval_builtin_delivered_to_a_call_is_still_refused(_tmp):
    """A builtin in a DATA position of the callee expression is delivered.

    The counterpart of the callee case, pinned together with it so neither
    side can regress alone: a blanket stop-at-call would silence the
    delivery, and a walk of the callee expression would refuse the callee use.
    A builtin that is the lookup KEY, an argument (plain, starred, or handed
    to an inner call), or a parameter a lambda is called with is a delivery;
    a builtin that is the SELECTED callee is a use. The walk does not model a
    lambda as transparent — a lambda is a function the walk cannot follow, so
    a builtin passed to one is delivered, which is the safe direction.
    """
    for source in (
            'y = f(eval)\n',
            'y2 = f(code=eval)\n',
            'w = f(g(eval))\n',
            'v = f(eval)(var)\n',
            'v = tbl[eval](x)\n',
            'v = (g(eval))(x)\n',
            'v = f(*[eval])(x)\n',
            'v = (lambda e: e)(eval)(x)\n'):
        _refused_with_code_eval(_tmp, source, source.strip())


def test_a_constant_program_through_an_effective_callee_is_refused(_tmp):
    """A CONSTANT program reaches the builtin however the effective callee
    is spelled, so the call arm reads the program through the same resolution
    the store uses.

    Without this the store treats these callees as a use and the call arm
    reads no program, so a constant program slips through — a regression the
    store-use cases alone cannot see, because they carry no constant program.
    """
    for source in (
            "v = (eval if c else print)('importlib.import_module')\n",
            "v = (eval or print)('importlib.import_module')\n",
            "v = [eval for _ in [0]][0]('importlib.import_module')\n",
            "v = (lambda: eval)()('importlib.import_module')\n"):
        _assert_refusal(_tmp, source, 1, 'code-evaluating')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
