"""Which binaries the suites under `tests/` may run without, and which they
insist on.

A suite that drives a real binary decides what a machine without it looks
like, and the two decisions are opposites: SKIP on absence and the binary
has to be installed in every job that runs a suite; assert it, or run it
without asking, and the suite fails loudly instead, so nothing about the
job's install goes unverified. The property is the decision, never the
spelling of the guard that makes it — `x is None`, `not x` and
`x != None` are three answers to one question, and a derivation that
reads two of them finds every suite written by the author of the third
invisible.

Split out of `tests/test_ci_lint_tools.py`, which owns the controls for
the installer's half of the lint-tool set, so the recognisers and the
workflow walk beside them can each be read on their own. The other half's
controls are in `tests/test_ci_tool_declarations.py` and read this module
too. Nothing here reads a workflow or a control name; everything
here answers one question about one tree.
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _receiver_resolution import _mentions  # noqa: E402
from _util import ROOT  # noqa: E402

# `_mentions` is defined once for this tree, in
# `tests/_receiver_resolution.py`, and imported rather than restated: its
# second argument is a set of names, and a guard names one, so every call
# here wraps the name in a set of one.

_SUBPROCESS = ('run', 'Popen', 'check_call', 'check_output')
_SKIP_NAMES = ('skip', 'skipTest', 'SkipTest')
# What a command that is not on PATH raises, so an `except` naming one of
# them is a suite talking about a binary's absence rather than about a
# file it happened to have open.
_ABSENT_COMMAND_ERRORS = ('FileNotFoundError', 'NotADirectoryError', 'OSError')


def _shutil_modules(tree):
    """The local names this module binds to the `shutil` module."""
    return _module_aliases(tree, 'shutil')


def _module_aliases(tree, module):
    """Every local name this module binds to `module`, its own name first.

    `import shutil as sh` and `import subprocess as sp` are the same nodes
    as the unaliased imports, so a derivation that reads the identifier
    literally sees a suite that names no tool at all — and then concludes
    the tool needs no install.
    """
    names = {module}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name for alias in node.names
                         if alias.name == module)
    return names


def _constants(scope):
    """The literal string bindings a scope introduces, at any depth.

    A tool named through a constant is the tool the suite requires, and the
    constant is as often bound inside the function that uses it as at
    module level, with an annotation as often as without. Reading only a
    module's top-level `NAME = 'literal'` made both of those spellings
    invisible, and a suite the derivation cannot read is a suite it
    concludes requires nothing.

    Two rounds, because a binding may name another binding: `TOOL = 'x'`
    then `ALIAS = TOOL` is as invisible as a name bound to nothing, and
    the second round reads the first round's answers rather than the
    source.
    """
    assignments = []
    for node in ast.walk(scope):
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if isinstance(target, ast.Name):
            assignments.append((target.id, value))
    bound = {}
    for _round in (0, 1):
        for name, value in assignments:
            if (isinstance(value, ast.Constant)
                    and isinstance(value.value, str)):
                bound[name] = value.value
            elif isinstance(value, ast.Name) and value.id in bound:
                bound[name] = bound[value.id]
    return bound


def _tool_name(node, bound, modules):
    """The tool a `shutil.which` call names, or None for any other call.

    A call that passes a keyword is scoped to a directory the caller chose,
    so it says what is on THAT path and nothing about the machine the suite
    runs on. A fixture that writes a bogus binary and then resolves it proves
    the resolver works; reading that as a requirement on the machine is how
    a control decides a tool needs no install because a test built its own.
    """
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'which'
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in modules):
        return None
    if node.keywords:
        return None
    argument = node.args[0] if node.args else None
    if isinstance(argument, ast.Name):
        return bound.get(argument.id)
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _dotted_or_bare_name(node):
    """The bare name a call or a raise names, or ''."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else ''


