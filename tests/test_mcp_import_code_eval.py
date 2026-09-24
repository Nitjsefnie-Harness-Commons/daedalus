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
    """A declared limit or a shadowed builtin, not a refusal."""
    _write_tree(Path(_tmp), {'composition.py': source})
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


def _refused_with_code_eval(_tmp, source, label=''):
    """With no operation or registry in the source, the only refusal it can
    produce IS the evidence the code-eval axis reached this store form."""
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
    """The issue's own spelling, read by the one folder."""
    _assert_refusal(_tmp, '''
def load(name):
    return eval('importlib.import_module')(name)
''', 3, 'code-evaluating')


def test_a_constant_program_naming_a_helper_is_refused(_tmp):
    """A constant program is refused whatever it names — the decision."""
    _assert_refusal(_tmp, '''
def load():
    return eval('some_helper')
''', 3, 'code-evaluating')


def test_each_code_evaluating_builtin_is_reached_by_one_mechanism(_tmp):
    """One operation through one named set, not three branches; dropping
    any single name turns its row red."""
    for name in ('eval', 'exec', 'compile'):
        _assert_refusal(_tmp, f'''
def load(name):
    return {name}('importlib')(name)
''', 3, 'code-evaluating')


def test_a_module_level_shadow_is_not_the_code_evaluating_builtin(_tmp):
    """`server.py`'s `exec = eval_tools['exec']` is a tool, not the builtin;
    drop the shadow and the same program is refused."""
    _assert_silent(_tmp, '''
eval_tools = {'exec': print}
exec = eval_tools['exec']


def call():
    return exec('code')
''')


def test_a_from_builtins_import_reaches_the_builtin(_tmp):
    """`from builtins import eval` binds the builtin itself, not a shadow;
    the unaliased and aliased spellings both reach it."""
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
    parameter and a closure do the same, so a shadow test recognising only
    the module store fails here."""
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
    """The one constant-folder reads a concatenation of literals, so it
    reaches the builtin as the joined string does."""
    _assert_refusal(_tmp, '''
def load(name):
    return eval('import_' + 'lib')(name)
''', 3, 'code-evaluating')


def test_a_runtime_assembled_program_is_a_declared_limit(_tmp):
    """A program the folder cannot read as a constant is the value-side
    limit the import name already carries."""
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
    """`loader = eval` hands the builtin to a name the walk cannot follow;
    a reach-call whose RESULT is the builtin binds it the same way."""
    _assert_refusal(_tmp, '''
def load():
    loader = eval
    return loader('importlib')
''', 3, 'code-evaluating')
    _assert_refusal(_tmp, '''
import builtins

x = getattr(builtins, 'eval')
''', 4, 'code-evaluating')


# The store forms that flow through the shared `_hidden` decision, each
# hiding a code-evaluating builtin and nothing else. This is the pin that
# says the code-eval axis rides the SAME store grammar: a form missing here
# is a store the walk follows for the operation but not for the builtin.
# `except ... as` is deliberately NOT listed: it is read separately by
# `visit_ExceptHandler`, which arms only the operation axis and the rebind
# because an except-name binds the caught exception, not the registry or a
# code-evaluating builtin, so neither is reachable through it (F7/F8).
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
    `_hidden` decision the default path also routes through. Every form that
    flows through `_hidden` is listed here; the code-eval axis must reach
    each one, so a store form nobody enumerates is a finding. `except ... as`
    is excluded because it never reaches `_hidden` (see the note above).
    """
    for label, source in EVERY_STORE_FORM:
        _refused_with_code_eval(_tmp, source, label)


def test_the_fold_limit_facets_are_accepted(_tmp):
    """The "value the walk cannot fold" limit is a mechanism, and these are
    its facets: a program or name COMPUTED at runtime the folder cannot read
    back to a constant. The f-string, the subscript that selects it, the
    tuple/dict index and the starred argument are all one declared limit,
    pinned here so the limit's letter covers them without a folder rule."""
    for source in (
            "v = eval(f'{\"importlib.import_module\"}')\n",
            "v = eval(['importlib.import_module'][0])\n",
            "v = eval(('importlib.import_module',)[0])\n",
            "v = eval({'a': 'importlib.import_module'}['a'])\n",
            "v = eval(*['importlib.import_module'])\n",
            "v = eval(''.join(['importlib', '.import_module']))\n"):
        _assert_silent(_tmp, source)


