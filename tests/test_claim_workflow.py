#!/usr/bin/env python3
"""Pin the claim action caller's permissions, prefilter and runner shape."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def test_the_claim_workflow_keeps_its_least_privilege_shape(tmp):
    del tmp
    workflow = (_util.ROOT / '.github' / 'workflows' / 'claim.yml').read_text(
        encoding='utf-8')
    # issues: write and nothing else — this job never reads the tree. Scoped
    # to the permissions block: the surrounding comments name other scopes to
    # say why they are absent, and a substring search would read those.
    _, marker, after = workflow.partition('\npermissions:\n')
    assert marker, workflow
    granted = []
    for line in after.splitlines():
        if not line.startswith('  ') or line.lstrip().startswith('#'):
            break
        granted.append(line.strip())
    assert granted == ['issues: write'], granted
    # Two claims racing must both be answered, so the group never cancels.
    assert 'cancel-in-progress: false' in workflow, workflow
    for guard in ('github.event.issue.pull_request == null',
                  "github.event.issue.state == 'open'",
                  "github.event.comment.user.type != 'Bot'"):
        assert guard in workflow, guard
    # Both names for giving an issue up reach the action, not just one.
    for command in ('/claim', '/unclaim', '/release'):
        term = f"contains(github.event.comment.body, '{command}')"
        assert term in workflow, command
    assert not re.search(r'^\s*(?:-\s+)?run:', workflow, re.MULTILINE), (
        'claim.yml must not contain a run block')
    _, marker, steps = workflow.partition('    steps:\n')
    assert marker, 'claim.yml must declare steps'
    entries = [line for line in steps.splitlines() if line.strip()]
    assert len(entries) == 1, 'claim.yml must have exactly one action step'
    assert re.fullmatch(
        r'      - uses: Nitjsefnie-Actions/claim@[0-9a-fA-F]{40}'
        r'  # v[0-9]+\.[0-9]+\.[0-9]+', entries[0]), (
            'claim action must use a full SHA pin with a version comment')
    assert '    runs-on: ubuntu-latest\n' in workflow, (
        'claim must remain a runner job')
    assert '    timeout-minutes: 5\n' in workflow, (
        'claim runner must keep its five-minute timeout')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='claimworkflow_'))