def _skips(body):
    """Whether the statements in an `if` body skip rather than fail.

    The SKIP is the distinguishing property, not the lookup: most of the
    tree's `shutil.which` uses are `assert node, '...'`, which state a
    requirement the jobs already meet. Reading a skip as an assertion — or
    the reverse — is what makes a control like this one either demand a
    package manager install git, or miss the next binary entirely.
    """
    for statement in body:
        for part in ast.walk(statement):
            if isinstance(part, ast.Call):
                if _dotted_or_bare_name(part) in _SKIP_NAMES:
                    return True
            elif isinstance(part, ast.Raise):
                name = _dotted_or_bare_name(part.exc)
                if name in _SKIP_NAMES or name.endswith('Skipped'):
                    return True
    return False


def _tolerates_absence(body):
    """Whether an `if` body ends the suite when its test does not hold.

    A guard that RETURNS is as much a decision to carry on without the
    tool as a guard that skips: the suite reports what it did without the
    binary, and a return reports it without saying so anywhere. Reading it
    as a requirement is how a control comes to believe a suite needs a
    binary it has just said it can do without.
    """
    return _skips(body) or any(isinstance(part, ast.Return)
                               for statement in body
                               for part in ast.walk(statement))


def _command_sites(tree, parent, bound_for, modules):
    """Every `(command word, enclosing try)` a subprocess call names.

    The command word is the tool; the `try` is what the suite does when
    the command cannot be started at all. The word is read through the
    scope's own constants as well as through a literal, because a suite
    that runs `subprocess.run([PARSER, ...])` names a binary as surely as
    one that runs `subprocess.run(['gojq', ...])`.
    """
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if _dotted_or_bare_name(node) not in _SUBPROCESS:
            continue
        if not _names_subprocess(node.func, modules):
            continue
        argv = node.args[0]
        if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
            continue
        first = argv.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            word = first.value
        elif isinstance(first, ast.Name):
            word = bound_for(node).get(first.id)
        else:
            continue
        if word:
            sites.append((word, _try_around(parent, node)))
    return sites


def _try_around(parent, node):
    """The nearest enclosing `try` that could answer a missing command."""
    current = node
    while current in parent:
        current = parent[current]
        if isinstance(current, ast.Try):
            return current
    return None


def _block_answers_a_missing_command(block):
    """Whether a `try` ends in a skip when the command is not there.

    `try: subprocess.run(['zizmor', ...]) except FileNotFoundError: skip`
    is the other ordinary way a suite says a binary may be missing, and it
    never calls `shutil.which` at all — so a derivation that reads only
    `which` sites did not merely miss the skip, it filed the tool as one the
    tree has decided may not be absent. The question is whether absence
    ends in a skip, never which idiom noticed it.

    A SKIP and not a return, unlike the lookup channel: a helper that
    catches OSError to hand its caller a failure string has not decided
    the suite may run without the binary, it has decided to report that it
    could not, and reading that as a skip puts `taskkill` in the required
    set because Windows-only cleanup returns a message instead of raising.
    """
    if block is None:
        return False
    return any(_catches_a_missing_command(handler.type)
               and _skips(handler.body)
               for handler in block.handlers)


def _catches_a_missing_command(handler):
    """Whether an `except` clause names the error a missing binary raises."""
    if handler is None:
        return False
    clauses = handler.elts if isinstance(handler, ast.Tuple) else [handler]
    return any(_dotted_or_bare_name(part) in _ABSENT_COMMAND_ERRORS
               for clause in clauses for part in ast.walk(clause))


def _names_subprocess(func, modules):
    """Whether a call goes through the subprocess module, aliased or not.

    The alias set is the module's own import bindings rather than a list of
    spellings: the docstring used to claim "aliased or not" while reading
    the identifier `subprocess` and one hardcoded abbreviation, so
    `import subprocess as sp` followed by `sp.run(...)` was a subprocess
    call the derivation did not see.
    """
    if isinstance(func, ast.Attribute):
        return (isinstance(func.value, ast.Name)
                and func.value.id in modules)
    return isinstance(func, ast.Name) and func.id in modules