def test_a_code_evaluating_builtin_nested_in_a_container_refuses(_tmp):
    """A builtin in a container is as hidden as a bare one; the store
    recogniser reads a value the same recursive way on both axes."""
    for source in (
            "d = {'e': eval}\n",
            "d = [eval]\n",
            "d = (eval,)\n",
            "d = {eval}\n",
            "import builtins\n\n\nd = {'e': builtins.eval}\n",
            "d = {'e': eval, 'other': 1}\n"):
        _refused_with_code_eval(_tmp, source)


def test_a_field_less_f_string_program_is_refused(_tmp):
    """A field-less f-string is the constant it looks like; the folder must
    read it, or a redundant `f` prefix evades the repro by one character."""
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
    """A `getattr` off builtins hands the builtin out; an unreadable one is
    refused rather than let through."""
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
    """A builtin that becomes the EFFECTIVE callee through any expression is
    still USED — the store holds the call's RESULT, which the declared
    call-result limit accepts — so the conditional, `or`, comprehension
    subscript and parenthesised callees all deliver nothing, as the bare
    name does."""
    for source in (
            '''
def load(var):
    x = eval(var)
    return x
''',
            '''
def load(var):
    z = (eval)(var)
    return z
''',
            '''
def load(var):
    z2 = f(eval(var))
    return z2
''',
            'v = (eval if c else print)(x)\n',
            'v = [eval for _ in [0]][0](x)\n',
            'v = (eval or print)(x)\n',
            'v = (0, eval)[0](x)\n',
            'v = (lambda: eval)()(x)\n',
            'v = [[eval]][0][0](x)\n',
            'v = (0, eval)[0](x)\n',
            "v = {'a': 0, 'b': eval}['a']('importlib.import_module')\n"):
        _assert_silent(_tmp, source)


