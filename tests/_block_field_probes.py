"""The block-field control's probe machinery: derivation,
templates, field-drop patch.
"""
import ast
import contextlib
import warnings

_BLOCK_FIELD_NAMES = ('body', 'orelse', 'finalbody')


_P = "raise RuntimeError('x')\nprobe()"
_P4 = '    ' + _P.replace('\n', '\n    ')
_P8 = '        ' + _P.replace('\n', '\n        ')

# One source template per claimed pair, proof at the field's depth.
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
    single-expression `body` fields out.
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
    """One parsed tree whose `field` holds [raise, probe-call]."""
    source = _TEMPLATES[cls.__name__, field]
    try:
        parsed = ast.parse(source, mode='exec')
    except SyntaxError as error:
        raise AssertionError((cls.__name__, field)) from error
    if cls is ast.Interactive:
        # mode='single' parses one statement, hence the shell.
        root = ast.Interactive(body=parsed.body)
    else:
        root = parsed
    try:
        compile(root, '<probe>',
                'single' if cls is ast.Interactive else 'exec')
    except (SyntaxError, ValueError, TypeError) as error:
        raise AssertionError((cls.__name__, field)) from error
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
                       for item in getattr(node, field))]
    assert len(holders) == 1, (cls.__name__, field, len(holders))
    return root, call


@contextlib.contextmanager
def field_unread(cls, field):
    """The walk's read with exactly this field dropped,
    `ast.iter_fields` wrapped so `_blocks` still decides a block."""
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