def _lookup_target_names(statement, lookup):
    """The names a lookup's result lands in, whatever statement binds it.

    Four ordinary shapes bind it, and a walk that reads one is blind to
    the other three:

        found = shutil.which(t)
        found: str = shutil.which(t)
        found, _rest = shutil.which(t), None
        first = found = shutil.which(t)

    The second is an `ast.AnnAssign`, which is not a subclass of
    `ast.Assign` and carries `.target` rather than `.targets` — so the arm
    that read `targets[0]` never fired on it, and a tool bound that way
    landed in NEITHER set. That is the worst of these failures: not a tool
    in the wrong set, but a tool the derivation concluded the tree states
    no requirement about at all. An annotation is a plausible habit in a
    repository that runs two pyright configurations, and `_constants`
    already read them, so the evidence a reader would use to rule this
    hole out was on screen and pointed the wrong way.

    The fourth is a chained assignment, where ONE value reaches every
    target: `first = found = which(t)` puts the same tool in both names, so
    a guard on either is a guard on the tool, and declining the statement
    for having two targets files the tool in neither set — the same failure
    as the annotation, reached from the other direction.

    A tuple target binds its ELEMENTS from the matching elements of a
    tuple value, position by position: `a, b = which(t), which(u)` must
    not file `u`'s guard under `a`, and `_first, found = None, which(t)`
    must not file `t`'s guard under `_first`. A target tuple against a
    non-tuple value binds nothing, because nothing unpacks — which is
    also why `(a, b) = c = which(t)` binds nothing, and it binds nothing
    on purpose: that spelling is a `ValueError` at runtime, since a path
    string is not a two-element sequence. The shape beside it that CAN
    run, `a = b, c = which(t), None`, has a tuple target in a
    comma-separated list, and is the case the third paragraph is about.
    """
    if isinstance(statement, ast.AnnAssign):
        target, value = statement.target, statement.value
    elif isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target, value = statement.targets[0], statement.value
    elif isinstance(statement, ast.Assign):
        # A comma-separated target list, where every target takes part of
        # one value: `a = b, c = which(t), None` gives `a` the whole tuple
        # and unpacks it into `b` and `c`. So each target is resolved
        # against the value on its own terms, and the tool reaches `b`.
        # Reading only the Name targets would file it under `a`, which
        # holds the tuple and not the tool.
        return tuple(
            name for item in statement.targets
            for name in _one_target(item, statement.value, lookup))
    else:
        return ()
    return _one_target(target, value, lookup)


def _one_target(target, value, lookup):
    """The names one assignment target takes the lookup's value into."""
    if isinstance(target, ast.Name):
        return (target.id,)
    if (isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple)
            and len(target.elts) == len(value.elts)):
        # Node identity, not a name test: the question is whether THIS
        # lookup is inside that value, and `lookup` is the call node rather
        # than a name any expression could carry.
        return tuple(name.id for name, item in zip(target.elts, value.elts)
                     if isinstance(name, ast.Name)
                     and lookup in ast.walk(item))
    return ()


def _role_of_lookup(parent, node, tool, skipped, present):
    """Record how the suite that wrote this `which` call treats absence."""
    current = node
    while current in parent:
        current = parent[current]
        if isinstance(current, ast.If) and node in ast.walk(current.test):
            (skipped if _tolerates_absence(current.body)
             else present).add(tool)
            return
        if isinstance(current, ast.Assert) and node in ast.walk(current.test):
            present.add(tool)
            return
        if isinstance(current, ast.stmt):
            scope = _scope(parent, current)
            for bound in _lookup_target_names(current, node):
                _role_of_binding(scope, bound, tool, skipped, present)
            return


