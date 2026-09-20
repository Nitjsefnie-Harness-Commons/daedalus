"""The synthetic snippets that red-exercise the repo-layout audit's
refusal limbs, at least one row per refusal site in both tiers: the
per-launch limbs, and the source-tier limbs (import aliases,
from-imports, the no-plain-import gate, unpack-derived names,
eval/exec, machinery members, undefined names, receiver resolution,
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
     "argv = ['git', 'clone']\n"
     "subprocess.run(argv, check=True)\n",
     'builds an argv the audit cannot read'),
    ('head-not-the-git-constant',
     "import subprocess\n"
     "subprocess.run(['ls', '-la'], check=True)\n",
     "argv does not start with the constant 'git'"),
    ('name-head-not-sys.executable',
     "import subprocess\n"
     "git = 'git'\n"
     "subprocess.run([git, 'clone'], check=True)\n",
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
    ('no-visible-launch',
     "import subprocess\n"
     "print('git status')\n",
     'declares no launch the audit can see through'),
)
