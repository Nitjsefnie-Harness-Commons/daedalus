"""Structured-sink rows that red-exercise the tree-wide bound rule's two
fail-closed branches.

LAUNCH_REFUSAL_ROWS is a table of refusal *strings*, so it cannot pin the
unplaced and ambiguous paths: neither emits a refusal string, only a
structured (lineno, head, kind) the tree-wide rule consumes. Each row here is
(label, source, expected sites); the suite asserts the analyser's own
`bound_sites` output, so deleting the unplaced path or the ambiguity
mechanism makes the matching row fail.
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
    # Negative space of the widened unplaced class: the sys.modules receiver
    # recognition treats ANY sys.modules[...] as a subprocess-derived module,
    # so a non-subprocess module reached that way is refused too. That
    # over-refusal is deliberate and fail-closed (zero live sites); this pins
    # widened boundary, and deleting the derives receiver path turns it red.
    ('sys-modules-negative-space',
     "import sys\n"
     "def probe():\n"
     "    sys.modules['json'].dumps({}, **{'timeout': 30})\n",
     [(3, 'unreadable', 'unplaced')]),
    # A module exec'd into a namespace dict and reached through the key
    # naming it. The exec call itself is refused separately; the launch it
    # makes reachable is placed, so the bound site reads its real head
    # rather than the unreadable one the unplaced path would give it.
    ('exec-namespace-receiver',
     "import subprocess\n"
     "ns = {}\n"
     "exec('import subprocess', ns)\n"
     "ns['subprocess'].run(['git', 'status'], check=True, timeout=30)\n",
     [(4, 'git', 'timeout')]),
    # Two classes, one attribute name, two values: the resolution is keyed
    # on the enclosing class, so the subprocess half is refused and the
    # json half is not placed at all.
    ('two-classes-one-attribute-name',
     "import json\n"
     "import subprocess\n"
     "class Launcher:\n"
     "    mod = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n"
     "class Reader:\n"
     "    mod = json\n"
     "    def read(self):\n"
     "        return self.mod.dumps({})\n",
     [(6, 'git', 'timeout')]),
    # A class body binding resolves and a constructor's own assignment does
    # not, so the second launch contributes no site at all.
    ('class-attribute-resolves-init-attribute-does-not',
     "import subprocess\n"
     "class Early:\n"
     "    mod = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n"
     "class Late:\n"
     "    def __init__(self):\n"
     "        self.mod = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     [(5, 'git', 'timeout')]),
    # A class body assignment whose target is not a name records no
    # attribute, so the receiver that shares its spelling is still
    # unresolvable rather than bound to the written value.
    ('class-attribute-subscript-target-records-nothing',
     "import subprocess\n"
     "class Runner:\n"
     "    slots[0] = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     []),
    # A name that is a module factory is a callable, not the module, so a
    # receiver spelled as that bare name is not placed even though the
    # expression derives. Only a receiver the Name arms cannot carry is
    # placed through the new arm.
    ('module-factory-name-receiver-stays-unplaced',
     "import subprocess\n"
     "def get():\n"
     "    return subprocess\n"
     "get.run(['git', 'status'], check=True, timeout=30)\n",
     [(4, 'unreadable', 'unplaced')]),
    # A namespace whose own value is a subprocess-derived binding: the
    # receiver derives, but the receiver resolver refuses that base, so
    # the launch is refused and reaches the tree-wide rule as an unplaced
    # site at an unreadable head rather than a placed one.
    ('derived-base-the-resolver-refuses',
     "import subprocess\n"
     "mods = {'subprocess': subprocess}\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n"
     "mods['subprocess'].run(['git', 'status'], check=True, timeout=30)\n",
     [(4, 'unreadable', 'unplaced'), (3, 'git', 'timeout')]),
    # A base this module does not define carries no readable table, so the
    # attribute it would bind does not resolve: the class that binds the
    # name in its own body places a site and the one that inherits it from
    # an imported mixin places none.
    ('inherited-attribute-resolves-an-unresolvable-base-does-not',
     "import subprocess\n"
     "from mixins import Helper\n"
     "class Early:\n"
     "    mod = subprocess\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n"
     "class Late(Helper):\n"
     "    def go(self):\n"
     "        return self.mod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     [(6, 'git', 'timeout')]),
    # The same split on a namespace key: a class body attribute resolves,
    # a constructor's own assignment does not.
    ('class-body-attribute-key-resolves-init-attribute-key-does-not',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n"
     "ns = {}\n"
     "class Runner:\n"
     "    def __init__(self):\n"
     "        self.key = 'subprocess'\n"
     "    def go(self):\n"
     "        return ns[self.key].run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     [(2, 'git', 'timeout')]),
    # A `+` whose operand is not a string is not a constant. Both rows
    # answer with a verdict; dropping the string guard on the constant
    # makes the fold raise TypeError instead, which is what pins it.
    ('import-module-name-concat-with-a-non-string',
     "import importlib\n"
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n"
     "mod = importlib.import_module('sub' + 1)\n",
     [(3, 'git', 'timeout')]),
    ('subscript-key-concat-with-a-non-string',
     "import subprocess\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n"
     "ns = {}\n"
     "ns['sub' + 1].dumps({})\n",
     [(2, 'git', 'timeout')]),
    # The `+`-folding arm recurses into fresh resolver calls, each with its
    # own loop, so the loop cap alone does not bound it: this name feeds
    # itself. The correct answer is a verdict, and dropping the seen-guard
    # turns it into a RecursionError that takes the whole tree-wide rule
    # down, which is what makes this row discriminating.
    ('self-referential-concat-name',
     "import importlib\n"
     "import subprocess\n"
     "a = a + a\n"
     "mod = importlib.import_module(a)\n"
     "subprocess.run(['git', 'status'], check=True)\n",
     []),
    # The foldable module-name family. Every one of these hands
    # `import_module` an expression Python folds to the string
    # `subprocess` and the resolver does not, so no bound site is emitted
    # and the tree-wide rule has nothing to read. The rows live HERE and
    # not in LAUNCH_REFUSAL_ROWS because the predicate that consumes them
    # is `bound_sites`: a refusal string would not stand in for the site
    # the rule never gets. Each is a ratchet — it goes red if the family
    # is ever taught, which is a decision rather than an accident.
    ('module-name-folded-by-an-f-string',
     "import importlib\n"
     "import subprocess\n"
     "name = 'subprocess'\n"
     "mod = importlib.import_module(f\"{name}\")\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'git', 'timeout')]),
    ('module-name-folded-by-percent-format',
     "import importlib\n"
     "import subprocess\n"
     "name = 'subprocess'\n"
     "mod = importlib.import_module('%s' % name)\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'git', 'timeout')]),
    ('module-name-folded-by-str-format',
     "import importlib\n"
     "import subprocess\n"
     "name = 'subprocess'\n"
     "mod = importlib.import_module('{0}'.format(name))\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'git', 'timeout')]),
    ('module-name-folded-by-str-join',
     "import importlib\n"
     "import subprocess\n"
     "name = 'subprocess'\n"
     "mod = importlib.import_module(''.join((name,)))\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'git', 'timeout')]),
    ('module-name-indexed-from-a-tuple-literal',
     "import importlib\n"
     "import subprocess\n"
     "pair = ('subprocess', 1)\n"
     "mod = importlib.import_module(pair[0])\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'git', 'timeout')]),
    ('module-name-indexed-from-a-dict-literal',
     "import importlib\n"
     "import subprocess\n"
     "mapping = {'k': 'subprocess'}\n"
     "mod = importlib.import_module(mapping['k'])\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n",
     [(5, 'git', 'timeout')]),
    ('module-name-bound-by-a-walrus',
     "import importlib\n"
     "import subprocess\n"
     "mod = importlib.import_module(held := 'subprocess')\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n",
     [(4, 'git', 'timeout')]),
    ('module-name-name-chain-past-the-cap',
     "import importlib\n"
     "import subprocess\n"
     "n0 = 'subprocess'\n"
     "n1 = n0\n"
     "n2 = n1\n"
     "n3 = n2\n"
     "n4 = n3\n"
     "n5 = n4\n"
     "n6 = n5\n"
     "n7 = n6\n"
     "n8 = n7\n"
     "mod = importlib.import_module(n8)\n"
     "subprocess.run(['git', 'status'], check=True, timeout=30)\n",
     [(13, 'git', 'timeout')]),
    # The MISS sentinel. A base that binds the name twice stores None for
    # it, and a base read BEFORE the one that binds the string stops the
    # resolution there. Reading an absent entry as the stored None instead
    # would drop that first candidate and let the later base through,
    # which is the one difference a reader cannot see from the code alone.
    ('ambiguous-base-attribute-does-not-hide-a-later-binding',
     "import subprocess\n"
     "ns = {}\n"
     "class Root:\n"
     "    key = 'json'\n"
     "    key = 'subprocess'\n"
     "class Near:\n"
     "    key = 'subprocess'\n"
     "class Holder(Root, Near):\n"
     "    def go(self):\n"
     "        return ns[self.key].run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     []),
    # A `global` in a class body writes the module's name, so it is not a
    # class attribute and `self.gmod` is not the module at runtime.
    ('global-declared-class-body-name-is-not-a-class-attribute',
     "import subprocess\n"
     "class Base:\n"
     "    global gmod\n"
     "    gmod = subprocess\n"
     "class Child(Base):\n"
     "    def go(self):\n"
     "        return self.gmod.run(\n"
     "            ['git', 'status'], check=True, timeout=30)\n",
     []),
    # One property, one walk. The class NAMESPACE binding the fixpoint
    # uses and the class ATTRIBUTE table a receiver reads come from the
    # same pass, so a name a class body binds under a condition is visible
    # both as the class name and as an attribute. Reading only the
    # namespace's direct statements dropped the conditional half, and the
    # two tables then disagreed about one class.
    ('class-namespace-and-attribute-read-the-same-walk',
     "import subprocess\n"
     "FLAG = True\n"
     "class Direct:\n"
     "    mod = subprocess\n"
     "class Conditional:\n"
     "    if FLAG:\n"
     "        mod = subprocess\n"
     "first = Direct\n"
     "second = Conditional\n"
     "def go():\n"
     "    one = first.mod.run(\n"
     "        ['git', 'status'], check=True, timeout=30)\n"
     "    return second.mod.run(\n"
     "        ['git', 'status'], check=True, timeout=30)\n",
     [(11, 'unreadable', 'unplaced'), (13, 'unreadable', 'unplaced')]),
    # A walrus is an expression rather than a statement, so no statement
    # list names it; the class body binds the name and this walk must too.
    ('class-body-walrus-is-a-class-namespace-binding',
     "import subprocess\n"
     "class Base:\n"
     "    if (held := subprocess):\n"
     "        mod = held\n"
     "handle = Base\n"
     "def go():\n"
     "    return handle.mod.run(\n"
     "        ['git', 'status'], check=True, timeout=30)\n",
     [(7, 'unreadable', 'unplaced')]),
    # A base name resolves in the enclosing scope and then falls back to
    # the module's globals, so a class statement inside a function still
    # reaches the module-level class it never names itself.
    ('base-falls-back-to-the-module-globals',
     "import subprocess\n"
     "class Base:\n"
     "    mod = subprocess\n"
     "def build():\n"
     "    class Child(Base):\n"
     "        def go(self):\n"
     "            return self.mod.run(\n"
     "                ['git', 'status'], check=True, timeout=30)\n",
     [(7, 'git', 'timeout')]),
)
