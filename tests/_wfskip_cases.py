"""Real-workflow mutations for the skip-propagation integration tests."""
from _wfskip import implicit_skip_violations


_SUITES_IF = "    if: ${{ !cancelled() && !failure() }}\n"


def suites_skip_violation(workflow, condition):
    replacement = f"    if: ${{{{ {condition} }}}}\n"
    mutated = workflow.replace(_SUITES_IF, replacement, 1)
    assert mutated != workflow, 'the real suites condition was not mutated'
    prefix = 'suites: skippable ancestor actionlint;'
    return any(item.startswith(prefix)
               for item in implicit_skip_violations(mutated))
