#!/usr/bin/env python3
"""The claim workflow's assignee rules, exercised as a shell script.

These suites run claim.yml's run block against a stub `gh`, so /claim
and /unclaim are tested as behaviour — who wins a held issue, what an
unclaim removes — rather than read back as YAML.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


_GH_STUB = r"""#!/usr/bin/env python3
# Stands in for `gh`. State lives in $STUB_STATE: one assignee per line.
# Every call is appended to $STUB_CALLS so the test can assert what was sent.
import os, sys, pathlib
state = pathlib.Path(os.environ['STUB_STATE'])
calls = pathlib.Path(os.environ['STUB_CALLS'])
argv = sys.argv[1:]
with calls.open('a', encoding='utf-8') as handle:
    handle.write(' '.join(argv) + chr(10))
assignees = [x for x in state.read_text(encoding='utf-8').split() if x]
who = ''
for arg in argv:
    if arg.startswith('assignees[]='):
        who = arg.split('=', 1)[1]
if '-X' in argv and 'POST' in argv and any('/assignees' in a for a in argv):
    if os.environ.get('STUB_REFUSE') != '1' and who not in assignees:
        assignees.append(who)
    state.write_text(chr(10).join(assignees), encoding='utf-8')
elif '-X' in argv and 'DELETE' in argv and any('/assignees' in a for a in argv):
    state.write_text(
        chr(10).join(a for a in assignees if a != who), encoding='utf-8')
elif any(a.endswith('/comments') for a in argv):
    pass
elif '--jq' in argv:
    print(chr(10).join(assignees))
"""


def _claim_script():
    workflow = (_util.ROOT / '.github' / 'workflows' / 'claim.yml').read_text(
        encoding='utf-8')
    _, marker, after = workflow.partition('        run: |\n')
    assert marker, 'claim.yml has no run block shaped as this test expects'
    lines = []
    for line in after.splitlines():
        if line.strip() and not line.startswith('          '):
            break
        lines.append(line[10:])
    return chr(10).join(lines)


def _run_claim(tmp, body, assigned, actor='alice', refuse=False):
    bash = _util.workflow_bash()
    workdir = Path(tmp) / f'claim-{abs(hash((body, tuple(assigned), actor, refuse)))}'
    (workdir / 'bin').mkdir(parents=True, exist_ok=True)
    stub = workdir / 'bin' / 'gh'
    stub.write_text(_GH_STUB, encoding='utf-8')
    stub.chmod(0o755)
    state = workdir / 'state'
    state.write_text(chr(10).join(assigned), encoding='utf-8')
    calls = workdir / 'calls'
    calls.write_text('', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'STUB_STATE': str(state), 'STUB_CALLS': str(calls),
        'STUB_REFUSE': '1' if refuse else '0',
        'GH_TOKEN': 'stub', 'REPO': 'owner/repo', 'ISSUE': '1',
        'ACTOR': actor, 'BODY': body,
    }
    result = subprocess.run([bash, '-c', _claim_script()], env=env,
                            capture_output=True, text=True, timeout=60)
    return (
        [x for x in state.read_text(encoding='utf-8').split() if x],
        calls.read_text(encoding='utf-8'),
        result,
    )


def test_the_claim_command_assigns_only_its_own_commenter(tmp):
    """/claim and /unclaim, exercised as shell rather than read as YAML.

    The interesting cases are the refusals: a claim on an issue somebody else
    holds must not steal it, an unclaim from a non-assignee must not touch the
    assignee that is there, and an unclaim must remove exactly one login so a
    second assignee survives.
    """
    assigned, calls, result = _run_claim(tmp, '/claim', [])
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert assigned == ['alice'], assigned
    assert 'Assigned to @alice.' in calls, calls

    # Already held by someone else: no assignment call at all.
    assigned, calls, result = _run_claim(tmp, '/claim', ['bob'])
    assert assigned == ['bob'], assigned
    assert '-X POST' not in calls, calls
    assert 'already claimed by @bob' in calls, calls

    # Unclaim removes ONLY the commenter, leaving a co-assignee in place.
    assigned, calls, result = _run_claim(tmp, '/unclaim', ['alice', 'bob'])
    assert assigned == ['bob'], assigned
    assert '-X DELETE' in calls and 'assignees[]=alice' in calls, calls

    # Unclaim by a non-assignee changes nothing.
    assigned, calls, result = _run_claim(tmp, '/unclaim', ['bob'])
    assert assigned == ['bob'], assigned
    assert '-X DELETE' not in calls, calls

    # /release is the same command under the name the reference
    # implementation uses, and behaves identically in both directions.
    assigned, calls, result = _run_claim(tmp, '/release', ['alice', 'bob'])
    assert assigned == ['bob'], assigned
    assert '-X DELETE' in calls and 'assignees[]=alice' in calls, calls
    assigned, calls, result = _run_claim(tmp, '/release', ['bob'])
    assert assigned == ['bob'], assigned
    assert '-X DELETE' not in calls, calls

    # Whitespace and a Windows line ending still match exactly...
    assigned, _calls, _result = _run_claim(tmp, '  /claim\r\n', [])
    assert assigned == ['alice'], assigned

    # ...but a sentence containing the word is not a command, and must not
    # reach the API at all.
    assigned, calls, result = _run_claim(tmp, 'please /claim this for me', [])
    assert assigned == [], assigned
    assert calls == '', calls
    assert result.returncode == 0, (result.stdout, result.stderr)

    # GitHub silently ignoring an assignee is reported, not assumed away.
    assigned, calls, result = _run_claim(tmp, '/claim', [], refuse=True)
    assert assigned == [], assigned
    assert result.returncode != 0, result.stdout
    assert 'would not accept @alice' in calls, calls


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
    # Both names for giving an issue up reach the script, not just one.
    for command in ('/claim', '/unclaim', '/release'):
        assert f"contains(github.event.comment.body, '{command}')" in workflow, command
    # The body is attacker-controlled: it travels by environment, and the only
    # ${{ }} in the run block would be an injection.
    _, _, after = workflow.partition('        run: |')
    assert '${{' not in after, 'an expression is interpolated into the script'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='claimworkflow_'))
