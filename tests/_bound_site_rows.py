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
)
