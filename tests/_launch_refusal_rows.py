"""The synthetic snippets that red-exercise the repo-layout audit's
refusal limbs, at least one row per refusal site in both tiers: the
per-launch limbs, and the source-tier limbs (import aliases,
from-imports, the no-plain-import gate, unpack-derived names, bound
non-name assignment targets, eval/exec, machinery members, undefined
names, a receiver the analyser cannot resolve, and no visible launch).

The walls of the launch policy live in BOUND_SITE_ROWS, not here: a
refusal string cannot stand in for the structured site the tree-wide
rule reads.
"""
LAUNCH_REFUSAL_ROWS = (
    ('clone-without-init.defaultBranch',
     "import subprocess\n"
     "subprocess.run(\n"
     "    ['git', '-c', 'advice.detachedHead=false', 'clone'],\n"
     "    check=True)\n",
     'clones without the silencing -c init.defaultBranch=main'),
    ('clone-without-advice.detachedHead',
     "import subprocess\n"
     "subprocess.run(\n"
     "    ['git', '-c', 'init.defaultBranch=main', 'clone'],\n"
     "    check=True)\n",
     'clones without the silencing -c advice.detachedHead=false'),
    ('argv-not-a-list-literal',
     "import subprocess\n"
     "def build():\n"
     "    return ['git', 'clone']\n"
     "subprocess.run(build(), check=True)\n",
     'builds an argv the audit cannot read'),
    ('head-not-the-git-constant',
     "import subprocess\n"
     "subprocess.run(['ls', '-la'], check=True)\n",
     "argv does not start with the constant 'git'"),
    ('name-head-not-sys.executable',
     "import subprocess\n"
     "node = 'node'\n"
     "subprocess.run([node, 'x'], check=True)\n",
     "argv does not start with the constant 'git'"),
    ('wall-clock-timeout-argument',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True, timeout=10)\n",
     'carries a timeout='),
    ('launch-without-check',
     "import subprocess\n"
     "subprocess.run(['git', 'status'])\n",
     'does not fail loudly on a failed git command'),
    ('keyword-mapping-unpack',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True, **opts)\n",
     'unpacks a keyword mapping the audit cannot read'),
    ('launch-through-check-output',
     "import subprocess\n"
     "subprocess.check_output(['git', 'status'], check=True)\n",
     'launches through subprocess.check_output, which the audit does '
     'not see'),
    ('import-alias',
     "import subprocess as sp\n"
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n",
     'aliases the subprocess import'),
    ('from-import',
     "import subprocess\n"
     "from subprocess import run\n"
     "subprocess.run(['git', 'status'], check=True)\n",
     'from-imports subprocess'),
    ('no-plain-import',
     "subprocess.run(['git', 'status'], check=True)\n",
     'declares no plain "import subprocess"'),
    ('dunder-import-launch',
     "import subprocess\n"
     "sp = __import__('subprocess')\n"
     "sp.run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('unpack-derived-name-assignment',
     "import subprocess\n"
     "launcher = subprocess.run\n"
     "a, b = launcher(['git', 'status'], check=True)\n",
     'unpacks subprocess-derived values the audit cannot follow'),
    ('unpack-derived-name-for-target',
     "import subprocess\n"
     "launcher = subprocess.run\n"
     "for word, flag in launcher(['git', 'status'], check=True):\n"
     "    pass\n",
     'unpacks subprocess-derived values the audit cannot follow'),
    # A target that binds rather than unpacks gets its own limb, and the
    # three spellings it can arrive in are separate targets.
    ('attribute-target-subprocess-assignment',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "class Runner:\n"
     "    def __init__(self):\n"
     "        self.mod = subprocess\n",
     'binds a subprocess-derived value to an attribute or subscript '
     'target the audit cannot follow'),
    ('subscript-target-subprocess-assignment',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "def probe(target):\n"
     "    target[0] = subprocess\n",
     'binds a subprocess-derived value to an attribute or subscript '
     'target the audit cannot follow'),
    ('annotated-attribute-target-subprocess-assignment',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "def probe():\n"
     "    self.mod: object = subprocess\n",
     'binds a subprocess-derived value to an attribute or subscript '
     'target the audit cannot follow'),
    ('list-target-subprocess-assignment',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "[sp, other] = [subprocess, 1]\n",
     'unpacks subprocess-derived values the audit cannot follow'),
    ('eval-call',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "eval('subprocess.run([\"git\", \"status\"], check=True)')\n",
     'calls eval, which the audit cannot resolve'),
    ('machinery-member-call',
     "import subprocess\n"
     "import functools\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "functools.reduce(subprocess.run, [])\n",
     'calls functools.reduce, which the audit cannot resolve'),
    ('undefined-callee-name',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "unknown_launcher(['git', 'status'], check=True)\n",
     "calls undefined name 'unknown_launcher'"),
    ('receiver-through-call',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "getattr(subprocess, 'run')(['git', 'status'], check=True)\n",
     'calls through a receiver the audit cannot resolve'),
    ('receiver-through-subscript',
     "import subprocess\n"
     "import sys\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "sys.modules['subprocess'].run(['git', 'status'], check=True)\n",
     'calls through a receiver the audit cannot resolve'),
    ('callee-through-subscript',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "[subprocess.run][0](['git', 'status'], check=True)\n",
     'calls through a receiver the audit cannot resolve'),
    ('exec-call',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "exec('pass')\n",
     'calls exec, which the audit cannot resolve'),
    ('importlib-machinery-member-call',
     "import subprocess\n"
     "import importlib\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "importlib.reload(subprocess)\n",
     'calls importlib.reload, which the audit cannot resolve'),
    ('receiver-through-getattr',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "getattr(subprocess, 'run').__call__(['git', 'status'], check=True)\n",
     'calls through a receiver the audit cannot resolve'),
    ('receiver-through-partial-builtin-alias',
     "import subprocess\n"
     "from functools import partial as list\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "list(subprocess.run).__call__(['git', 'status'], check=True)\n",
     'calls through a receiver the audit cannot resolve'),
    ('receiver-through-import-module-builtin-alias',
     "import subprocess\n"
     "from importlib import import_module as list\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "list('subprocess').run(['git', 'status'], check=True)\n",
     'calls through a receiver the audit cannot resolve'),
    ('aliased-machinery-member-call',
     "import subprocess\n"
     "import importlib as il\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "il.reload(subprocess)\n",
     'calls il.reload, which the audit cannot resolve'),
    # Issue #1099: the module name reaches import_module through a name.
    # The resolver is kept for exactly these and nothing more; every
    # spelling it still cannot read is a reported site, not a row.
    ('import-module-name-held-in-a-variable',
     "import importlib\n"
     "import subprocess\n"
     "name = 'subprocess'\n"
     "mod = importlib.import_module(name)\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('import-module-name-chain',
     "import importlib\n"
     "import subprocess\n"
     "first = 'subprocess'\n"
     "second = first\n"
     "mod = importlib.import_module(second)\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('import-module-keyword-name-argument',
     "import importlib\n"
     "import subprocess\n"
     "name = 'subprocess'\n"
     "mod = importlib.import_module(name=name)\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('import-module-from-import-alias',
     "import subprocess\n"
     "from importlib import import_module as load\n"
     "name = 'subprocess'\n"
     "mod = load(name)\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('import-module-aliased-module',
     "import importlib as il\n"
     "import subprocess\n"
     "name = 'subprocess'\n"
     "mod = il.import_module(name)\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('import-module-name-from-a-concat',
     "import importlib\n"
     "import subprocess\n"
     "mod = importlib.import_module('sub' + 'process')\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    # The two readings that resolve to nothing: a name bound twice is a
    # guess, and a self-referential chain stops rather than loops.
    ('import-module-name-bound-twice',
     "import importlib\n"
     "import subprocess\n"
     "name = 'json'\n"
     "name = 'subprocess'\n"
     "mod = importlib.import_module(name)\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    ('import-module-name-from-a-parameter',
     "import importlib\n"
     "import subprocess\n"
     "def go(argument):\n"
     "    mod = importlib.import_module(argument)\n"
     "    return mod.run(['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    ('import-module-other-module-name',
     "import importlib\n"
     "import subprocess\n"
     "name = 'json'\n"
     "mod = importlib.import_module(name)\n"
     "mod.dumps({})\n",
     'declares no launch the audit can see through'),
    # Issue #1100: the receiver is a class attribute. The row pins the
    # NO-VISIBLE-LAUNCH gate — the analyser does not place this launch.
    # What puts it in scope is the unplaced bound site from a different
    # code path, pinned as `class-attribute-receiver-is-reported` in
    # BOUND_SITE_ROWS; the two together are #1100's coverage.
    ('class-attribute-receiver-is-reported',
     "import subprocess\n"
     "class Runner:\n"
     "    mod = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    ('inherited-attribute-receiver-is-reported',
     "import subprocess\n"
     "class Base:\n"
     "    mod = subprocess\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    # A namespace the module builds at run time, reached by its key.
    ('namespace-key-receiver-is-placed',
     "import subprocess\n"
     "ns = {}\n"
     "ns['subprocess'].run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('no-visible-launch',
     "import subprocess\n"
     "print('git status')\n",
     'declares no launch the audit can see through'),
    # A loop target takes the same split as an assignment target: a
    # tuple unpacks, a subscript binds.
    ('subscript-for-target-subprocess-launch',
     "import subprocess\n"
     "launcher = subprocess.run\n"
     "for ns[0] in launcher(['git', 'status'], check=True):\n"
     "    pass\n",
     'binds a subprocess-derived value to an attribute or subscript '
     'target the audit cannot follow'),
    ('tuple-for-target-subprocess-launch',
     "import subprocess\n"
     "launcher = subprocess.run\n"
     "for word, flag in launcher(['git', 'status'], check=True):\n"
     "    pass\n",
     'unpacks subprocess-derived values the audit cannot follow'),
    # The human-readable half of the one route `BOUND_SITE_ROWS` pins that
    # this branch added, so a message that stops naming the problem is
    # caught here rather than by a reader of a CI log. The other two
    # routes need no row: a `**` unpacked on a launch is main's
    # `keyword-mapping-unpack` row, and a `functools.partial` is reported by
    # the `unplaced` arm, which pins a head rather than a message.
    ('foreign-keyword-on-a-launch',
     "import subprocess\n"
     "def probe():\n"
     "    subprocess.run(['git', 'status'], check=True, timout=30)\n",
     'which this subprocess does not take'),
)
