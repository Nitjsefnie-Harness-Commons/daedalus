"""Read an action's pinned reference out of raw workflow text.

Not a suite itself — run_tests.py only loads `test_*.py`.

Raw text rather than `_yamlread`, so the suite that tests that decoder does
not anchor its own fixture on the code under test.
"""
import re


class WorkflowPinError(Exception):
    """A workflow does not pin an action exactly once."""


def pinned_action(workflow, action):
    """Return `action` with the 40-hex SHA `workflow` pins it to.

    Raises rather than guessing, because a plausible return is the silent
    no-op a derived mutation anchor exists to remove.
    """
    pattern = re.compile(
        r'(?<![0-9A-Za-z._/-])'
        + re.escape(action)
        + r'@[0-9a-f]{40}(?![0-9A-Za-z])')
    pins = sorted(set(pattern.findall(workflow)))
    if not pins:
        raise WorkflowPinError(f'no pin for {action!r} in workflow text')
    if len(pins) > 1:
        raise WorkflowPinError(f'conflicting pins for {action!r}: {pins!r}')
    return pins[0]
