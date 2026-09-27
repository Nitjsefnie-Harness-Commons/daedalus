"""The two tables the `bool`-behind-a-binding class is disclosed by.

`CARRIERS` is what the generated sweep CROSSES: every way a module can carry
the value of a `bool` index, each putting its row on a different side of the
boundary. `UNDECIDED_CARRIERS` is the half the builder cannot emit, each with
the reason. A carrier in neither table is a member of the class the instrument
cannot see, which is how a bypass survives a sweep reporting nothing unpaid,
so `test_mcp_builtin_names.py` holds the two against each other — and the two
tables NAME their carriers in one vocabulary, so the check compares like with
like rather than two lists that cannot intersect.

They live here rather than beside the builder that walks them because that
module is within a dozen lines of its size ceiling, and because a class's
disclosure belongs with the class.
"""

# The name the module binds the builtin under: a store the module makes at its
# own level, and a `global` declaration one scope down doing the same in each
# of the ways CPython spells a binding. The lambda rather than `print` because
# it RETURNS and says nothing: `lambda v: None` makes the index a value no
# container is indexed by, so the call raises before it imports anything, and
# it writes to the oracle's own output pipe as `print` would not be permitted
# to. A `del` and an `except ... as` UNBIND the name, so the use after the
# call is a `NameError`, and an import binds it to a module that is not
# callable.
_REBIND = '\n{name} = lambda v: None\n'

# The `global` carriers are the bindings `symtable` does not report on the
# ROOT side: a name a nested scope binds under a `global` declaration leaves
# the root symbol imported and not assigned, so a rule that reads the root
# alone sees the import and never the store. The CALL is what makes each a
# runtime measurement rather than a declaration — `global b` on its own binds
# nothing, so a row that only declared it would measure a module still
# holding the builtin.
#
# Four spellings, four literals, joined by concatenation rather than by a
# substitution rule: a rule about the text of a binding is the same trick as
# the enumeration these carriers replaced.
_GLOBAL_HEAD = '\n\ndef _rebind():\n    global {name}\n    '
_GLOBAL_TAIL = '\n\n_rebind()\n'

_GLOBAL_STORES = (
    ('a name rebound under a global declaration',
     ' and rebound under a global', '{name} = lambda v: None'),
    ('a name deleted under a global declaration',
     ' and deleted under a global', 'del {name}'),
    ('a name caught by an except clause under a global declaration',
     ' and caught by an except under a global',
     'try:\n        1 / 0\n    except Exception as {name}:\n        pass'),
    ('a name imported under a global declaration',
     ' and imported under a global', 'import os as {name}'),
)

# (the carrier's name, the step suffix, the source before the binding, the
# source after it, and whether the name is REPLACED by the time it is read).
# Whether the module has RUN the binding by then is what the first pair says:
# a statement it may skip leaves the name unbound, so the alias is evidence of
# a builtin and not a fact of one. `g` is false in the oracle's own globals
# for exactly that — the condition is one the runtime does not take, and the
# guard cannot read its value either. A name REPLACED and a binding the module
# may never have made do not compose: a name that is never bound is not
# something a store can replace.
CARRIERS = (
    ('the name left alone', '', '', '', False),
    ('a name replaced at the module level', ' and replaced', '', _REBIND,
     True),
    ('a binding under a condition the module may not take',
     ' under a condition', 'if g:\n    ', '', False),
) + tuple((name, suffix, '',
          _GLOBAL_HEAD + statement + _GLOBAL_TAIL, True)
          for name, suffix, statement in _GLOBAL_STORES)

# What the builder cannot emit, and why. Every one is held by a hand case in
# `test_mcp_builtin_names.py`, which runs them.
UNDECIDED_CARRIERS = (
    ("a name bound by a comprehension's own target",
     'the callee is evaluated in a flat namespace, so the name the '
     'comprehension binds has nothing to bind it with'),
    ("a use in a scope the walk cannot line up with the resolver's",
     'the oracle builds no such scope for a use to be inside one'),
    ("a name a decorator's own scope shadows",
     'a decorator runs where the `def` it decorates is written and is handed '
     'the function itself, so the shadowing name IS that function and the row '
     'raises while the module is still being defined — before there is a '
     'value for the oracle to classify'),
)
