#!/usr/bin/env python3
"""The speed gate negotiates its flags with the head scripts it runs.

The `timed` job takes its TEXT from the pull request's merge ref and its
SCRIPTS from `head/`, so a flag the text carries can be one the head's parser
refuses, and the cell dies on a branch whose own changes had nothing to do
with speed. The steps therefore ask each script what it takes and pass only
what it advertises, refusing closed when the answer is unreadable.

Every fixture here is a real argparse program, not a double that always
exits 0: a double that cannot fail proves nothing about what the step does
with a flag the parser does not know.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _speedharness import (  # noqa: E402
    run_workflow_script, workflow_script,
)
from _wfgraph import _job_section, _tests_yml  # noqa: E402

_COMPARATOR = 'head/scripts/ci/compare_durations.py'
_INSTRUMENT = 'head/scripts/ci/time_tests.py'
_PROBE = 'Probe the head scripts for options'

# The comparator exactly as it stood before 64aa98dd added `--ratio-file`.
# Same parser, same required pair, no ratio file — which is what a head that
# predates that commit actually runs against this workflow. Its description
# names no option, because the real script's help named none: the probe
# reduces the whole rendered help, prose included.
_OLD_COMPARATOR = '''#!/usr/bin/env python3
"""A comparator as it stood before a deciding ratio was recorded."""
import argparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', nargs='+', required=True)
    parser.add_argument('--head', nargs='+', required=True)
    parser.add_argument('--max-regression', type=float, default=0.30)
    parser.add_argument('--base-label', default='baseline')
    parser.add_argument('--summary-file')
    parser.add_argument('--accept')
    parser.add_argument('--require-measurements', action='store_true')
    args = parser.parse_args()
    print('compared {} against {}'.format(args.base, args.head))


main()
'''

# The same comparator with one required option left out, for the refusal the
# required path is there to produce.
_HEADLESS_COMPARATOR = _OLD_COMPARATOR.replace(
    "    parser.add_argument('--head', nargs='+', required=True)\n", '')

# A timing instrument reduced to what it took before 44d6ad0e added the
# selection options. It records what it parsed, so a test can prove the flag
# reached the parser rather than only the command line.
_INSTRUMENT_TEMPLATE = '''#!/usr/bin/env python3
"""A timing instrument with a chosen argparse surface."""
import argparse
import json
import os


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--out', required=True)
{extra}    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    record = {{'tree': args.tree, 'only': getattr(args, 'only', None),
              'except': getattr(args, 'except_globs', None)}}
    with open(os.path.join(args.out, 'durations.json'), 'w',
              encoding='utf-8') as handle:
        json.dump(record, handle)
    print('timed {{}}'.format(args.tree))


main()
'''

_SELECTION_ARGS = (
    "    parser.add_argument('--only', nargs='+', metavar='GLOB')\n"
    "    parser.add_argument('--except', nargs='+', dest='except_globs',\n"
    "                        metavar='GLOB')\n"
)

# One suite per report file, timed on both sides, so the real comparator has a
# shared set to sum and a ratio to record.
_DURATIONS = {'tests': {'test_one_slow': 1.0, 'test_two_slow': 2.0}}


def _instrument(selection):
    """A real instrument carrying the selection options, or none."""
    return _INSTRUMENT_TEMPLATE.format(
        extra=_SELECTION_ARGS if selection else '')


def _workdir(tmp, name):
    """One cell's working directory, with the report rounds beside it."""
    workdir = Path(tmp) / name
    for round_ in (1, 2):
        for side in ('base', 'head'):
            reports = workdir / 'reports' / f'{side}-{round_}'
            reports.mkdir(parents=True)
            (reports / 'test_one.json').write_text(
                json.dumps(_DURATIONS), encoding='utf-8')
    return workdir


def _scripts(workdir):
    """The head checkout's own `scripts/ci` directory."""
    return workdir / 'head' / 'scripts' / 'ci'


def _install_comparator(workdir, source=None):
    """The real comparator and the siblings it imports, or a given source.

    The instrument is installed beside it because the probe step reads both
    and refuses the cell when either one cannot answer.
    """
    target = _scripts(workdir)
    target.mkdir(parents=True, exist_ok=True)
    for name in ('compare_durations.py', 'zeroed_suites.py',
                 'speed_summary.py', 'accepted_speed_changes.json',
                 'time_tests.py'):
        _copy(ROOT / 'scripts' / 'ci' / name, target / name)
    if source is not None:
        (target / 'compare_durations.py').write_text(source,
                                                     encoding='utf-8')
    return target


