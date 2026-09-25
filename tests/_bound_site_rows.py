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
)
