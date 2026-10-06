"""The block-field control's probe machinery, beside the walk it
reads: the derivation, one source template per claimed pair, and
the field-drop patch the teeth half reads through.
"""
import ast
import contextlib
import warnings

_BLOCK_FIELD_NAMES = ('body', 'orelse', 'finalbody')


_P = "raise RuntimeError('x')\nprobe()"
_P4 = '    ' + _P.replace('\n', '\n    ')
_P8 = '        ' + _P.replace('\n', '\n        ')

# One source template per claimed pair: the field under proof carries
# the proof, every other field the parser needs is spelled minimally.
_TEMPLATES = {
    ('Module', 'body'): _P,
    ('Interactive', 'body'): _P,
    ('FunctionDef', 'body'): 'def f():\n' + _P4 + '\n',
    ('AsyncFunctionDef', 'body'): 'async def f():\n' + _P4 + '\n',
    ('ClassDef', 'body'): 'class c:\n' + _P4 + '\n',
    ('If', 'body'): 'if t:\n' + _P4 + '\n',
    ('If', 'orelse'): 'if t:\n    pass\nelse:\n' + _P4 + '\n',
    ('For', 'body'): 'for t in x:\n' + _P4 + '\n',
    ('For', 'orelse'): 'for t in x:\n    pass\nelse:\n' + _P4 + '\n',
    ('AsyncFor', 'body'):
        'async def f():\n    async for t in x:\n' + _P8 + '\n',
    ('AsyncFor', 'orelse'):
        'async def f():\n    async for t in x:\n        pass\n    else:\n'
        + _P8 + '\n',
    ('While', 'body'): 'while t:\n' + _P4 + '\n',
    ('While', 'orelse'): 'while t:\n    pass\nelse:\n' + _P4 + '\n',
    ('Try', 'body'): 'try:\n' + _P4 + '\nexcept Exception:\n    pass\n',
    ('Try', 'orelse'):
        'try:\n    pass\nexcept Exception:\n    pass\nelse:\n'
        + _P4 + '\n',
    ('Try', 'finalbody'):
        'try:\n    pass\nexcept Exception:\n    pass\nfinally:\n'
        + _P4 + '\n',
    ('TryStar', 'body'):
        'try:\n' + _P4 + '\nexcept* Exception:\n    pass\n',
    ('TryStar', 'orelse'):
        'try:\n    pass\nexcept* Exception:\n    pass\nelse:\n'
        + _P4 + '\n',
    ('TryStar', 'finalbody'):
        'try:\n    pass\nexcept* Exception:\n    pass\nfinally:\n'
        + _P4 + '\n',
    ('With', 'body'): 'with x:\n' + _P4 + '\n',
    ('AsyncWith', 'body'):
        'async def f():\n    async with x:\n' + _P8 + '\n',
    ('match_case', 'body'): 'match t:\n    case _:\n' + _P8 + '\n',
    ('ExceptHandler', 'body'): 'try:\n    pass\nexcept:\n' + _P8 + '\n',
}


def claimed_block_fields():
    """Every (carrier, field) pair the grammar declares a statement
    list: the field is one of the three grammar names and a bare
    instance's value for it is a list, which keeps the
    single-expression `body` of Expression, Lambda and IfExp out.
    """
    claimed = []
    for cls in {value for value in vars(ast).values()
                if isinstance(value, type) and issubclass(value, ast.AST)}:
        for field in cls._fields:
            if field not in _BLOCK_FIELD_NAMES:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', DeprecationWarning)
                if isinstance(getattr(cls(), field, None), list):
                    claimed.append((cls, field))
    return sorted(claimed, key=lambda pair: (pair[0].__name__, pair[1]))


def block_probe(cls, field):
    """One parsed tree whose `field` holds [raise, probe-call]; the
    single probe call and its single holder are asserted, so a
    template that misplaced the proof fails loudly.
    """
    source = _TEMPLATES[cls.__name__, field]
    if cls is ast.Interactive:
        # mode='single' parses one statement; the shell carries the
        # parse-produced statements the REPL position would.
        root = ast.Interactive(body=ast.parse(source).body)
    else:
        root = ast.parse(source, mode='exec')
    calls = [node.value for node in ast.walk(root)
             if isinstance(node, ast.Expr)
             and isinstance(node.value, ast.Call)
             and isinstance(node.value.func, ast.Name)
             and node.value.func.id == 'probe']
    assert len(calls) == 1, (cls.__name__, field, len(calls))
    call = calls[0]
    holders = [node for node in ast.walk(root)
               if isinstance(node, cls)
               and any(getattr(item, 'value', None) is call
                       for item in getattr(node, field, ()))]
    assert len(holders) == 1, (cls.__name__, field, len(holders))
    return root, call


@contextlib.contextmanager
def field_unread(cls, field):
    """The walk's read with exactly this field dropped: a wrapper over
    `ast.iter_fields`, so `_blocks`'s own predicate still decides what
    a block is.
    """
    real = ast.iter_fields

    def without(node):
        for name, value in real(node):
            # pylint: disable-next=unidiomatic-typecheck
            if type(node) is cls and name == field:
                continue
            yield name, value

    ast.iter_fields = without
    try:
        yield
    finally:
        ast.iter_fields = real