def _scope(parent, node):
    """The function or module the statement sits in, and no wider.

    A helper that reuses one name for several lookups is common, and a
    guard in another function is that other lookup's guard. Reading the
    whole module instead attributes one tool's skip to another tool, which
    is how a control ends up believing a binary nothing has to install.
    """
    current = node
    while current in parent:
        current = parent[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        if isinstance(current, ast.Module):
            return current
    return None


def _role_of_binding(scope, name, tool, skipped, present):
    """Record how the suite guards the tool bound to `name` in that scope.

    The question being asked is whether the thing that was looked up is
    missing, not which spelling of that question its author chose: `x is
    None`, `x == None`, `not x`, `x != None` and `x in (None,)` are five
    answers to one question, and a derivation that reads three of them
    finds every suite written by the author of the other two invisible.
    An invisible suite is a tool nothing has to install. A bare truthiness
    test whose body skips therefore counts too, which is wrong for a body
    that skips only when the tool is PRESENT — no suite is written that
    way, and the error runs toward a red build rather than a green one.

    The mention test comes first in both halves on purpose: the body of
    every `if` in a scope is walked to decide what it does, and a module
    holds hundreds of `if`s for the handful that guard a lookup. Testing
    the name first turns a walk per lookup into a walk per conditional.
    """
    if scope is None:
        return
    if any(_mentions(part.test, frozenset((name,)))
           and _tolerates_absence(part.body)
           for part in ast.walk(scope) if isinstance(part, ast.If)):
        skipped.add(tool)
    if any(_mentions(part.test, frozenset((name,))) for part in ast.walk(scope)
           if isinstance(part, ast.Assert)):
        present.add(tool)


def _skip_texts(tree):
    """Every literal a skip call or a skip raise renders.

    CORROBORATION, and only ever additive: a skip that names the tool it
    is skipping on is a skip on that tool, whatever plumbing carries the
    lookup to the skip, and the common shape where the lookup goes into a
    dict and the guard reads it out in another function reaches the skip
    this way and nowhere else.

    It is not what makes the control see a guarded `which`, and nothing
    here depends on a human having named the binary in the message. While
    it was the only thing standing between an ordinary guard and
    invisibility, the control's sensitivity to a whole class of real suites
    was decided by the wording of a skip message.
    """
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted_or_bare_name(node)
        elif isinstance(node, ast.Raise):
            name = _dotted_or_bare_name(node.exc)
        else:
            continue
        if name in _SKIP_NAMES or name.endswith('Skipped'):
            texts.extend(part.value for part in ast.walk(node)
                         if isinstance(part, ast.Constant)
                         and isinstance(part.value, str))
    return texts


_ROLES = []


def _tool_roles():
    """`(skipped, present)` tool names, read off the suites' own source.

    Read once per process: the answer is a property of the tree, and three
    controls asking the same question should not parse 340 modules six times
    between them.
    """
    if not _ROLES:
        _ROLES.append(_derive_tool_roles())
    return _ROLES[0]


def _derive_tool_roles(sources=None):
    """The two tool sets, one pass to enumerate and one to classify.

    A suite that SKIPS on a missing tool has decided the machine may lack it,
    and that tool is the required set: a job that has not installed one
    reports green having checked nothing. One that ASSERTS a tool, or RUNS
    it without asking, has decided the machine may not lack it — the suite
    fails loudly instead, so nothing about the job's install goes
    unverified. A tool in neither is not a requirement the tree states at
    all.

    `present` is NOT an exemption from the required set. It was one, on the
    argument that a tool in both is covered by a control that fails rather
    than skips — an argument this derivation could not support, because it
    cannot tell an assert the tree always reaches from one it never does.
    What genuinely needs no install is named in the control's
    SHIPPED_BY_THE_IMAGE, one entry and one reason each. `present` is read
    so the control can say which binaries the tree already insists on, and
    so neither channel can go empty without the non-vacuity assertion
    noticing.

    `sources` is a seam for the controls that drive this derivation over a
    synthetic module: what the recognisers read is a question about the
    recognisers, and asking it by planting a file in `tests/` would make
    every spelling a suite might use a tracked change.

    Two passes, and the second re-parses rather than holding every module's
    tree at once: a skip can only be matched against the tools the tree
    names, and 340 trees is more to hold than a 4 GB cap should be asked
    for.
    """
    if sources is None:
        sources = sorted((ROOT / 'tests').rglob('*.py'))
    candidates = set()
    for source in sources:
        tree = _read_tree(source)
        modules = _shutil_modules(tree)
        bound = _constants(tree)
        candidates |= {name for name in
                       (_tool_name(node, bound, modules)
                        for node in ast.walk(tree)) if name}
    skipped, present = set(), set()
    for source in sources:
        tree = _read_tree(source)
        parent = _parents_of(tree)
        modules = _shutil_modules(tree)
        launchers = _module_aliases(tree, 'subprocess')
        # One walk for every scope in the module, rather than one walk per
        # lookup: a module-level `which` resolves against the whole module,
        # and re-walking it for each call is what makes a derivation over
        # 340 modules a derivation nobody waits for.
        scopes = {id(node): _constants(node) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef,
                                       ast.AsyncFunctionDef))}
        # A function sees the module's bindings too, and its own shadow
        # them: `def probe(): local = TOOL` names the module's TOOL, and a
        # lookup that resolved only the function's own bindings would see
        # a name bound to nothing.
        module_level = scopes.get(id(tree), {})
        scopes = {key: {**module_level, **value}
                  for key, value in scopes.items()}

        def bound_for(node, _parent=parent, _scopes=scopes, _tree=tree):
            return _scopes.get(id(_scope(_parent, node) or _tree), {})

        commands = _command_sites(tree, parent, bound_for, launchers)
        present |= {word for word, _block in commands}
        skipped |= {word for word, block in commands
                    if _block_answers_a_missing_command(block)}
        for text in _skip_texts(tree):
            skipped |= {tool for tool in candidates
                        if re.search(rf'\b{re.escape(tool)}\b', text)}
        for node in ast.walk(tree):
            tool = _tool_name(node, bound_for(node), modules)
            if tool is not None:
                _role_of_lookup(parent, node, tool, skipped, present)
    return skipped, present