def _copy(source, target):
    """Copy a repository file into the workdir the workflow steps run in."""
    target.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')


def _install_instrument(workdir, source):
    """Put a given instrument where the timing step looks for it."""
    target = _scripts(workdir)
    target.mkdir(parents=True, exist_ok=True)
    (target / 'time_tests.py').write_text(source, encoding='utf-8')


def _step(name):
    return workflow_script(_tests_yml(), 'timed', name)


def _probe(workdir):
    """Run the probe step over this workdir's head checkout."""
    output = workdir / 'probe-output'
    output.write_text('', encoding='utf-8')
    result = run_workflow_script(workdir, _step(_PROBE),
                                 {'GITHUB_OUTPUT': str(output)})
    values = {}
    for line in output.read_text(encoding='utf-8').splitlines():
        key, _, value = line.partition('=')
        values[key] = value
    return result, values


def _summary(workdir):
    """The step summary GitHub always provides, and a step may only append."""
    summary = workdir / 'summary.md'
    summary.touch()
    return summary


def _run_compare(workdir, options):
    """Run the real Compare step with the token sets the probe produced."""
    summary = _summary(workdir)
    result = run_workflow_script(workdir, _step('Compare'), {
        'BASE_LABEL': 'v1.2.3',
        'COMPARE_OPTIONS': options['compare_options'],
        'MAX_REGRESSION': '0.30',
        'GITHUB_STEP_SUMMARY': str(summary),
    })
    return result, summary.read_text(encoding='utf-8')


def _run_timing(workdir, options, suites='test_speed*.py'):
    """Run the real timing step over one round of both sides."""
    step = _step('Run both suites, interleaved')
    summary = _summary(workdir)
    result = run_workflow_script(workdir, step, {
        'GITHUB_WORKSPACE': str(workdir),
        'ROUNDS': '1',
        'SUITES_ONLY': suites,
        'SUITES_EXCEPT': '',
        'TIME_OPTIONS': options['time_options'],
        'GITHUB_STEP_SUMMARY': str(summary)})
    return result, summary.read_text(encoding='utf-8')


def test_a_head_without_the_ratio_flag_runs_and_says_so(tmp):
    """The planted defect: a comparator this workflow predates.

    `--ratio-file` is passed only when the head's own help lists it, so a
    head from before 64aa98dd measures instead of dying at argparse.
    """
    workdir = _workdir(tmp, 'old-comparator')
    _install_comparator(workdir, _OLD_COMPARATOR)
    probe, options = _probe(workdir)
    assert probe.returncode == 0, (probe.stdout, probe.stderr)
    result, summary = _run_compare(workdir, options)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not (workdir / 'reports' / 'ratio.txt').exists(), (
        'a comparator that cannot write one must not leave it behind')
    assert '::warning::' in result.stdout, result.stdout
    assert '--ratio-file' in result.stdout, result.stdout
    assert '--ratio-file' in summary, summary


def test_the_current_comparator_still_gets_every_option(tmp):
    """The false-green guard: negotiation must not drop what is supported."""
    workdir = _workdir(tmp, 'current-comparator')
    _install_comparator(workdir)
    probe, options = _probe(workdir)
    assert probe.returncode == 0, (probe.stdout, probe.stderr)
    result, summary = _run_compare(workdir, options)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (workdir / 'reports' / 'ratio.txt').exists(), (
        'the real comparator was not given --ratio-file', result.stdout)
    assert '::warning::' not in result.stdout, result.stdout
    assert 'did not list' not in summary, summary


def test_a_head_with_no_comparator_refuses_rather_than_skips(tmp):
    """A checkout the scripts cannot come from is a refusal, not a skip."""
    workdir = _workdir(tmp, 'no-comparator')
    probe, _ = _probe(workdir)
    assert probe.returncode == 1, (probe.stdout, probe.stderr)
    assert _COMPARATOR in probe.stdout, probe.stdout
    assert 'Traceback' not in probe.stdout + probe.stderr, (
        probe.stdout, probe.stderr)


