"""Planted sources and the verdicts the coverage-binding controls expect.

Not a suite itself — run_tests.py only loads `test_*.py`.

The rows here are what four suites judge against: a snippet a real-module
plant writes into a copied tests module, and the diagnostic that snippet
must draw. They were split out of
tests/test_coverage_scope_bindings.py, tests/test_coverage_bindings.py and
tests/test_coverage_unfollowable_forms.py, each of which another suite
imported whole to reach them. The names keep their private spellings, so
a suite that called one calls the same name it always did.
"""
from _coverage_guard import _BINDING_MESSAGE, _synthetic_violations


_NL = '\n'


def _binding_violation(line):
    return f'tests/synthetic.py:{line}: {_BINDING_MESSAGE}'


def _assert_binding_pair(unsafe, line, explicit, explicit_line):
    assert _synthetic_violations(unsafe) == [
        _binding_violation(line)
    ]
    assert _synthetic_violations(explicit) == [
        f'tests/synthetic.py:{explicit_line}: subprocess.run cwd=tmp '
        'declares no env='
    ]


def _scope_cases():
    return (
        ('unshadowed', """dict(cwd='module')
def nested():
    return dict(cwd='nested')
""", ()),
        ('lambda body', "lambda dict: dict(cwd='lambda body')\n",
         (("dict(cwd='lambda body')", "'lambda body'"),)),
        ('class body', """dict(cwd='before class')
class Example:
    dict = factory
    value = dict(cwd='class body')
dict(cwd='after class')
""", (("dict(cwd='class body')", "'class body'"),)),
        ('function default', """def build(
        dict=dict(cwd='function default')):
    return dict
""", ()),
        ('comprehension', """dict(cwd='before comprehension')
[
    dict(cwd='inside comprehension')
    for dict in dict(cwd='comprehension iterable')
]
dict(cwd='after comprehension')
""", (("dict(cwd='inside comprehension')",
         "'inside comprehension'"),)),
        ('comprehension walrus', """[(dict := factory)
 for item in values]
dict(cwd='after walrus')
""", (("dict(cwd='after walrus')", "'after walrus'"),)),
        ('nested comprehension walrus', """def build():
    [[(dict := factory) for inner in items] for outer in groups]
    return dict(cwd='after nested walrus')
""", (("dict(cwd='after nested walrus')",
         "'after nested walrus'"),)),
        ('definition headers', """@decorate(dict(cwd='function decorator'))
def build(dict):
    return dict
@decorate(dict(cwd='class decorator'))
class Example(dict(cwd='class base')):
    dict = factory
lambda dict=dict(cwd='lambda default'): dict
""", ()),
    )


def _scope_violations(relative, source, expected):
    return [
        f'{relative}:{source[:source.index(marker)].count(_NL) + 1}: '
        f'unresolved callee dict cwd={cwd} declares no env='
        for marker, cwd in expected]


def _module_text(target):
    return target.read_bytes().decode('utf-8').replace('\r\n', '\n')


def _inserted_line(source, anchor, snippet, marker):
    mutated = source.replace(anchor, snippet + anchor, 1)
    return mutated, mutated[:mutated.index(marker)].count('\n') + 1


def _binding_snippets():
    return (
        (
            'call result',
            """def _binding_probe(tmp):
    launcher = nullcontext(subprocess).__enter__()
    os.chdir(tmp)
    launcher.run(['python3', 'child.py'])
""",
            'launcher = nullcontext',
            """def _binding_probe(tmp):
    result = subprocess.run(['python3', 'child.py'], cwd=tmp)
"""),
        (
            'defaults',
            """def _binding_probe(
        tmp, launcher=subprocess, *, other=subprocess):
    os.chdir(tmp)
    launcher.run(['python3', 'child.py'])
    other.run(['python3', 'child.py'])
""",
            'launcher=subprocess',
            """def _binding_probe(tmp, result=subprocess.run(
        ['python3', 'child.py'], cwd=tmp), *, other=subprocess.run(
        ['python3', 'child.py'], cwd=tmp)):
    return result, other
"""),
        (
            'match capture',
            """def _binding_probe(tmp):
    os.chdir(tmp)
    match subprocess:
        case launcher:
            launcher.run(['python3', 'child.py'])
""",
            'match subprocess:',
            """def _binding_probe(tmp):
    match subprocess.run(['python3', 'child.py'], cwd=tmp):
        case result:
            return result
"""),
        (
            'comprehension',
            """def _binding_probe(tmp):
    os.chdir(tmp)
    return [launcher.run(['python3', 'child.py'])
            for launcher in [subprocess]]
""",
            'for launcher in',
            """def _binding_probe(tmp):
    return [result for result in
            [subprocess.run(['python3', 'child.py'], cwd=tmp)]]
"""),
        (
            'container',
            """def _binding_probe(tmp):
    launchers = [subprocess]
    os.chdir(tmp)
    return [launcher.run(['python3', 'child.py'])
            for launcher in launchers]
""",
            'launchers = [subprocess]',
            """def _binding_probe(tmp):
    return [subprocess.run(['python3', 'child.py'], cwd=tmp)]
"""),
        (
            'callee chain',
            """def _binding_probe(tmp):
    os.chdir(tmp)
    for launcher in [nullcontext(subprocess).__enter__()]:
        launcher.run(['python3', 'child.py'])
""",
            'for launcher in',
            """def _binding_probe(tmp):
    for result in [nullcontext(subprocess.run(
            ['python3', 'child.py'], cwd=tmp)).__enter__()]:
        return result
"""),
    )


_IMPORT_LAUNCH = "dict(['python3', 'child.py'], cwd=tmp)"


def _unresolved_dict(line):
    return [f'tests/synthetic.py:{line}: unresolved callee dict '
            'cwd=tmp declares no env=']


def _rebound_owner(line, spelling='behaviour.ROOT'):
    return [f'tests/synthetic.py:{line}: subprocess.run '
            f'cwd={spelling} declares no env=']


def _unfollowable_snippets():
    """Rows the real-module plant drives, unsafe and explicit alike."""
    return (
        (
            'decorator list',
            """def _binding_probe(tmp):
    from functools import partial
    @partial(subprocess.run)
    def go():
        pass
""",
            '@partial(subprocess.run)',
            """def _binding_probe(tmp):
    result = subprocess.run(['python3', 'child.py'], cwd=tmp)
"""),
        (
            'call arguments',
            """def _binding_probe(tmp):
    import operator
    operator.call(subprocess.run, ['python3', 'child.py'])
""",
            'operator.call(subprocess.run,',
            """def _binding_probe(tmp):
    result = subprocess.run(['python3', 'child.py'], cwd=tmp)
"""),
    )
