#!/usr/bin/env python3
"""Execute the invariants of the secret-scan workflow and .gitleaks.toml.

The config reader refuses any shape it does not recognise: a substring scan
would accept a lookalike the scanner reads differently.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import ROOT  # noqa: E402
from _yamlsteps import workflow_mapping  # noqa: E402

EXAMPLE_UUID = '123e4567-e89b-12d3-a456-426614174000'
CONFIG = '.gitleaks.toml'
WORKFLOW = '.github/workflows/secrets.yml'


def _read(relative):
    return (ROOT / relative).read_text(encoding='utf-8')


def _toml_sections(text):
    """The config's [(header, {key: raw value})], refusing what it cannot
    decode: a repeat, a key outside a section, a line not `key = value`."""
    sections = []
    for stripped in (line.strip() for line in text.splitlines()):
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('['):
            assert stripped not in dict(sections), f'repeated {stripped!r}'
            sections.append((stripped, {}))
            continue
        key, marker, value = (part.strip() for part in stripped.partition('='))
        assert sections and marker, f'{CONFIG}: undecodable {stripped!r}'
        assert key not in sections[-1][1], f'{CONFIG}: repeated {key!r}'
        sections[-1][1][key] = value
    return sections


def _single_list_item(raw):
    """The one quoted item of a `['...']` TOML value."""
    item = re.fullmatch(r"\[\s*'([^',]*)'\s*\]", raw)
    assert item, raw
    return item.group(1)


def test_one_bounded_read_only_job_runs_on_main_prs_cron_and_dispatch(tmp):
    del tmp
    top = workflow_mapping(_read(WORKFLOW))
    assert set(top) == {
        'name', 'on', 'permissions', 'concurrency', 'jobs'}, sorted(top)
    assert top['on'] == {
        'push': {'branches': ['main']}, 'pull_request': None,
        'schedule': [{'cron': '26 5 * * *'}], 'workflow_dispatch': None,
    }, top['on']
    assert top['permissions'] == {'contents': 'read'}, top['permissions']
    job = top['jobs']['gitleaks']
    assert set(job) == {'runs-on', 'timeout-minutes', 'steps'}, job
    assert (job['runs-on'], job['timeout-minutes']) == ('ubuntu-latest', '10')
    for step in job['steps']:
        assert not {'if', 'continue-on-error'} & set(step), step


def test_the_config_extends_the_default_rules_and_admits_one_uuid(tmp):
    del tmp
    sections = _toml_sections(_read(CONFIG))
    assert [h for h, _ in sections] == ['[extend]', '[[allowlists]]']
    assert sections[0][1] == {'useDefault': 'true'}, sections[0]
    allowlist = sections[1][1]
    assert set(allowlist) == {'description', 'rules', 'regexes'}, allowlist
    assert _single_list_item(allowlist['rules']) == 'generic-api-key'
    pattern = _single_list_item(allowlist['regexes'])
    assert pattern == f'^{EXAMPLE_UUID}$', pattern
    description = allowlist['description']
    assert re.fullmatch(r'"[^"]*"', description), description
    for phrase in ('RFC 4122', 'bridge-token fixture',
                   'what would clear this entry'):
        assert phrase in description, description
