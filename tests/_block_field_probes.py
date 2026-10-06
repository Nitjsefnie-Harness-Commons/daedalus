"""The block-field control's probe machinery, beside the walk it reads.

`test_mcp_import_refusals.py` holds every statement-list field the grammar
declares, both ways; this module carries the derivation and the probe
trees, so the suite stays under its own size ceiling.
"""
import ast
import contextlib
import warnings


def _arguments():
    return ast.arguments(posonlyargs=[], args=[], vararg=None,
                         kwonlyargs=[], kw_defaults=[], kwarg=None,
                         defaults=[])


def _handler():
    return [ast.ExceptHandler(type=ast.Name(id='Exception',
                                            ctx=ast.Load()),
                              name=None, body=[ast.Pass()])]


_BLOCK_FIELD_NAMES = ('body', 'orelse', 'finalbody')

# The fillers the compiler and the walk's own `_leaves` ask of the fields
# beside the proof: a non-proof statement list carries [Pass()] (the
# compiler refuses empty bodies, and `_leaves` reads an if-with-else's
# last body statement); a `try` needs a handler, `except*` a typed one.
_SCALAR_FILL = {
    'name': lambda: 'p',
    'args': _arguments,
    'returns': lambda: None,
    'test': lambda: ast.Constant(True),
    'target': lambda: ast.Name(id='t', ctx=ast.Store()),
    'iter': lambda: ast.Constant(0),
    'subject': lambda: ast.Constant(None),
    'pattern': lambda: ast.MatchAs(name=None, pattern=None),
    'guard': lambda: None,
    'type_comment': lambda: None,
    'type': lambda: ast.Name(id='Exception', ctx=ast.Load()),
}

_LIST_FILL = {
    (ast.Try, 'handlers'): _handler,
    (ast.TryStar, 'handlers'): _handler,
    (ast.With, 'items'): lambda: [ast.withitem(
        context_expr=ast.Constant(None), optional_vars=None)],
    (ast.AsyncWith, 'items'): lambda: [ast.withitem(
        context_expr=ast.Constant(None), optional_vars=None)],
}


def claimed_block_fields():
    """Every (carrier, field) pair the grammar declares a statement list.

    One class in the ast module at a time: a pair is claimed when the
    grammar names the field `body`, `orelse` or `finalbody` and a bare
    instance's value for it is a list -- which is what keeps the
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
    """One compilable tree whose `field` holds [raise, probe-call].

    Every other field is filled with what the compiler and the walk's own
    `_leaves` ask of it, and the wrappers put the carrier where a real
    module carries it: a handler in a `try`, a case in a `match`, an
    async loop in an async def. Returns the root and the probe call.
    """
    call = ast.Call(func=ast.Name(id='probe', ctx=ast.Load()), args=[],
                    keywords=[])
    root = probe_root(cls, fill(cls, field,
                                [ast.Raise(), ast.Expr(value=call)]))
    ast.fix_missing_locations(root)
    compile(root, '<probe>', 'single' if cls is ast.Interactive else 'exec')
    return root, call


def fill(cls, field, body):
    """One carrier whose `field` holds `body`, every other field filled."""
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        node = cls()
    for name in cls._fields:
        if name == field:
            setattr(node, name, body)
        elif name in _SCALAR_FILL:
            setattr(node, name, _SCALAR_FILL[name]())
        elif (cls, name) in _LIST_FILL:
            setattr(node, name, _LIST_FILL[(cls, name)]())
        elif name in _BLOCK_FIELD_NAMES:
            setattr(node, name, [ast.Pass()])
        else:
            setattr(node, name, [])
    return node


def probe_root(cls, node):
    """The root the carrier compiles as, in the wrapper a module carries."""
    if cls in (ast.Module, ast.Interactive):
        return node
    if cls is ast.ExceptHandler:
        node = ast.Try(body=[ast.Pass()], handlers=[node], orelse=[],
                       finalbody=[])
    elif cls is ast.match_case:
        node = ast.Match(subject=ast.Constant(None), cases=[node])
    if cls in (ast.AsyncFor, ast.AsyncWith):
        node = fill(ast.AsyncFunctionDef, 'body', [node])
    return ast.Module(body=[node], type_ignores=[])


@contextlib.contextmanager
def field_unread(cls, field):
    """The walk's read with exactly this field dropped from it.

    A wrapper over `ast.iter_fields`, so `_blocks`'s own predicate still
    decides what a block is and the mutant is that field alone dropped --
    not a reimplementation that could drift from the real predicate.
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