def test_a_code_eval_builtin_delivered_to_a_call_is_still_refused(_tmp):
    """A builtin in a DATA position — a lookup key, a plain/starred/nested
    argument, or a parameter a lambda is called with — is delivered, while a
    SELECTED callee is a use. Pinned with the callee case so neither side
    regresses alone: a blanket stop-at-call silences the delivery, a walk of
    the callee expression refuses the use. A lambda is NOT modelled as
    transparent, so a builtin passed to one is delivered (the safe side).
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
    """A CONSTANT program reaches the builtin however the effective callee is
    spelled, so the call arm reads the program through the same value
    resolution the store uses. Without this the store treats each callee as a
    use and the call arm reads no program, so a constant program slips through
    — invisible to the store-use cases, which carry no constant program."""
    for source in (
            "v = (eval if c else print)('importlib.import_module')\n",
            "v = (eval or print)('importlib.import_module')\n",
            "v = [eval for _ in [0]][0]('importlib.import_module')\n",
            "v = (lambda: eval)()('importlib.import_module')\n",
            "v = [[eval]][0][0]('importlib.import_module')\n",
            "v = {'a': eval}['a']('importlib.import_module')\n",
            "v = [eval][i]('importlib.import_module')\n",
            "v = (0, eval)[1]('importlib.import_module')\n",
            "eval.__call__('importlib.import_module')('os')\n",
            "getattr(eval, '__call__')('importlib.import_module')('os')\n",
            "v = [eval, print][0:1][0]('importlib.import_module')\n",
            "v = [*[eval]][0]('importlib.import_module')\n",
            "v = (lambda *, k=1: eval)()\n",
            "v = compile(source='importlib.import_module', filename='f',\n"
            "          mode='eval')\n",
            "a = lambda eval: 1; b = lambda: "
            "eval('importlib.import_module')\n"):
        _assert_refusal(_tmp, source, 1, 'code-evaluating')
    _assert_refusal(_tmp,
                    "import builtins\nv = builtins.__dict__['eval']("
                    "'importlib.import_module')\n", 2, 'code-evaluating')


def test_a_lambda_is_a_reach_only_when_it_is_callable_with_no_arguments(_tmp):
    """The zero-argument lambda reach is a PROPERTY, and the two bounds must
    DISAGREE. A vararg, a kwarg and a defaulted parameter all accept a
    zero-argument call and return the builtin, so they reach the operation;
    a required positional or keyword-only parameter blocks the call and
    raises at runtime, so it is not a reach."""
    for source in (
            'v = (lambda *a: eval)()\n',
            'v = (lambda **k: eval)()\n',
            'v = (lambda a=1: eval)()\n'):
        _assert_refusal(_tmp, source, 1, 'code-evaluating')
    for source in (
            'v = (lambda a: eval)()\n',
            'v = (lambda *, a: eval)()\n',
            'v = (lambda *, k: eval)()\n'):
        _assert_silent(_tmp, source)


def test_a_code_evaluating_use_before_its_module_binding_is_refused(_tmp):
    """A module runs top to bottom, so a use that PRECEDES its own later
    module-level binding still reads the real builtin. A use AFTER the binding
    sees the bound name and stays accepted, which is what keeps a legitimate
    module-level `exec = <tool>` store clean."""
    _assert_refusal(_tmp, '''
import importlib
v = eval('importlib.import_module')('os')
def eval(code):
    return code
''', 3, 'code-evaluating')
    _assert_silent(_tmp, '''
import importlib
def eval(code):
    return code
v = eval('x')
''')


def test_the_readable_selection_arms_have_rows_that_disagree(_tmp):
    """A row per readable-selection limb, so deleting the negative-index
    bound, the bool-key guard, the `__call__` projection guard, or the
    keyword-only conjunct each turns one of these red."""
    # a readable int key resolves the element: [0] picks eval
    _assert_refusal(_tmp, 'v = [eval, 0][0]("importlib.import_module")\n',
                    1, 'code-evaluating')
    # a negative literal is a UnaryOp, an unreadable key, so the
    # fail-closed container answer refuses it
    _assert_refusal(_tmp, 'v = (0, eval)[-1]("importlib.import_module")\n',
                    1, 'code-evaluating')
    _assert_refusal(_tmp, 'v = (0, eval)[-5]("importlib.import_module")\n',
                    1, 'code-evaluating')
    # bool-key guard: [eval,0][True] is treated as unreadable, not as 1
    _assert_refusal(_tmp, 'v = [eval, 0][True]("importlib.import_module")\n',
                    1, 'code-evaluating')
    # a readable key that selects a non-builtin stays silent
    _assert_silent(_tmp, 'v = [eval, 0][1]("importlib.import_module")\n')
    # a getattr on eval that is NOT __call__ is not a reach: the bare call
    # is silent, so the __call__ projection guard is what is pinned
    _assert_silent(_tmp,
                   "getattr(eval, 'notcall')"
                   "('importlib.import_module')('os')\n")
    # a required keyword-only lambda parameter blocks the zero-arg call
    _assert_silent(_tmp, 'v = (lambda *, k: eval)()\n')


def test_the_unreadable_selection_fallback_has_a_negative_space(_tmp):
    """The fail-closed answer for an unreadable selection is pinned on both
    sides, so the fallback holds a delivery when the container holds a builtin
    and stays silent when it does not."""
    # container holds a builtin, unreadable index -> refused
    _assert_refusal(_tmp, "v = [eval][i]('importlib.import_module')\n",
                    1, 'code-evaluating')
    # container holds no builtin, unreadable index -> silent
    _assert_silent(_tmp, "v = [print][i]('importlib.import_module')\n")
    # container reached only through a further selection that holds one
    _assert_refusal(_tmp,
                    "v = [[eval]][0][i]('importlib.import_module')\n",
                    1, 'code-evaluating')
    _assert_silent(_tmp, "v = [[print]][0][i]('importlib.import_module')\n")


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