def _read_tree(source):
    """The module at `source` parsed, or its text parsed when handed one."""
    if isinstance(source, str):
        return ast.parse(source)
    return ast.parse(source.read_text(encoding='utf-8'))


def _parents_of(tree):
    """Every node's parent, so a walk can ask what a node sits inside."""
    return {child: node for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)}


# Ways a suite says a binary may be missing, one per answer to the same
# question. Every one of them SKIPS or RETURNS on absence, and every skip
# message names the parser rather than the binary, so none of them is
# visible to the channel that matches a tool named in a message: a
# derivation that reads these is reading the guard, not the wording.
#
# THIS IS A LIST OF WITNESSES, NOT AN ENUMERATION, and the control that
# reads it says so where a reader meets it. The name of the test that
# drives it says "every spelling", and that is a claim this table cannot
# make: adding a fifteenth shape turns it red, which is the point — a
# recogniser that cannot see a shape nobody thought of fails nothing —
# but it also means the table's real job is to hold one witness per way
# the recogniser's STRUCTURE can fail, so that a structural change which
# drops an arm is red rather than silent. The shapes still open, recorded
# so the next round does not re-derive them, and kept in step with what
# `_lookup_target_names` below says it cannot read:
#
# - a `which` over a loop variable, where the tool is never a CANDIDATE at
#   all, so no guard-shape fix reaches it;
# - `os.popen`;
# - a string argv under `shell=True`, where the whole command is one
#   string rather than an argv element;
# - a locally named skip helper whose name is neither `skip*` nor
#   `*Skipped`, which is a class bound — the recogniser matches call
#   NAMES, not call intent — rather than one defect;
# - a multi-target assignment, `a = b = which(t)` and the comma-separated
#   `a = b, c = which(t), None` beside it, which are now READ and are rows
#   in the table below rather than open shapes. They were open until this
#   round, and the previous list did not say so, which is the failure this
#   paragraph exists to stop repeating: a disclosure a reader consults has
#   to name what the recogniser actually declines.
#
# And one shape that is named because it CANNOT RUN, so handling it would
# be handling a program nobody can execute: `(a, b) = c = which(t)` unpacks
# a path string into two names, which is a ValueError. It binds nothing,
# and `_lookup_target_names` says so in the same words rather than leaving
# a reader to assume the silence was an oversight.
GUARDED_ON = {
    'inline identity':
        'if shutil.which(TOOL) is None:\n    _util.skip("no parser")',
    'inline truthiness':
        'if not shutil.which(TOOL):\n    _util.skip("no parser")',
    'inline not-identity':
        'if shutil.which(TOOL) is not None:\n    return',
    'bound identity':
        'found = shutil.which(TOOL)\nif found is None:\n'
        '    _util.skip("no parser")',
    'bound truthiness':
        'found = shutil.which(TOOL)\nif not found:\n'
        '    _util.skip("no parser")',
    'bound equality':
        'found = shutil.which(TOOL)\nif found == None:\n'
        '    _util.skip("no parser")',
    'bound inequality':
        'found = shutil.which(TOOL)\nif found != None:\n'
        '    _util.skip("no parser")',
    'bound membership':
        'found = shutil.which(TOOL)\nif found in (None,):\n'
        '    _util.skip("no parser")',
    'bound conjunction':
        'found = shutil.which(TOOL)\nif found is None or not extra:\n'
        '    _util.skip("no parser")',
    'aliased import':
        'found = sh.which(TOOL)\nif not found:\n'
        '    _util.skip("no parser")',
    'annotated constant':
        'found = shutil.which(NAMED)\nif not found:\n'
        '    _util.skip("no parser")',
    'constant bound in a function':
        'def probe():\n    local = TOOL\n    found = shutil.which(local)\n'
        '    if not found:\n        _util.skip("no parser")\n',
    'annotated binding of the result':
        'found: str = shutil.which(TOOL)\nif not found:\n'
        '    _util.skip("no parser")',
    'tuple-unpacked binding of the result':
        'found, _rest = shutil.which(TOOL), None\nif not found:\n'
        '    _util.skip("no parser")',
    'second slot of a tuple-unpacked binding':
        'def probe():\n    _first, found = None, shutil.which(TOOL)\n'
        '    if not found:\n        _util.skip("no parser")\n',
    'second name of a chained assignment':
        'first = found = shutil.which(TOOL)\nif not found:\n'
        '    _util.skip("no parser")',
    'first name of a chained assignment':
        'first = found = shutil.which(TOOL)\nif not first:\n'
        '    _util.skip("no parser")',
    'tuple target in a comma-separated target list':
        'whole = first, rest = shutil.which(TOOL), None\n'
        'if not first:\n    _util.skip("no parser")',
    'name target in a comma-separated target list':
        'whole = first, rest = shutil.which(TOOL), None\n'
        'if not whole:\n    _util.skip("no parser")',
    'command that cannot be started':
        'try:\n    subprocess.run([TOOL, "--version"], check=True)\n'
        'except FileNotFoundError:\n    _util.skip("no parser")',
    'aliased command that cannot be started':
        'try:\n    sp.run([TOOL, "--version"], check=True)\n'
        'except OSError:\n    _util.skip("no parser")',
}
_PREAMBLE = ('import shutil\nimport subprocess\nimport shutil as sh\n'
             'import subprocess as sp\nTOOL = "gojq"\nNAMED: str = "gojq"\n')


REQUIRED_ON = {
    'inline assert':
        'assert shutil.which(TOOL), "the parser runs the fixture"',
    'bound assert':
        'found = shutil.which(TOOL)\n'
        'assert found, "the parser runs the fixture"',
    'a command run without asking':
        'subprocess.run([TOOL, "--version"], check=True)',
}
# A tool the tree both insists on and tolerates the absence of. Nothing in
# the tree is written this way today; two independent plants were, and both
# read green, which is what these cases are here to refuse.
BOTH_ON = {
    'asserted elsewhere, skipped here':
        'def required():\n    assert shutil.which(TOOL), "the parser is '
        'the fixture"\n\n\n'
        'def optional():\n    found = shutil.which(TOOL)\n'
        '    if not found:\n        _util.skip("no parser")\n',
    'run unguarded, skipped when it cannot start':
        'def unguarded():\n    subprocess.run([TOOL, "--version"], '
        'check=True)\n\n\n'
        'def optional():\n    try:\n'
        '        subprocess.run([TOOL, "--format", "json"], check=True)\n'
        '    except FileNotFoundError:\n        _util.skip("no parser")\n',
}
