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
    # classified. The set is NAMED, not general, on purpose: a claim about
    # every clause in the two analyser modules is open-ended, and each
    # review round falsified the last round's version of it one arm further
    # out. The named guards are the four limbs of proved_fixed's
    # disjunction, the machinery_route loop, the four `seen` guards in
    # _argv_read.py, and the launcher-factory arm of the bound fixpoint.
    # Each is CONTROLLED — a row below, or a control in
    # test_repo_layout.py, that fails when the clause is deleted — or it
    # is not controlled, for one of exactly two closed reasons written at
    # the clause: REDUNDANT, where a named bound guarantees it, or DEAD,
    # where no input reaches it. There is no third answer, and that is
    # deliberate: a category for "live and unpinned" would admit the
    # analyser's whole residue and make the rule a licence rather than a
    # check. The arms that are live and unpinned are named at their own
    # clauses and tracked at issue #1144, which is where a reader who
    # trusts this set should look next.
    # A name that spelled a subprocess import and was then rebound to a
    # fixed value sits in `safe_names` and NOT in `bound`, so the
    # `subprocess_names` limb of proved_fixed is the only thing refusing
    # it. Each of these three loses its row when that limb is dropped.
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
    # A plain `import subprocess` puts `subprocess` in `safe_names`, so the
    # first limb of proved_fixed's disjunction does not fire, and it enters
    # NEITHER `bound` nor `subprocess_names` — an alias needs an asname and
    # a from-import needs its own form. Only the `== 'subprocess'` limb
    # refuses it, which is why that limb cannot be read off the others.
    ('bare-subprocess-receiver-is-unplaced',
     "import subprocess\nsubprocess(timeout=30)\n",
     [(2, 'unreadable', 'unplaced')]),
    # The bound behind the `bound` limb's redundancy, pinned from the other
    # side: a call through a bare Name in `bound` is collected as a PLACED
    # launch, so it reports a `timeout` row here and never an `unplaced`
    # one. This row is what refuses a refactor of the launch-collection
    # chain that would turn that placement into an unplaced report.
    ('bound-name-called-bare-is-a-placed-launch',
     "import os\n"
     "import subprocess\n"
     "os = subprocess\n"
     "os(timeout=30)\n",
     [(4, 'unreadable', 'timeout')]),
    # A `*args` / `**kwargs` parameter binds its name in its own scope
    # exactly as a positional one does, and _parameters has to collect it
    # or the receiver reads as proved. Each of these four loses its row
    # when that collection goes; the positional spellings above keep
    # theirs either way, which is what makes the vararg arm the
    # discriminator.
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
    # A PAIR, and the pair is the control: these two differ only in what
    # the factory returns, and that difference is the whole boundary the
    # launcher-factory arm of the bound fixpoint draws. Both are refused
    # identically when the arm is deleted; only with it in place does a
    # factory returning a LAUNCH read as one and a factory returning the
    # MODULE read as unproved. The for-target spelling of the first shape
    # reaches the same arm and changes no verdict this row does not
    # already pin, so it is named here and not added.
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
)
