"""The synthetic snippets that red-exercise the repo-layout audit's
per-launch refusal limbs, one row per limb; the source-tier limbs
(import aliases, from-imports, unpack-derived names, eval/exec,
machinery members, undefined names, receiver resolution, no visible
launch) are outside this table's scope."""
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
)
