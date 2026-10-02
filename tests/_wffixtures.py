"""Fixtures shared by the planted-workflow suites."""
import os
from pathlib import Path

from _repo import ROOT
from _yamlscalar import YAMLReadError
from _wfgraph import _tests_yml

# Verbatim blocks from .github/workflows/tests.yml: the suites job's needs,
# and the changes job's outputs.
BLOCK_NEEDS = (
    '    needs:\n'
    '      - changes\n'
    '      - pycodestyle\n'
    '      - pylint\n'
    '      - pyright\n'
    '      - eslint\n'
    '      - actionlint\n')
BLOCK_OUTPUTS = (
    '    outputs:\n'
    '      matrix: ${{ steps.classify.outputs.matrix }}\n'
    '      docs_only: ${{ steps.classify.outputs.docs_only }}\n'
    '      workflows: ${{ steps.classify.outputs.workflows }}\n')


def _real(tmp, source, name='tests.yml'):
    path = os.path.join(tmp, name)
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(source)
    with open(path, encoding='utf-8', newline='') as handle:
        return handle.read()


def _replaced(old, new, name='tests.yml'):
    """Return a shipped workflow with one real block swapped for a rewrite."""
    workflow = _tests_yml() if name == 'tests.yml' else (
        ROOT / '.github' / 'workflows' / name).read_text(encoding='utf-8')
    assert old in workflow, old
    mutated = workflow.replace(old, new, 1)
    assert mutated != workflow
    return mutated


def _refuses(call, *args, contains=None):
    """Return the message from the refusal `call` must raise."""
    try:
        call(*args)
    except (AssertionError, ValueError, YAMLReadError) as error:
        message = f'{type(error).__name__}: {error}'
        if contains is not None:
            assert contains in message, message
        return message
    raise AssertionError(f'{call.__name__} accepted the planted defect')


def _probe_workflow(tmp, name, source):
    root = Path(tmp) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / 'probe.yml').write_text(source, encoding='utf-8')
    return root
