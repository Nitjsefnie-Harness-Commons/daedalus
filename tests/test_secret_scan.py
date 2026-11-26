#!/usr/bin/env python3
"""Execute the invariants of the gitleaks secret-scan gate.

The workflow is the gate and .gitleaks.toml is its scope, so the suite reads
decoded values: the workflow through the shared decoder, the config through
a reader that refuses any shape it does not recognise — a substring scan
would accept a lookalike the scanner reads differently.
"""
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _workflowrun import run_step  # noqa: E402
from _yamlsteps import workflow_mapping  # noqa: E402

EXAMPLE_UUID = '123e4567-e89b-12d3-a456-426614174000'
CONFIG = '.gitleaks.toml'
WORKFLOW = '.github/workflows/secrets.yml'
STEPS = (None, 'Refuse a shallow clone',
         'Download gitleaks and verify its digest',
         "Take the scan's config and ignore list from the trusted commit",
         'Scan the tree and the history')
TRUSTED = '[extend]\nuseDefault = true\n# trusted\n'
DEFAULT = '[extend]\nuseDefault = true\n'
TRUSTED_CASES = (
    ('pull_request', 'base', TRUSTED, 'trusted\n'),
    ('push', 'base', TRUSTED, 'trusted\n'),
    ('workflow_dispatch', '', TRUSTED, 'trusted\n'),
    ('push', '0' * 40, DEFAULT, ''),
    # Empty would read the index and HEAD the head: both the judged tree.
    ('pull_request', '', DEFAULT, ''),
    ('pull_request', 'HEAD', DEFAULT, ''),
    ('pull_request', 'base', TRUSTED + '[Extend]\nPath = "j.toml"\n', None))


def _read(relative):
    return (ROOT / relative).read_text(encoding='utf-8')


def _job():
    return workflow_mapping(_read(WORKFLOW))['jobs']['gitleaks']


def _step(index):
    step = _job()['steps'][index]
    assert step.get('name') == STEPS[index], step
    return step


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
    """The single quoted item of a `['...']` TOML value, as a string."""
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
    assert tuple(step.get('name') for step in job['steps']) == STEPS, job
    assert set(job) == {'runs-on', 'timeout-minutes', 'steps'}, job
    assert (job['runs-on'], job['timeout-minutes']) == ('ubuntu-latest', '10')
    for step in job['steps']:
        assert not {'if', 'continue-on-error'} & set(step), step


def test_a_verified_download_feeds_a_bare_scan_of_trusted_rules(tmp):
    del tmp
    download = _step(2)
    assert set(download) == {'name', 'run'}, download
    # The release choice is the workflow's business; the lines must agree.
    fetch, digest, untar = download['run'].splitlines()
    curl = shlex.split(fetch)
    # Split literal: the release scanners read the host out of source text.
    url = re.fullmatch(
        'https://' r'github\.com/gitleaks/gitleaks/releases/download/'
        r'v(?P<ver>\d+\.\d+\.\d+)/(?P<tar>gitleaks_'
        r'(?P<tarver>\d+\.\d+\.\d+)_linux_x64\.tar\.gz)', curl[-1])
    assert curl[0] == 'curl' and url, curl
    assert url.group('tarver') == url.group('ver'), 'tarball names release'
    assert {'-fsSLO', '--retry-all-errors'} <= set(curl), curl
    for option in ('--retry', '--retry-delay', '--connect-timeout',
                   '--max-time'):
        assert int(curl[curl.index(option) + 1]) > 0, (option, curl)
    assert re.fullmatch(r"echo '[0-9a-f]{64}  " + re.escape(url.group(
        'tar')) + r"' \| sha256sum -c -", digest), digest
    assert untar == f"tar xzf {url.group('tar')} gitleaks", untar
    scan = _step(4)
    assert scan['env'] == {'ATTRIBUTES': '.git/info/attributes'}, scan
    commands = scan['run'].replace('\\\n', ' ').splitlines()
    assert '||' not in scan['run'] and len(commands) == 3, commands
    # The judged tree's own exemptions are gone before the scan starts.
    assert commands[:2] == ['rm -rf .gitleaksignore',
                            "echo '* !diff' > \"$ATTRIBUTES\""], commands
    argv = shlex.split(commands[2])
    assert argv[:2] == ['./gitleaks', 'detect'], argv
    assert {'--verbose', '--redact', '--ignore-gitleaks-allow'} <= set(argv)
    ignore = argv[argv.index('--gitleaks-ignore-path') + 1]
    assert ignore.startswith('$RUNNER_TEMP/'), argv
    assert argv[argv.index('--config') + 1] == ignore + '/.gitleaks.toml'
    # Shell quoting hands gitleaks the bound walk as ONE argument.
    assert '--log-opts=--full-history --diff-filter=tuxdb HEAD' in argv, argv


def _git(repo, *args):
    return subprocess.run(
        ('git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
         '-C', str(repo)) + args, check=True, capture_output=True,
        text=True, env=_util.child_coverage('scrub')).stdout.split()


def test_rules_come_from_the_trusted_commit_never_the_judged_tree(tmp):
    if os.name == 'nt':
        _util.skip('the step runs under bash on ubuntu-latest only')
    for case, (event, sha, config, ignore) in enumerate(TRUSTED_CASES):
        repo, runner_temp = Path(tmp) / f'repo{case}', Path(tmp) / f'rt{case}'
        runner_temp.mkdir(parents=True)
        _git(tmp, 'init', '-q', str(repo))
        for toml, listed in ((TRUSTED if ignore is not None else config,
                              'trusted'), ('[[allowlists]]', 'judged')):
            (repo / '.gitleaks.toml').write_bytes(toml.encode())
            (repo / '.gitleaksignore').write_bytes(f'{listed}\n'.encode())
            _git(repo, 'add', '.')
            _git(repo, 'commit', '-qm', 'c')
        base, head = _git(repo, 'rev-parse', 'HEAD^', 'HEAD')
        _git(repo, 'update-ref', 'refs/remotes/origin/main', base)
        sha = base if sha == 'base' else sha
        # The variable this event does not read names the judged head.
        done = run_step(repo, _step(3), {
            **os.environ, 'RUNNER_TEMP': str(runner_temp), 'EVENT_NAME': event,
            'BASE_SHA': sha if event == 'pull_request' else head,
            'BEFORE_SHA': sha if event == 'push' else head})
        rules = runner_temp / 'gitleaks-trusted'
        if ignore is None:
            assert done.returncode and 'refusing to scan' in done.stderr, case
            continue
        assert done.returncode == 0, (case, done.stdout, done.stderr)
        assert [(rules / name).read_text(encoding='utf-8') for name in (
            '.gitleaks.toml', '.gitleaksignore')] == [config, ignore], case


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
