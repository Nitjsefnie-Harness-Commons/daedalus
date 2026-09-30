"""The PLANT MODULES the call-site arm's controls are built from.

`tests/_launch_plants.py` holds the receiver plants; these are the
parameter plants, and they are here for the same reason with one more
reason beside it: the controls that read them grew past the suite's size
ceiling while the plants did not, and a plant table is data rather than
reasoning. Every row is a planted module read by
`tests/test_launch_deadline_reach.py`, which is where the reason each one
exists is written -- a plant with no reason is a shape nobody chose.

The shapes are held apart on purpose. `PREDICATES` is one row per
condition the arm is built from, and every entry is `RECORDER` with
exactly one line changed, so a mutation is attributable to one condition
without a diff. `SPELLINGS` is a call written two different ways, each
with its refusal beside its discharge so neither reading is untested.
`RENAMED` is the axis every other row shares -- the parameter's name and
the argument's are the SAME word everywhere else -- and therefore the one
no other row could falsify.
"""

# The arm's positive shape, and the shape every row below is ONE delta
# from. A test double is handed a recorder list, the deadline goes into
# it, and the module writes the list as a container literal at the only
# call site -- which is `tests/test_real_browser_harness.py:130-175` in
# miniature, down to the recorder arriving as a PARAMETER.
RECORDER = '''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = []
    return build(recorded)
'''

# One row per predicate the arm is built from. Each is `RECORDER` with
# exactly one line changed, so the delta is readable without a diff and a
# mutation is attributable to one condition.
PREDICATES = {
    'a-call-passing-a-launch': RECORDER.replace(
        '    return build(recorded)',
        "    return build(subprocess.Popen(['x']))").replace(
            'def build(recorded):', 'import subprocess\n\n\n'
            'def build(recorded):', 1),
    'a-container-the-caller-built-from-a-launch': '''import subprocess


def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = [subprocess.Popen(['x'])]
    return build(recorded)
''',
    'a-spread-positional': RECORDER.replace(
        '    return build(recorded)', '    return build(*[recorded])'),
    'a-spread-keyword': RECORDER.replace(
        '    return build(recorded)',
        "    return build(**{'recorded': recorded})"),
    'an-omitted-argument': '''def build(recorded=None):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    return build()
''',
    'a-name-the-caller-does-not-write-as-a-literal': '''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go(items):
    recorded = items
    return build(recorded)
''',
    'a-second-call-site-the-reader-cannot-see': '''import subprocess


def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = []
    return build(recorded)


def other():
    return build(subprocess.Popen(['x']))
''',
    'no-call-site-in-the-module': '''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run
''',
    'an-argument-named-other-than-the-parameter': '''def build(kid):
    def run(timeout=None):
        return kid.wait(timeout)

    return run


def go(deadline, child):
    kid = []
    captured = []
    return build(child)(timeout=deadline)
''',
    'a-star-before-a-later-slot': '''import subprocess


def build(lead, recorded, tail):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    spread = (None, subprocess.Popen(['sleep', '2']))
    return build(*spread, [])
''',
    'a-launch-called-inside-a-literal-container': '''import subprocess


def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    return build([subprocess.Popen(['true'])])
''',
    'a-launch-through-a-local-member-alias': '''import subprocess


def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    pop = subprocess.Popen
    return build([pop(['true'])])
''',
    'a-launch-through-a-module-member-alias': '''import subprocess

pop = subprocess.Popen


def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    return build([pop(['true'])])
''',
    'a-match-capture-rebinds-the-name': '''def build(kid):
    def run(timeout=None):
        return kid.wait(timeout)

    return run


def go(holder):
    kid = []
    match holder:
        case [kid]:
            pass
    return build(kid)(timeout=1)
''',
    'an-except-handler-rebinding-the-name': '''def build(kid):
    def run(timeout=None):
        return kid.wait(timeout)

    return run


def go(holder):
    kid = []
    try:
        pass
    except OSError as kid:
        pass
    return build(kid)(timeout=1)
''',
    'a-match-mapping-rest-rebinding-the-name': '''def build(kid):
    def run(timeout=None):
        return kid.wait(timeout)

    return run


def go(holder):
    kid = []
    match holder:
        case {'k': 1, **kid}:
            pass
    return build(kid)(timeout=1)
''',
    'an-import-rebinding-the-name': '''def build(kid):
    def run(timeout=None):
        return kid.wait(timeout)

    return run


def go():
    import kid
    kid = []
    return build(kid)(timeout=1)
''',
    'a-nested-def-rebinding-the-name': '''def build(kid):
    def run(timeout=None):
        return kid.wait(timeout)

    return run


def go():
    def kid():
        return []

    kid = []
    return build(kid)(timeout=1)
''',
    'a-from-import-rebinding-the-name': '''def build(kid):
    def run(timeout=None):
        return kid.wait(timeout)

    return run


def go():
    from json import kid
    kid = []
    return build(kid)(timeout=1)
''',
    'a-receiver-that-is-not-a-parameter': '''def build():
    def run(args, *, timeout):
        return kids.wait(timeout)

    return run
''',
    'a-call-site-that-is-itself-a-launch': '''import subprocess


def run(recorded):
    def inner(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return inner


def go():
    return subprocess.run([])
''',
    'a-call-site-written-at-module-scope': '''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    try:
        pass
    except ValueError as recorded:
        pass


recorded = []
RUN = build(recorded)
''',
    'a-nested-helper-that-shadows-the-name': '''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    def inner(recorded):
        recorded = []
        return build(recorded)

    return inner
''',
}

