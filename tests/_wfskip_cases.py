"""Real-workflow mutations for the skip-propagation integration tests."""
from _wfskip import implicit_skip_violations


_TEST_IF = "    if: ${{ !cancelled() && !failure() }}\n"


def job_skip_violation(workflow, condition):
    replacement = ('' if condition is None
                   else f"    if: ${{{{ {condition} }}}}\n")
    mutated = workflow.replace(_TEST_IF, replacement, 1)
    assert mutated != workflow, 'the real test condition was not mutated'
    prefix = 'test: skippable ancestor actionlint;'
    return any(item.startswith(prefix)
               for item in implicit_skip_violations(mutated))
