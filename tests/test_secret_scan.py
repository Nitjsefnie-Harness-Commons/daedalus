#!/usr/bin/env python3
"""Execute the invariants of the gitleaks secret-scan gate.

The workflow is the gate and .gitleaks.toml is its scope, so the suite reads
decoded values: the workflow through the shared decoder, the config through
a reader that refuses any shape it does not recognise — a substring scan
would accept a lookalike the scanner reads differently.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _yamlsteps import workflow_mapping  # noqa: E402

CHECKOUT_SHA = '3d3c42e5aac5ba805825da76410c181273ba90b1'
GITLEAKS_VERSION = '8.30.1'
TARBALL = 'gitleaks_8.30.1_linux_x64.tar.gz'
DIGEST = '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb'
EXAMPLE_UUID = '123e4567-e89b-12d3-a456-426614174000'
CONFIG = '.gitleaks.toml'

WORKFLOW = '.github/workflows/secrets.yml'


def _read(relative):
    path = ROOT / relative
    assert path.is_file(), f'{relative} does not exist yet'
    return path.read_text(encoding='utf-8')


def _decoded_workflow():
    return workflow_mapping(_read(WORKFLOW))


def _download_command():
    """The download step's expected shell lines, exactly."""
    return [
        'curl --connect-timeout 5 --max-time 120 -fsSLO '
        'https://github.com/gitleaks/gitleaks/releases/download/'
        f'v{GITLEAKS_VERSION}/{TARBALL}',
        f"echo '{DIGEST}  {TARBALL}' | sha256sum -c -",
        f'tar xzf {TARBALL} gitleaks',
    ]


def _toml_sections(text):
    """The config's sections as [(header, {key: raw value})].

    Comments and blank lines carry no decoded value and are skipped; a
    duplicate key inside a section, a key outside any section, or a line
    that is not `key = value` is refused rather than guessed at.
    """
    sections = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('['):
            assert stripped not in dict(sections), (
                f'{CONFIG} declares {stripped!r} twice')
            sections.append((stripped, {}))
            continue
        assert sections, (
            f'{CONFIG} has a key outside any section: {stripped!r}')
        key, marker, value = stripped.partition('=')
        assert marker, (
            f'{CONFIG} line is not a key = value pair: {stripped!r}')
        entries = sections[-1][1]
        assert key.strip() not in entries, (
            f'{CONFIG} declares {key.strip()!r} twice in one section')
        entries[key.strip()] = value.strip()
    return sections


def _single_string_list(raw, owner):
    """The one quoted item of a `['...']` TOML value."""
    assert raw.startswith('[') and raw.endswith(']'), (owner, raw)
    inner = raw[1:-1].strip()
    assert inner and ',' not in inner, (owner, raw)
    assert len(inner) >= 2 and inner[0] == "'" and inner[-1] == "'", (
        owner, raw)
    return inner[1:-1]


def _double_quoted(raw, owner):
    """The body of a double-quoted TOML string."""
    assert len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"', (owner, raw)
    body = raw[1:-1]
    assert '"' not in body, (owner, raw)
    return body


def test_triggers_cover_main_pushes_prs_the_cron_and_manual_runs(tmp):
    del tmp
    on = _decoded_workflow()['on']
    assert on == {
        'push': {'branches': ['main']},
        'pull_request': None,
        'schedule': [{'cron': '26 5 * * *'}],
        'workflow_dispatch': None,
    }, on


def test_permissions_are_exactly_read_only(tmp):
    del tmp
    permissions = _decoded_workflow()['permissions']
    assert permissions == {'contents': 'read'}, (
        f'unsafe decoded permissions: {permissions!r}')


def test_concurrency_is_keyed_on_the_ref_and_cancelling(tmp):
    del tmp
    concurrency = _decoded_workflow()['concurrency']
    assert concurrency == {
        'group': 'secrets-${{ github.ref }}',
        'cancel-in-progress': 'true',
    }, concurrency


def test_the_checkout_reads_full_history_without_credentials(tmp):
    del tmp
    steps = _decoded_workflow()['jobs']['gitleaks']['steps']
    checkout = steps[0]
    assert checkout['uses'] == f'actions/checkout@{CHECKOUT_SHA}', checkout
    assert checkout['with'] == {
        'fetch-depth': '0', 'persist-credentials': 'false'}, checkout


def test_the_binary_is_downloaded_from_github_and_digest_verified(tmp):
    del tmp
    steps = _decoded_workflow()['jobs']['gitleaks']['steps']
    download = steps[1]
    assert download['run'].splitlines() == _download_command(), download
    url = _download_command()[0].split()[-1]
    assert url.startswith('https://github.com/'), url


def test_the_scan_step_is_the_bare_gate(tmp):
    del tmp
    steps = _decoded_workflow()['jobs']['gitleaks']['steps']
    scan = steps[2]
    assert scan['run'] == './gitleaks detect --redact --config .gitleaks.toml'
    assert 'if' not in scan and 'continue-on-error' not in scan, scan
    assert '|| true' not in scan['run'], scan


def test_no_step_and_no_job_key_escapes_the_gate(tmp):
    del tmp
    job = _decoded_workflow()['jobs']['gitleaks']
    steps = job['steps']
    assert len(steps) == 3, steps
    assert [step.get('name') or step.get('uses', '').split('@')[0]
            for step in steps] == [
        'actions/checkout',
        'Download gitleaks and verify its digest',
        'Scan the tree and the history',
    ], steps
    assert set(job) == {'runs-on', 'timeout-minutes', 'steps'}, job


def test_the_job_bounds_itself_with_a_timeout(tmp):
    del tmp
    job = _decoded_workflow()['jobs']['gitleaks']
    assert job['runs-on'] == 'ubuntu-latest', job
    assert job['timeout-minutes'] == '10', job


def test_the_config_extends_the_default_rules(tmp):
    del tmp
    sections = _toml_sections(_read(CONFIG))
    assert [header for header, _ in sections] == [
        '[extend]', '[[allowlists]]'], sections
    assert sections[0][1] == {'useDefault': 'true'}, sections[0]
    assert sections[1][1].get('useDefault') is None, sections[1]


def test_the_allowlist_admits_exactly_the_example_uuid(tmp):
    del tmp
    allowlist = _toml_sections(_read(CONFIG))[1][1]
    assert set(allowlist) == {'description', 'rules', 'regexes'}, allowlist
    assert _single_string_list(allowlist['rules'], 'rules') == (
        'generic-api-key'), allowlist['rules']
    pattern = _single_string_list(allowlist['regexes'], 'regexes')
    assert pattern == f'^{EXAMPLE_UUID}$', pattern
    assert re.fullmatch(pattern, EXAMPLE_UUID), pattern
    for rejected in (EXAMPLE_UUID.upper(), 'x' + EXAMPLE_UUID,
                     EXAMPLE_UUID + 'x', f"'{EXAMPLE_UUID}'"):
        assert re.fullmatch(pattern, rejected) is None, (pattern, rejected)
    description = _double_quoted(allowlist['description'], 'description')
    assert 'RFC 4122' in description, description
    assert 'bridge-token fixture' in description, description
    assert 'what would clear this entry' in description, description


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='secretscan_')


if __name__ == '__main__':
    raise SystemExit(main())