# The two call-site SPELLINGS the arm reads, each with the refusal beside
# its discharge so neither line is untested. Both are decided by the ARM
# rather than by `literal_bindings`: the writing that proves the `Name` is
# inside a function, and a function's own `ast.arg` is walked before it, so
# the module-wide table cannot prove the name and the arm has to.
SPELLINGS = {
    'keyword': ('''def build(*, recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = []
    return build(recorded=recorded)
''', '''def build(*, recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go(items):
    recorded = items
    return build(recorded=recorded)
'''),
    'positional-only': ('''def build(recorded, /):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = []
    return build(recorded)
''', '''def build(recorded, /):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go(items):
    recorded = items
    return build(recorded)
'''),
}

# The one axis every other row shares and therefore none of them can
# falsify: the parameter's identifier and the argument's are the SAME word
# in `RECORDER`, in every entry of `PREDICATES` and in both `SPELLINGS`, so
# a reader that looked the argument up by the parameter's name passed the
# whole table. Here they differ, and the caller's scope carries a local of
# BOTH names so the pair is a discriminator rather than a coincidence.
RENAMED = {
    'discharge': '''def build(kid):
    def run(timeout=None):
        return kid.wait(timeout)

    return run


def go(deadline):
    kid = []
    captured = []
    return build(captured)(timeout=deadline)
''',
    'refuse': PREDICATES['an-argument-named-other-than-the-parameter'],
}

# The `**` the slot-scoped star check deliberately reads past. A `**` fills
# NAMED parameters and moves no positional index, so this call's `recorded`
# is the one it wrote; the coarser "is this call spread at all" reading
# would refuse it. This is what makes the scoping a decision with a
# control rather than a sentence in a docstring.
DOUBLE_STAR = '''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = []
    extra = {}
    return build(recorded, **extra)
'''

# The false green, in the shape the arm would admit if it stopped asking
# what the caller actually passed: a real child in a list the caller
# built, handed to a double the caller fills.
#
# The receiver is a parameter of the OWNER and not of the judged `run`,
# and that is the whole of what makes this row the arm's. A receiver the
# judged function takes itself is in that function's own `shadowed` set,
# so the pre-existing gate refuses it whatever the arm answers and the
# control would be byte-identical with the arm removed -- which is exactly
# what it was, until a mutation table measured it.
LIST_HANDED = '''import subprocess


def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = [subprocess.Popen(['sleep', '2'])]
    return build(recorded)
'''

# The runtime leg of the same shape, and a REAL child: `drain` takes the
# list and joins the child in it with a one-second deadline, against a
# child that sleeps two. The margin is what makes BOUNDED an answer
# rather than a race, and the deadline is the parameter's own value rather
# than a literal so the leg exercises the route the row is about.
LIST_HANDED_RUNTIME = '''import subprocess


def spawn():
    return subprocess.Popen(["sleep", "2"])


def run_gate(timeout):
    def drain(kids, timeout=None):
        return kids[0].wait(timeout)

    kids = [spawn()]
    try:
        drain(kids, timeout=timeout)
    except subprocess.TimeoutExpired:
        kids[0].kill()
        kids[0].wait()
        return "BOUNDED"
    kids[0].kill()
    kids[0].wait()
    return "NOT BOUNDED"
'''
