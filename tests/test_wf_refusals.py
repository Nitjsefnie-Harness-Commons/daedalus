#!/usr/bin/env python3
"""Refusals the workflow YAML readers must never stop making."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _yamlsteps import complete_job_mapping, step_mappings  # noqa: E402
_job = complete_job_mapping

sys.path[:0] = [str(ROOT), str(ROOT / 'scripts' / 'ci')]

from workflow_yaml import (  # noqa: E402
    workflow_step_items as _step_items)


def _value_error(call):
    """Return the refusal a call raised, or None when it returned."""
    try:
        call()
    except ValueError as error:
        return str(error)
    return None


# A key written twice is how a workflow hides a `run:` behind a decoy, so
# every decoder refuses one rather than leaving a consumer to guess.  One
# test per decoder, because a helper over all of them hides which guard
# died; the two shared-message refusals get fixtures that exclude each other.
STEP_JOB = 'jobs:\n  sample:\n    steps:\n'
JOB = 'jobs:\n  sample:\n'
DUP = 'duplicate mapping key: '
MULTILINE = 'step uses has an unsupported multiline scalar'
SCALAR = 'step uses has an unsupported scalar'


def test_a_duplicate_step_key_is_refused(_tmp):
    source = STEP_JOB + '      - run: echo one\n        run: echo two\n'
    assert _value_error(lambda: step_mappings(source, 'sample')) == DUP + 'run'


def test_a_duplicate_step_scalar_mapping_key_is_refused(_tmp):
    source = STEP_JOB + '      - env:\n          F: a\n          F: b\n'
    assert _value_error(lambda: step_mappings(source, 'sample')) == DUP + 'F'


def test_a_duplicate_job_mapping_key_is_refused(_tmp):
    source = JOB + '    runs-on: a\n    runs-on: b\n'
    assert _value_error(lambda: _job(source, 'sample')) == DUP + 'runs-on'


def test_a_duplicate_sequence_item_mapping_key_is_refused(_tmp):
    source = STEP_JOB + '      - run: one\n        run: two\n'
    assert _value_error(lambda: _job(source, 'sample')) == DUP + 'run'


def test_a_duplicate_flow_mapping_key_is_refused(_tmp):
    source = JOB + '    env: {FOO: a, FOO: b}\n'
    assert _value_error(lambda: _job(source, 'sample')) == DUP + 'FOO'


def test_a_duplicate_named_mapping_key_is_refused(_tmp):
    source = STEP_JOB + '      - run: one\njobs:\n  other:\n    steps: []\n'
    assert _value_error(
        lambda: _step_items(source, 'sample')) == DUP + 'jobs'


def test_a_duplicate_decoded_step_key_is_refused(_tmp):
    source = STEP_JOB + '      - uses: owner/a@1\n        uses: owner/b@2\n'
    assert _value_error(lambda: _step_items(source, 'sample')) == DUP + 'uses'


def test_a_step_scalar_that_closes_no_quote_is_refused(_tmp):
    source = STEP_JOB + '      - uses: "abc\n      - run: echo "def\n'
    assert _value_error(lambda: _step_items(source, 'sample')) == MULTILINE


def test_a_step_scalar_opening_with_an_indicator_is_refused(_tmp):
    source = STEP_JOB + '      - uses: [owner/one@abc]\n'
    assert _value_error(lambda: _step_items(source, 'sample')) == SCALAR


def test_a_step_scalar_carrying_a_control_character_is_refused(_tmp):
    source = STEP_JOB + '      - uses: one\ttwo\n'
    assert _value_error(lambda: _step_items(source, 'sample')) == SCALAR


def test_an_over_indented_step_scalar_is_refused(_tmp):
    source = STEP_JOB + '      - uses: owner/one@abc\n          continued\n'
    assert _value_error(lambda: _step_items(source, 'sample')) == MULTILINE


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='wfrefusals_')


if __name__ == '__main__':
    raise SystemExit(main())
