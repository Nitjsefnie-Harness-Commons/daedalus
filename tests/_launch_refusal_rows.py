"""The synthetic snippets that red-exercise the repo-layout audit's
refusal limbs, at least one row per refusal site in both tiers: the
per-launch limbs, and the source-tier limbs (import aliases,
from-imports, the no-plain-import gate, unpack-derived names,
eval/exec, machinery members, undefined names, receiver resolution
through an import name, a class body attribute or a namespace key, and
no visible launch)."""
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
    ('no-visible-launch',
     "import subprocess\n"
     "print('git status')\n",
     'declares no launch the audit can see through'),
    # The module name reaches import_module through a variable: resolved
    # through the bindings table, so the launch is placed and refused.
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
    # The two readings that resolve to nothing: a name bound twice is a
    # guess, so the last write is not taken even when it names subprocess,
    # and a self-referential chain stops rather than loops.
    ('import-module-name-bound-twice',
     "import importlib\n"
     "import subprocess\n"
     "name = 'json'\n"
     "name = 'subprocess'\n"
     "mod = importlib.import_module(name)\n"
     "mod.run(['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    ('import-module-self-referential-name',
     "import importlib\n"
     "import subprocess\n"
     "name = name\n"
     "mod = importlib.import_module(name)\n"
     "mod.dumps({})\n",
     'declares no launch the audit can see through'),
    ('import-module-other-module-literal',
     "import importlib\n"
     "import subprocess\n"
     "mod = importlib.import_module('json')\n"
     "mod.dumps({})\n",
     'declares no launch the audit can see through'),
    ('import-module-other-module-name',
     "import importlib\n"
     "import subprocess\n"
     "name = 'json'\n"
     "mod = importlib.import_module(name)\n"
     "mod.dumps({})\n",
     'declares no launch the audit can see through'),
    # The class body attribute receiver, and the shapes around it that must
    # still place a launch.
    ('class-attribute-receiver',
     "import subprocess\n"
     "class Runner:\n"
     "    mod = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('classmethod-attribute-receiver',
     "import subprocess\n"
     "class Runner:\n"
     "    mod = subprocess\n"
     "    @classmethod\n"
     "    def go(cls):\n"
     "        return cls.mod.run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('annotated-class-attribute-receiver',
     "import subprocess\n"
     "class Runner:\n"
     "    mod: object = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    # A value reached through the attribute's own base resolves against the
    # outer class, which fails closed: the class binds `mod` to subprocess.
    ('nested-attribute-receiver',
     "import subprocess\n"
     "class Runner:\n"
     "    inner = None\n"
     "    mod = subprocess\n"
     "    def go(self):\n"
     "        return self.inner.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('class-attribute-bound-to-a-module',
     "import json\n"
     "import subprocess\n"
     "class Reader:\n"
     "    mod = json\n"
     "    def go(self):\n"
     "        return self.mod.dumps({})\n",
     'declares no launch the audit can see through'),
    ('class-attribute-bound-to-a-string',
     "import subprocess\n"
     "class Reader:\n"
     "    mod = 'subprocess'\n"
     "    def go(self):\n"
     "        return self.mod.upper()\n",
     'declares no launch the audit can see through'),
    ('class-attribute-bound-to-a-local-object',
     "import subprocess\n"
     "class Reader:\n"
     "    mod = object()\n"
     "    def go(self):\n"
     "        return self.mod\n",
     'declares no launch the audit can see through'),
    # One attribute bound twice in a class body resolves to nothing, for
    # the same last-wins-is-a-guess reason the module table uses: a class
    # binding `mod` to json and then to subprocess is not read as the
    # second write.
    ('class-attribute-bound-twice',
     "import subprocess\n"
     "class Runner:\n"
     "    mod = json.dumps\n"
     "    mod = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    # A class attribute that reads itself stops instead of recursing.
    ('class-attribute-self-referential',
     "import subprocess\n"
     "class Runner:\n"
     "    mod = self.mod\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    # A run-time namespace keyed by the module's own name, however the key
    # is spelled.
    ('subscript-namespace-key-in-a-name',
     "import subprocess\n"
     "ns = {}\n"
     "key = 'subprocess'\n"
     "ns[key].run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('subscript-namespace-key-from-a-concat',
     "import subprocess\n"
     "ns = {}\n"
     "ns['sub' + 'process'].run(['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('subscript-tuple-key-does-not-resolve',
     "import subprocess\n"
     "ns = {}\n"
     "key = ('subprocess',)\n"
     "ns[key].dumps({})\n",
     'declares no launch the audit can see through'),
    ('subscript-other-module-key',
     "import subprocess\n"
     "ns = {}\n"
     "ns['json'].dumps({})\n",
     'declares no launch the audit can see through'),
    # The receiver a method parameter carries is not traceable and is not a
    # launch, however member-like the method it names.
    ('bare-parameter-receiver',
     "import subprocess\n"
     "def probe(data):\n"
     "    return data.replace('a', 'b')\n",
     'declares no launch the audit can see through'),
)
