"""Structured-sink rows that red-exercise the bound rule's fail-closed
branches, read the way the tree-wide rule reads them.

LAUNCH_REFUSAL_ROWS is a table of refusal *strings*, so it cannot pin
the unplaced and ambiguous paths: neither emits a refusal string, only a
structured (lineno, head, kind) the tree-wide caller consumes. Each row
here is (label, source, expected sites); the suite asserts the analyser's
own `bound_sites` output, so deleting the unplaced path or the ambiguity
mechanism makes the matching row fail.

A row that asserts `[]` is a ratchet: it records that a shape produces no
site today, and it goes red the day the shape is taught. That is a
statement about the current answer, not a claim that the shape is
covered — the shape is covered by being REPORTED, and a shape that is
reported at `unreadable` is in scope to the rule either way.
"""
BOUND_SITE_ROWS = (
    ('ambiguous-name',
     "import subprocess\n"
     "argv = ['node', 'x']\n"
     "argv = ['git', 'status']\n"
     "def probe():\n"
     "    subprocess.run(argv, check=True, timeout=30)\n",
     [(5, 'ambiguous', 'timeout')]),
    ('alias-import-unplaced',
     "import subprocess as sp\n"
     "def probe():\n"
     "    sp.run(['git', 'status'], check=True, timeout=30)\n",
     [(3, 'unreadable', 'unplaced')]),
    # Negative space of the widened receiver class: a key reading
    # 'subprocess' derives whatever the base, so a mapping that has
    # nothing to do with modules is read as one. Deliberate and
    # fail-closed; deleting the derives key path turns this row red.
    ('sys-modules-negative-space',
     "import sys\n"
     "def probe():\n"
     "    sys.modules['json'].dumps({}, **{'timeout': 30})\n",
     [(3, 'unreadable', 'unplaced')]),
    ('subscript-key-on-an-unrelated-base-derives-alike',
     "import os\n"
     "import subprocess\n"
     "os.environ['subprocess'].run(\n"
     "    ['git', 'status'], check=True, timeout=30)\n",
     [(3, 'git', 'timeout')]),
    # The unplaced arm is the whole of the second way a site comes in
    # scope, and each row here is a receiver the analyser cannot PROVE is
    # a fixed, non-launch value.
    ('unproved-parameter-receiver-is-reported',
     "import subprocess\n"
     "def probe(process):\n"
     "    return process.communicate(timeout=30)\n",
     [(3, 'unreadable', 'unplaced')]),
    ('unproved-attribute-receiver-is-reported',
     "import subprocess\n"
     "class Runner:\n"
     "    def go(self):\n"
     "        return self.proc.wait(timeout=60)\n",
     [(4, 'unreadable', 'unplaced')]),
    ('namespace-key-receiver-is-placed-as-a-launch',
     "import subprocess\n"
     "def probe(ns):\n"
     "    return ns['subprocess'].run(\n"
     "        ['git', 'status'], check=True, timeout=30)\n",
     [(3, 'git', 'timeout')]),
    # Issue #1100's own symptom in the shapes the resolver no longer
    # tries to read: an inherited attribute, a class body under a
    # condition, and an instance the class body never bound.
    ('inherited-attribute-receiver-is-reported',
     "import subprocess\n"
     "class Root:\n"
     "    mod = subprocess\n"
     "class Branch(Root):\n"
     "    pass\n"
     "class Leaf(Branch):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     [(8, 'unreadable', 'unplaced')]),
    ('conditional-class-body-attribute-is-reported',
     "import os\n"
     "import subprocess\n"
     "class Base:\n"
     "    if os.environ.get('DAEDALUS'):\n"
     "        mod = subprocess\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     [(8, 'unreadable', 'unplaced')]),
    ('instance-receiver-over-a-base-binding-is-reported',
     "import subprocess\n"
     "class Base:\n"
     "    mod = subprocess\n"
     "class Child(Base):\n"
     "    pass\n"
     "handle = Child()\n"
     "def go():\n"
     "    return handle.mod.run(\n"
     "        ['git', 'status'], check=True, timeout=30)\n",
     [(8, 'unreadable', 'unplaced')]),
    # A name bound to the import machinery is not a proved value: the
    # argument decides what it returns, and an argument the resolver
    # cannot read leaves the module itself unknown. This is the whole of
    # issue #1099's residual.
    ('machinery-call-binding-is-not-proved',
     "import importlib\n"
     "import subprocess\n"
     "name = 'subprocess'\n"
     "mod = importlib.import_module(f\"{name}\")\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'unreadable', 'unplaced')]),
    ('machinery-call-binding-with-a-readable-argument-is-read',
     "import importlib\n"
     "import subprocess\n"
     "name = 'subprocess'\n"
     "mod = importlib.import_module(name)\n"
     "mod.run(\n"
     "    ['git', 'status'], check=True, timeout=30)\n",
     [(5, 'git', 'timeout')]),
    # The negative of the arm: a name the module binds to a plain value
    # IS proved, and a bounded call through it is not reported.
    ('proved-name-receiver-is-not-reported',
     "import json\n"
     "import subprocess\n"
     "def probe():\n"
     "    payload = json.dumps\n"
     "    return payload(timeout=30)\n",
     []),
    ('self-referential-concat-name',
     "import importlib\n"
     "import subprocess\n"
     "a = a + a\n"
     "mod = importlib.import_module(a)\n"
     "subprocess.run(['git', 'status'], check=True)\n",
     []),
    # A namespace the module builds by exec is still reached by its key,
    # so the launch is placed and the exec call is refused separately.
    ('exec-namespace-receiver-is-placed',
     "import subprocess\n"
     "ns = {}\n"
     "exec('import subprocess', ns)\n"
     "ns['subprocess'].run(['git', 'status'], check=True, timeout=30)\n",
     [(4, 'git', 'timeout')]),
    # A name that derives without being bound is a module factory, a
    # callable rather than the module, and the placement arm keeps bare
    # names out for that reason: placing `get.run(...)` would read a
    # callable as a launch. The unplaced arm still reports it.
    ('module-factory-name-receiver-stays-unplaced',
     "import subprocess\n"
     "def get():\n"
     "    return subprocess\n"
     "get.run(['git', 'status'], check=True, timeout=30)\n",
     [(4, 'unreadable', 'unplaced')]),
    # A `+` whose operand is not a string is not a constant. The row is
    # here because dropping the string guard makes the fold raise
    # TypeError instead of answering.
    ('import-module-name-concat-with-a-non-string',
     "import importlib\n"
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n"
     "mod = importlib.import_module('sub' + 1)\n",
     [(3, 'git', 'timeout')]),
    # The two routes to the machinery that a spelling of `import ... as`
    # does not name, and the parameter a module-level import's spelling
    # shadows. All three are unproved, and a name the analyser cannot
    # read is what the reduction exists to report.
    ('machinery-reached-by-assignment-is-unproved',
     "import importlib\n"
     "import subprocess\n"
     "il = importlib\n"
     "mod = il.import_module('subprocess')\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'unreadable', 'unplaced')]),
    ('parameter-shadows-a-module-import-is-unproved',
     "import json\n"
     "import subprocess\n"
     "def go(json):\n"
     "    return json.run(\n"
     "        ['git', 'status'], check=True, timeout=30)\n",
     [(4, 'unreadable', 'unplaced')]),
    # A comprehension binds its loop names, so one reaching a bounded
    # call is a receiver the analyser cannot read.
    ('comprehension-target-receiver-is-reported',
     "import subprocess\n"
     "def go(things):\n"
     "    return [sp.run(\n"
     "        ['git', 'status'], check=True, timeout=30) for sp in things]\n",
     [(3, 'unreadable', 'unplaced')]),
    # A factory the analyser cannot name is a receiver it cannot read,
    # and a call on a name it CAN account for stays proved.
    ('factory-origin-cannot-be-named-is-unproved',
     "import subprocess\n"
     "def get():\n"
     "    return subprocess\n"
     "mod = get()\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'unreadable', 'unplaced')]),
    ('call-on-a-name-the-analyser-can-name-is-still-proved',
     "import subprocess\n"
     "def go():\n"
     "    proc = subprocess.Popen(['git', 'status'])\n"
     "    return proc.wait(timeout=30)\n",
     []),
    # `head_label`'s two container guarantees: a container is git when
    # ANY element's head is, and a non-git element never makes a git one
    # read as non-git.
    ('argv-container-is-git-when-any-element-is',
     "import subprocess\n"
     "for argv in (['node', 'x'], ['git', 'status']):\n"
     "    subprocess.run(argv, check=True, timeout=30)\n",
     [(3, 'git', 'timeout')]),
    ('argv-container-second-head-git-is-still-git',
     "import subprocess\n"
     "for argv in (['node', 'x'], ['git', 'status']):\n"
     "    subprocess.run(argv, check=True, timeout=30)\n",
     [(3, 'git', 'timeout')]),
    # The positional-only spelling of the parameter the shadowed import
    # row pins: a second spelling needs a row of its own.
    ('positional-only-parameter-shadows-a-module-import',
     "import json\n"
     "import subprocess\n"
     "def go(json, /):\n"
     "    return json.run(\n"
     "        ['git', 'status'], check=True, timeout=30)\n",
     [(4, 'unreadable', 'unplaced')]),
    # The rows below are the CONTROLS for the guard clauses this change
    # classified. The set is NAMED, not general: a claim about every
    # clause in the two analyser modules is open-ended, and each review
    # round falsified the last round's version of it one arm further out.
    # The named guards are the four limbs of proved_fixed's disjunction,
    # the machinery_route loop, the four `seen` guards in _argv_read.py,
    # and the launcher-factory arm of the bound fixpoint. Each is
    # CONTROLLED — a row here, or a control in test_repo_layout.py, that
    # fails when the clause is deleted — or not controlled, for one of
    # exactly two reasons written at the clause: REDUNDANT, where a named
    # bound guarantees it, or DEAD, where no input reaches it. There is
    # no third answer, deliberately: a category for "live and unpinned"
    # would admit the analyser's whole residue and make the rule a
    # licence. Those arms are named at their clauses and tracked at
    # #1144, which is where a reader who trusts this set looks next.
    ('subprocess-alias-spelled-then-rebound-is-unproved',
     "import os\n"
     "import subprocess as sp\n"
     "sp = os\n"
     "def f():\n"
     "    return sp(timeout=1)\n",
     [(5, 'unreadable', 'unplaced')]),
    ('subprocess-from-import-spelled-then-rebound-is-unproved',
     "import os\n"
     "from subprocess import run as r\n"
     "r = os\n"
     "def f():\n"
     "    return r(timeout=1)\n",
     [(5, 'unreadable', 'unplaced')]),
    ('annotated-subprocess-alias-is-unproved',
     "import subprocess as sp\n"
     "sp: object = None\n"
     "def f():\n"
     "    return sp(timeout=1)\n",
     [(4, 'unreadable', 'unplaced')]),
    # The only row for the `== 'subprocess'` limb, which no other row and
    # no other limb can stand in for. Why it needs its own is at the
    # disjunction in proved_fixed.
    ('bare-subprocess-receiver-is-unplaced',
     "import subprocess\nsubprocess(timeout=30)\n",
     [(2, 'unreadable', 'unplaced')]),
    # The bound behind the `bound` limb's redundancy, pinned from the other
    # side: a bare Name in `bound` is a PLACED launch, so it reports a
    # `timeout` row and never an `unplaced` one. This is what refuses a
    # refactor of the launch chain that would invert that.
    ('bound-name-called-bare-is-a-placed-launch',
     "import os\n"
     "import subprocess\n"
     "os = subprocess\n"
     "os(timeout=30)\n",
     [(4, 'unreadable', 'timeout')]),
    # Four spellings of the vararg collection in _parameters, which is
    # what each of them loses.
    ('vararg-receiver-shadowing-a-module-import-is-unproved',
     "import os\n"
     "import subprocess\n"
     "def f(*os):\n"
     "    subprocess.run(['git', 'status'], check=True)\n"
     "    return os(timeout=1)\n",
     [(5, 'unreadable', 'unplaced')]),
    ('kwarg-receiver-shadowing-a-module-import-is-unproved',
     "import os\n"
     "import subprocess\n"
     "def f(**os):\n"
     "    subprocess.run(['git', 'status'], check=True)\n"
     "    return os(timeout=1)\n",
     [(5, 'unreadable', 'unplaced')]),
    ('vararg-receiver-shadowing-a-from-import-is-unproved',
     "from os import path\n"
     "import subprocess\n"
     "def f(*path):\n"
     "    subprocess.run(['git', 'status'], check=True)\n"
     "    return path(timeout=1)\n",
     [(5, 'unreadable', 'unplaced')]),
    ('lambda-vararg-receiver-shadowing-a-module-import-'
     'is-unproved',
     "import os\n"
     "import subprocess\n"
     "f = lambda *os: os(timeout=1)\n"
     "subprocess.run(['git', 'status'], check=True)\n",
     [(3, 'unreadable', 'unplaced')]),
    # A PAIR, and the pair is the control: these differ only in what the
    # factory returns, which is the boundary the `else` arm draws. Drop
    # that arm's `bound.add` and only the launcher row changes — the
    # module row is byte-identical, so it is a control and not a second
    # copy. Removing the `module_factories` half changes NEITHER; that
    # arm is among the unpinned ones at #1144. The for-target spelling
    # of the launcher shape reaches the same arm and pins no verdict this
    # row does not already pin, so it is not added.
    ('launcher-factory-bare-name-receiver-is-a-placed-launch',
     "import subprocess\n"
     "def make():\n"
     "    return subprocess.run\n"
     "go = make()\n"
     "go(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'git', 'timeout')]),
    ('module-factory-bare-name-receiver-stays-unplaced',
     "import subprocess\n"
     "def get():\n"
     "    return subprocess\n"
     "go = get()\n"
     "go(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'unreadable', 'unplaced')]),
    # Not part of the NAMED guard set above, and deliberately outside it.
    # Those rows are the controls for arms 1134 classified as CONTROLLED,
    # REDUNDANT-with-a-bound or DEAD; these are two routes this branch
    # handed OUT of `tests/_launch_census.py` and INTO the analyser, so
    # the repo has one answer about a launch call rather than two. Adding
    # a row here is not adding an arm to that set, and the set's closure
    # argument is unchanged by these three.
    # Two routes inside the analyser's unit, handed to it from
    # `tests/_launch_census.py` so the repo has one answer about a launch
    # call rather than two. A `**` unpacked on a launch, and a keyword the
    # stdlib does not take: each is a bound that can reach a child without
    # the word `timeout` appearing anywhere on the call. A launcher built by
    # `functools.partial` needs no row here — main's `unplaced` arm already
    # reports it, at `unreadable`, which is the head the gate keeps.
    ('unpack-at-a-non-git-launch',
     "import subprocess\n"
     "def probe():\n"
     "    subprocess.run(['node', 'x.js'], **{'timeout': 30})\n",
     [(3, 'non-git', 'unpack')]),
    ('foreign-keyword-on-a-launch',
     "import subprocess\n"
     "def probe():\n"
     "    subprocess.run(['git', 'status'], check=True, timout=30)\n",
     [(3, 'git', 'keyword')]),
    # Negative space for both: a launch that takes only what the stdlib
    # takes emits nothing, so neither rule can be firing on every launch.
    # This is the direction that keeps them from becoming false-positive
    # generators, and it is why the keyword set is read from the signature
    # rather than written down.
    ('a-clean-launch-emits-nothing',
     "import subprocess\n"
     "def probe():\n"
     "    return subprocess.run(['git', 'status'], check=True, cwd='/tmp')\n",
     []),
)
