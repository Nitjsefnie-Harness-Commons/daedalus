#!/usr/bin/env python3
"""The production decoder's step `uses` values and job enumeration.

scripts/ci/cache_action_releases.py cross-checks its line scanner against
these two: every decoded cache reference must match a recognised pin.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(
    1, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))
import _util  # noqa: E402
from workflow_yaml import (  # noqa: E402
    YAMLReadError, job_names, workflow_step_items)


def test_step_items_carry_the_decoded_uses_in_every_spelling(tmp):
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - uses: owner/one@abc\n'
        "      - 'uses': 'owner/two@def'\n"
        '      - "u\\x73es": "actions\\x2fcache\\x40123"\n'
        '      - uses: >-\n'
        '          owner/four@0123\n'
        '      - run: echo harmless\n')
    items = workflow_step_items(source, 'sample')
    assert [item.uses for item in items] == [
        'owner/one@abc', 'owner/two@def', 'actions/cache@123',
        'owner/four@0123', None], items


def test_a_duplicated_uses_key_is_refused(tmp):
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - uses: owner/one@abc\n'
        '        uses: owner/two@def\n')
    try:
        workflow_step_items(source, 'sample')
    except YAMLReadError as error:
        assert str(error) == 'duplicate mapping key: uses', error
        return
    raise AssertionError('a duplicated uses key was accepted')


def test_job_names_are_the_decoded_keys_in_file_order(tmp):
    del tmp
    source = (
        'name: x\njobs:\n  second-first:\n    runs-on: a\n'
        '  "esc\\x61ped":\n    runs-on: b\n  a:\n    runs-on: c\n')
    assert job_names(source) == ['second-first', 'escaped', 'a']
    assert job_names('name: x\n') is None


def test_job_names_refuse_a_jobs_value_that_is_not_a_mapping(tmp):
    del tmp
    try:
        job_names('jobs: {}\n')
    except YAMLReadError as error:
        assert str(error) == 'jobs is not a mapping', error
        return
    raise AssertionError('a flow-mapping jobs value was accepted')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='wfyamluses_')


if __name__ == '__main__':
    raise SystemExit(main())
