#!/usr/bin/env python3
"""Refusals the workflow YAML readers must never stop making."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wffixtures import _value_error  # noqa: E402
from _yamlread import (  # noqa: E402
    job_mapping, step_scalar, step_scalars, top_level_mapping)
from _yamlsteps import complete_job_mapping, step_mappings  # noqa: E402
_job = complete_job_mapping

sys.path[:0] = [str(ROOT), str(ROOT / 'scripts' / 'ci')]

from workflow_yaml import (  # noqa: E402
    workflow_step_items as _step_items)


# A key written twice can hide a `run:` behind a decoy, so every decoder
# below refuses one. The shipped reader is the exception: it decodes only
# `name`, `id` and `uses`, so a repeated `run:` is recorded as None every
# time and no consumer sees either write. Each duplicate-key site keeps
# its own input in the decoder controls below.
STEP_JOB_HEAD = 'jobs:\n  sample:\n    steps:\n'
JOB_HEAD = 'jobs:\n  sample:\n'
DUP = 'duplicate mapping key: '
MULTILINE = 'step uses has an unsupported multiline scalar'
SCALAR = 'step uses has an unsupported scalar'


def test_a_duplicate_step_key_is_refused(_tmp):
    cases = (
        (STEP_JOB_HEAD + '      - run: echo one\n        run: echo two\n',
         'run'),
        (STEP_JOB_HEAD + '      - env:\n          F: a\n          F: b\n',
         'F'),
    )
    for source, key in cases:
        assert _value_error(
            lambda source=source: step_mappings(source, 'sample')) == DUP + key


def test_a_duplicate_job_mapping_key_is_refused(_tmp):
    cases = (
        (JOB_HEAD + '    runs-on: a\n    runs-on: b\n', 'runs-on'),
        (STEP_JOB_HEAD + '      - run: one\n        run: two\n', 'run'),
        (JOB_HEAD + '    env: {FOO: a, FOO: b}\n', 'FOO'),
    )
    for source, key in cases:
        assert _value_error(
            lambda source=source: _job(source, 'sample')) == DUP + key


def test_a_duplicate_named_mapping_key_is_refused(_tmp):
    source = (STEP_JOB_HEAD + '      - run: one\njobs:\n  other:\n'
              '    steps: []\n')
    assert _value_error(
        lambda: _step_items(source, 'sample')) == DUP + 'jobs'


def test_a_step_scalar_opening_with_an_indicator_is_refused(_tmp):
    source = STEP_JOB_HEAD + '      - uses: [owner/one@abc]\n'
    assert _value_error(lambda: _step_items(source, 'sample')) == SCALAR


def test_a_step_scalar_carrying_a_control_character_is_refused(_tmp):
    source = STEP_JOB_HEAD + '      - uses: one\ttwo\n'
    assert _value_error(lambda: _step_items(source, 'sample')) == SCALAR


def test_an_over_indented_step_scalar_is_refused(_tmp):
    source = (STEP_JOB_HEAD + '      - uses: owner/one@abc\n'
              '          continued\n')
    assert _value_error(lambda: _step_items(source, 'sample')) == MULTILINE


# The same class in `_yamlread`, the reader `_yamlsteps` is built on.  A
# combined plant of all six of its duplicate-key sites left exactly one
# surviving test noticing, and that test does not name a duplicate key,
# so these five are the only rows pinning five of the six sites.
def test_a_duplicate_top_level_mapping_key_is_refused(_tmp):
    source = JOB_HEAD + '    steps: []\njobs:\n  other:\n    steps: []\n'
    assert _value_error(
        lambda: top_level_mapping(source, 'jobs')) == DUP + 'jobs'


def test_a_duplicate_key_in_a_nested_scalar_mapping_is_refused(_tmp):
    source = JOB_HEAD + '    env:\n      F: a\n      F: b\n'
    assert _value_error(
        lambda: job_mapping(source, 'sample', 'env')) == DUP + 'F'


def test_a_step_scalar_written_twice_in_one_step_is_refused(_tmp):
    source = STEP_JOB_HEAD + '      - if: a\n        if: b\n'
    assert _value_error(
        lambda: step_scalars(source, 'sample', 'if')) == DUP + 'if'


def test_a_step_name_field_written_twice_is_refused(_tmp):
    cases = (
        (STEP_JOB_HEAD + '      - name: a\n        name: b\n'
         '        if: x\n', 'a', 'name'),
        (STEP_JOB_HEAD + '      - if: a\n        name: x\n'
         '        if:\n          b\n', 'x', 'if'),
    )
    for source, name, key in cases:
        assert _value_error(
            lambda source=source, name=name: step_scalar(
                source, 'sample', name, 'if')) == DUP + key


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='wfrefusals_')


if __name__ == '__main__':
    raise SystemExit(main())
