"""The synthetic snippets that red-exercise the repo-layout audit's
refusal limbs, at least one row per refusal site in both tiers: the
per-launch limbs, and the source-tier limbs (import aliases,
from-imports, the no-plain-import gate, unpack-derived names, bound
non-name assignment targets, eval/exec, machinery members, undefined
names, receiver resolution through an import name, a class body
attribute (own or inherited) or a namespace key, and no visible
launch)."""
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
    # Attribute lookup walks the bases, so a base's class body binds the
    # receiver too. The receiver's spelling is not what decides it.
    ('inherited-class-attribute-receiver',
     "import subprocess\n"
     "class Base:\n"
     "    mod = subprocess\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('two-level-inherited-class-attribute-receiver',
     "import subprocess\n"
     "class Base:\n"
     "    mod = subprocess\n"
     "class Mid(Base):\n"
     "    pass\n"
     "class Leaf(Mid):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('classmethod-attribute-receiver-through-inheritance',
     "import subprocess\n"
     "class Base:\n"
     "    mod = subprocess\n"
     "class Child(Base):\n"
     "    @classmethod\n"
     "    def go(cls):\n"
     "        return cls.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('type-self-attribute-receiver',
     "import subprocess\n"
     "class Runner:\n"
     "    mod = subprocess\n"
     "    def go(self):\n"
     "        return type(self).mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    # Where two branches of a diamond bind the name differently, either
    # derived value is taken: no C3 linearisation, so report.
    ('diamond-inheritance-derives-from-either-branch',
     "import json\n"
     "import subprocess\n"
     "class Left:\n"
     "    mod = json\n"
     "class Right(Left):\n"
     "    mod = subprocess\n"
     "class Far(Left):\n"
     "    pass\n"
     "class Both(Right, Far):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    # The override, in both directions: the derived class's own table wins,
    # so a subclass that rebinds the name to something else is not a launch
    # and one that rebinds it to subprocess is.
    ('subclass-override-binding-the-attribute-to-subprocess',
     "import json\n"
     "import subprocess\n"
     "class Base:\n"
     "    mod = json\n"
     "class Child(Base):\n"
     "    mod = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('subclass-override-binding-the-attribute-to-another-module',
     "import json\n"
     "import subprocess\n"
     "class Base:\n"
     "    mod = subprocess\n"
     "class Child(Base):\n"
     "    mod = json\n"
     "    def go(self):\n"
     "        return self.mod.dumps({})\n",
     'declares no launch the audit can see through'),
    # An attribute whose own base carries no class body binding: the inner
    # class is unreadable, so the name does not resolve either way.
    ('unbound-inner-attribute-does-not-resolve',
     "import subprocess\n"
     "class Runner:\n"
     "    inner = None\n"
     "    def go(self):\n"
     "        return self.inner.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    # A namespace key held in a class body attribute reads like any other
    # receiver.
    ('class-body-attribute-names-a-namespace-key',
     "import subprocess\n"
     "ns = {}\n"
     "class Runner:\n"
     "    key = 'subprocess'\n"
     "    def go(self):\n"
     "        return ns[self.key].run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    # Negative space of the widened subscript class: a key reading
    # 'subprocess' derives whatever the base, so a mapping with nothing to
    # do with modules is read as one. Deliberate and fail-closed, no live
    # site; deleting the derives key path turns this row red.
    ('subscript-key-on-an-unrelated-base-derives-alike',
     "import os\n"
     "import subprocess\n"
     "os.environ['subprocess'].run(\n"
     "    ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    # A list target unpacks exactly as a tuple target does.
    ('list-target-subprocess-assignment',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "[sp, other] = [subprocess, 1]\n",
     'unpacks subprocess-derived values the audit cannot follow'),
    # A class body is a block Python executes, so a name bound inside an
    # `if`, a loop, a `with` or a `try` is a class-namespace binding. Each
    # form is its own control, because the walk that reads them is one
    # nearest-scope test and a row for only some of the forms would leave
    # the others unproved.
    ('class-body-if-binds-the-attribute',
     "import subprocess\n"
     "FLAG = True\n"
     "class Base:\n"
     "    if FLAG:\n"
     "        mod = subprocess\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('class-body-for-binds-the-attribute',
     "import subprocess\n"
     "class Base:\n"
     "    for _ in range(1):\n"
     "        mod = subprocess\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('class-body-while-binds-the-attribute',
     "import subprocess\n"
     "class Base:\n"
     "    while False:\n"
     "        mod = subprocess\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('class-body-with-binds-the-attribute',
     "import subprocess\n"
     "class Base:\n"
     "    with open(__file__):\n"
     "        mod = subprocess\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('class-body-try-binds-the-attribute',
     "import subprocess\n"
     "class Base:\n"
     "    try:\n"
     "        mod = subprocess\n"
     "    except OSError:\n"
     "        pass\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('class-body-nested-blocks-bind-the-attribute',
     "import subprocess\n"
     "class Base:\n"
     "    for _ in range(1):\n"
     "        if True:\n"
     "            try:\n"
     "                mod = subprocess\n"
     "            except OSError:\n"
     "                pass\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    # The other side of the same test: a nested function or a nested class
    # binds its own names, so a binding inside one is not the outer
    # class's attribute however deeply the class body nests it.
    ('nested-class-body-binding-is-not-the-outer-class-attribute',
     "import subprocess\n"
     "class Holder:\n"
     "    class Inner:\n"
     "        mod = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    ('nested-function-body-binding-is-not-a-class-attribute',
     "import subprocess\n"
     "class Holder:\n"
     "    if True:\n"
     "        def build():\n"
     "            mod = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    # The base name is read through the scope the class statement sits
    # in, so a class of the same name defined inside a function does not
    # take the module-level one's place.
    ('function-local-class-does-not-shadow-the-base-name',
     "import json\n"
     "import subprocess\n"
     "class Base:\n"
     "    mod = subprocess\n"
     "def build():\n"
     "    class Base:\n"
     "        mod = json\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('qualified-base-attribute-receiver',
     "import subprocess\n"
     "class Outer:\n"
     "    class Base:\n"
     "        mod = subprocess\n"
     "class Child(Outer.Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    # Negative space of the base-name union: a name the scope binds twice
    # yields every class it could name, so a base that binds the module
    # and a later rebinding of that name to another module over-refuse.
    ('base-name-bound-twice-over-refuses-either-class',
     "import json\n"
     "import subprocess\n"
     "class Base:\n"
     "    mod = subprocess\n"
     "class Base:\n"
     "    mod = json\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    ('diamond-bases-disagree-over-refuses',
     "import json\n"
     "import subprocess\n"
     "class Root:\n"
     "    pass\n"
     "class Far(Root):\n"
     "    mod = json\n"
     "class Near(Root):\n"
     "    mod = subprocess\n"
     "class Both(Far, Near):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
    # The `+`-folding arm recurses into fresh resolver calls, each with
    # its own loop, so the loop cap alone does not bound it.
    ('import-module-name-from-a-parameter',
     "import importlib\n"
     "import subprocess\n"
     "def go(argument):\n"
     "    mod = importlib.import_module(argument)\n"
     "    return mod.run(['git', 'status'], check=True, timeout=30)\n",
     'declares no launch the audit can see through'),
    ('base-name-bound-twice-catches-either-class',
     "import subprocess\n"
     "class Base:\n"
     "    mod = json\n"
     "class Base:\n"
     "    mod = subprocess\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     'carries a timeout='),
)
