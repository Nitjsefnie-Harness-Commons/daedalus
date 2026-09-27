"""The two tables the `bool`-behind-a-binding class is disclosed by.

`CARRIERS` is what the generated sweep CROSSES: every way a module can carry
the value of a `bool` index, each putting its row on a different side of the
boundary. `UNDECIDED_CARRIERS` is the half the builder cannot emit, each with
the reason. A carrier in neither table is a member of the class the instrument
cannot see, which is how a bypass survives a sweep reporting nothing unpaid,
so `test_mcp_builtin_names.py` holds the two against each other.

They live here rather than beside the builder that walks them because that
module is within a dozen lines of its size ceiling, and because a class's
disclosure belongs with the class.
"""

# As (the step suffix, the source before the binding, the source after it, and
# whether the name is REPLACED by the time it is read). Whether the module has
# RUN the binding by then is what the first pair says: a statement it may skip
# leaves the name unbound, so the alias is evidence of a builtin and not a
# fact of one. `g` is false in the oracle's own globals for exactly that — the
# condition is one the runtime does not take, and the guard cannot read its
# value either. A name REPLACED and a binding the module may never have made
# do not compose: a name that is never bound is not something a store can
# replace.
#
# The store is a LAMBDA rather than `print` because it RETURNS and says
# nothing: `lambda v: None` makes the index a value no container is indexed
# by, so the call raises before it imports anything, and it writes to the
# oracle's own output pipe as `print` would not be permitted to.
_REBIND = '\n{name} = lambda v: None\n'

# The `global` carrier is the store `symtable` does not report: a name a
# nested scope rebinds under a `global` declaration leaves the root symbol
# imported and not assigned, so a rule that reads the symbol table alone sees
# the import and never the store. The CALL is what makes this a runtime
# measurement rather than a declaration — `global b` on its own rebinds
# nothing, so a row that only declared it would measure a module still
# holding the builtin.
_GLOBAL_REBIND = (
    '\n\ndef _rebind():\n    global {name}\n    {name} = lambda v: None\n'
    '\n_rebind()\n')

CARRIERS = (('', '', '', False),
            (' and replaced', '', _REBIND, True),
            (' under a condition', 'if g:\n    ', '', False),
            (' and rebound under a global', '', _GLOBAL_REBIND, True))

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