def test_the_timing_step_drops_and_keeps_its_selection_flags(tmp):
    """Both directions of the negotiation, read off the parsed values.

    The step dies at `MISSING: reports/... has no durations` when the head's
    instrument cannot parse the selection, so a stale head and a current one
    are the two halves of one assertion.
    """
    workdir = _workdir(tmp, 'old-instrument')
    _install_comparator(workdir)
    _install_instrument(workdir, _instrument(False))
    probe, options = _probe(workdir)
    assert probe.returncode == 0, (probe.stdout, probe.stderr)
    result, summary = _run_timing(workdir, options)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert 'unrecognized arguments' not in result.stderr, result.stderr
    assert 'no durations' not in result.stderr, result.stderr
    assert '--only' in result.stdout and '::warning::' in result.stdout, (
        result.stdout)
    assert '--only' in summary, summary
    recorded = json.loads(
        (workdir / 'reports' / 'head-1' / 'durations.json').read_text(
            encoding='utf-8'))
    assert recorded['only'] is None, recorded

    workdir = _workdir(tmp, 'current-instrument')
    _install_comparator(workdir)
    _install_instrument(workdir, _instrument(True))
    probe, options = _probe(workdir)
    assert probe.returncode == 0, (probe.stdout, probe.stderr)
    result, summary = _run_timing(workdir, options)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert '::warning::' not in result.stdout, result.stdout
    assert 'did not list' not in summary, summary
    recorded = json.loads(
        (workdir / 'reports' / 'head-1' / 'durations.json').read_text(
            encoding='utf-8'))
    assert recorded['only'] == ['test_speed*.py'], recorded


def test_every_option_the_comparator_declares_reaches_the_command(tmp):
    """The invariant that stops the negotiation rotting in silence.

    An option added to the script but never wired into the step would
    otherwise degrade quietly, forever. `--help` is excluded because
    argparse adds it implicitly and the step must never pass it.
    """
    del tmp
    declared = _declared_comparator_options()
    assert declared, 'no add_argument calls were read from the comparator'
    compare = '\n'.join(_job_section(_tests_yml(), 'timed')).partition(
        '- name: Compare\n')[2]
    compare = compare.partition('- name:')[0]
    wired = set(re.findall(r'^\s*add (--[a-z][a-z0-9-]*)', compare,
                           re.MULTILINE))
    assert declared - {'--help'} == wired, (declared, wired)


def test_the_help_extraction_reproduces_the_parser_at_every_width(tmp):
    """The probe's one assumption, checked rather than assumed.

    argparse wraps its usage line to the terminal, so a regex over a
    rendered help could read a truncated option at one width and the whole
    of it at another.
    """
    del tmp
    comparator = ROOT / 'scripts' / 'ci' / 'compare_durations.py'
    # `--help` is the one token the parser adds implicitly rather than
    # declares. `-h` is absent because argparse brackets it in the usage
    # line and the probe's regex only matches a token the whitespace
    # introduces — and no caller ever passes it.
    expected = _declared_comparator_options() | {'--help'}
    for columns in ('40', '60', '80', '200'):
        environment = dict(os.environ, COLUMNS=columns)
        extracted = _help_tokens(comparator, environment)
        assert extracted == expected, (columns, sorted(extracted))


def test_a_required_option_the_head_does_not_offer_stops_the_step(tmp):
    """The required path is a refusal; the drop path must not swallow it."""
    workdir = _workdir(tmp, 'headless-comparator')
    _install_comparator(workdir, _HEADLESS_COMPARATOR)
    probe, options = _probe(workdir)
    assert probe.returncode == 0, (probe.stdout, probe.stderr)
    result, _summary = _run_compare(workdir, options)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert '--head' in result.stdout, result.stdout


def _declared_comparator_options():
    """The comparator's options, read from its own parser declarations."""
    source = (ROOT / 'scripts' / 'ci' / 'compare_durations.py').read_text(
        encoding='utf-8')
    return set(re.findall(r"add_argument\(\s*'(--[a-z][a-z0-9-]*)'", source))


def _help_tokens(script, environment):
    """The option tokens a rendered `--help` carries, at one width."""
    completed = subprocess.run(
        ['python3', str(script), '--help'], capture_output=True,
        check=True, timeout=120, env=environment)
    # The same reduction the probe's shell does: match with the leading
    # whitespace, then strip it, so a token cannot arrive padded.
    return {token.replace(' ', '')
            for token in re.findall(r'(?:^|\s)-{1,2}[a-z][a-z0-9-]+',
                                    completed.stdout.decode('utf-8'))}


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='speedgateoptions_')


if __name__ == '__main__':
    raise SystemExit(main())
