#!/usr/bin/env python3
"""Executable contracts for the coverage comment privilege boundary."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _yamlread import YAMLReadError, step_scalar, step_scalars  # noqa: E402
import test_coverage_comment_workflow as commenter  # noqa: E402


_DOWNLOAD = (
    'actions/download-artifact@'
    '37930b1c2abaa49bbe596cd826c3c89aef350131'
)
_EXPECTED_STEPS = (
    ('Check for the comment artifact', 'run', None),
    ('Resolve the target pull request from the event', 'run', None),
    ('Mark missing patch coverage', 'run', None),
    ('Download the comment artifact', 'uses', _DOWNLOAD),
    ('Post or update the pull request comment', 'run', None),
)
_MARKER = '<!-- daedalus-diff-coverage -->\n'
_SENTINEL = 'PRIVILEGED_TOKEN_SENTINEL'
_HOSTILE_BODIES = {
    'bash': (
        '#!/bin/bash\n'
        'printf \'%s\' "$GH_TOKEN" > artifact-side-effect\n'
        'printf \'bash replaced body\\n\' > body.md\n'
    ),
    'javascript': (
        '#!/usr/bin/env node\n'
        "const fs = require('fs');\n"
        "fs.writeFileSync('artifact-side-effect', process.env.GH_TOKEN);\n"
        "fs.writeFileSync('body.md', 'javascript replaced body\\n');\n"
    ),
}


def _workflow():
    """Read the privileged workflow under test."""
    return (ROOT / '.github/workflows/coverage-comment.yml').read_text(
        encoding='utf-8')


def _assert_privileged_step_allowlist(workflow):
    """Require the complete decoded step topology and action identity."""
    runs = step_scalars(workflow, 'comment', 'run')
    assert runs is not None and len(runs) == 4, (
        f'unsafe privileged shell count: {runs!r}')
    uses = step_scalars(workflow, 'comment', 'uses')
    assert uses == [_DOWNLOAD], f'unsafe privileged actions: {uses!r}'
    for name, kind, identity in _EXPECTED_STEPS:
        run = step_scalar(workflow, 'comment', name, 'run')
        uses_value = step_scalar(workflow, 'comment', name, 'uses')
        if kind == 'run':
            assert run is not None and uses_value is None, (
                f'unsafe privileged step kind: {name!r}')
        else:
            assert run is None and uses_value == identity, (
                f'unsafe privileged step action: {name!r}')


def _assert_allowlist_refuses(workflow):
    """Require one hostile topology mutation to fail the allowlist."""
    try:
        _assert_privileged_step_allowlist(workflow)
    except (AssertionError, YAMLReadError):
        return
    raise AssertionError('unsafe privileged step mutation was accepted')


def _run_hostile_post(tmp, label, body):
    """Run the real post shell with an executable-looking artifact body."""
    workdir = Path(tmp) / label
    (workdir / 'bin').mkdir(parents=True)
    commenter._write_executable(  # pylint: disable=protected-access
        workdir / 'bin' / 'gh',
        commenter._GH_COMMENT_STUB)  # pylint: disable=protected-access
    state_path = workdir / 'state.json'
    calls_path = workdir / 'calls.jsonl'
    state_path.write_text('[]', encoding='utf-8')
    calls_path.write_text('', encoding='utf-8')
    (workdir / 'body.md').write_text(body, encoding='utf-8')
    (workdir / 'pr-number.txt').write_text('170\n', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'GH_TOKEN': _SENTINEL,
        'REPO': 'owner/repo',
        'PR_NUMBER': '170',
        'STUB_STATE': str(state_path),
        'STUB_CALLS': str(calls_path),
    }
    script = commenter._run_block(  # pylint: disable=protected-access
        _workflow(), 'Post or update the pull request comment')
    result = commenter._run_shell_block(  # pylint: disable=protected-access
        workdir, script, env)
    state = json.loads(state_path.read_text(encoding='utf-8'))
    return result, state, calls_path, workdir


def test_hostile_artifact_bodies_remain_inert_text(tmp):
    """Executable-looking bodies are posted exactly and never executed."""
    for label, body in _HOSTILE_BODIES.items():
        result, state, calls, workdir = _run_hostile_post(
            tmp, label, body)
        assert result.returncode == 0, (result.stdout, result.stderr)
        side_effect = workdir / 'artifact-side-effect'
        assert not side_effect.exists(), (
            f'{label} artifact executed with the privileged token')
        assert len(state) == 1, state
        assert state[0]['body'] == _MARKER + body, (
            label, state[0]['body'], body)
        writes = commenter._writes(calls)  # pylint: disable=protected-access
        assert len(writes) == 1, calls.read_text(encoding='utf-8')


def test_privileged_steps_are_an_exact_allowlist(tmp):
    """No additional shell or action can enter the privileged job."""
    del tmp
    workflow = _workflow()
    _assert_privileged_step_allowlist(workflow)
    extra_action = (
        '      - name: Unexpected action\n'
        '        uses: actions/upload-artifact@'
        '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a\n'
    )
    extra_shell = (
        '      - name: Unexpected artifact consumer\n'
        '        run: wc -c body.md\n'
    )
    changed_action = workflow.replace(_DOWNLOAD, 'owner/other@' + '0' * 40)
    for addition in (extra_action, extra_shell):
        mutated = workflow.replace('    steps:\n', '    steps:\n' + addition,
                                   1)
        assert mutated != workflow, addition
        _assert_allowlist_refuses(mutated)
    assert changed_action != workflow, _DOWNLOAD
    _assert_allowlist_refuses(changed_action)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
