#!/usr/bin/env python3
"""The timed-timings workflow: the shapes a green board cannot see.

`timed-timings.yml` had no shape test of its own, and the consequence
was a job that went green every night downloading nothing: the cell
artifacts were selected with `gh run download -n`, whose `--name` matches
artifact names EXACTLY, where `-p`/`--pattern` is the glob. `-n
'speed-durations-*'` matched nothing, exited 1, and the walk stepped
over every candidate -- so `steps.download.outputs.count` stayed `0`,
the refresher and both suites were skipped, the file never updated, and
nothing reported it.

So this suite does what a `grep` cannot: it reads the download step's
command and DECODES the flag that selected artifacts, and it EXECUTES
the step's own `run:` block under `bash -e` against a `gh` double that
implements both flags as `gh run download --help` documents them and
lays artifacts out the way the CLI does. The layout the step's
`reference.json` probe reads is reached through the command the workflow
actually runs.

Also here: the job's own `permissions` block, which replaced the
workflow-level one and so took `contents: read` with it, and the timed
matrix's `|| '[]'` default beside the `except` key that never existed.
"""
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT, git_index  # noqa: E402
from _speedharness import (  # noqa: E402
    run_workflow_script, workflow_script, write_executable)
from _wfgraph import _job_section  # noqa: E402
from _yamlread import job_mapping, step_scalars  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

WORKFLOWS = ROOT / '.github' / 'workflows'
# `gh run download --help`: the two selector flags, by what they MATCH.
# The pin is on the meaning, not on the spelling, so renaming the short
# flag is not a finding and swapping one for the other is.
GLOB_FLAGS = ('-p', '--pattern')
EXACT_FLAGS = ('-n', '--name')
_SELECTOR_FLAGS = GLOB_FLAGS + EXACT_FLAGS
# The artifact name `tests.yml`'s timed job gives one cell, and the file
# the refresher reads each cell's reading through.
_ARTIFACT_PREFIX = 'speed-durations-'
_REFERENCE_FILE = 'reference.json'
_CELLS = ('cell-01', 'cell-02', 'cell-03')
# The three cells of one measured run, as the walk sees them.
_ARTIFACTS = tuple(f'{_ARTIFACT_PREFIX}{cell}' for cell in _CELLS)

# The `gh` double, as the module the PATH-leading launcher runs. It
# answers only the calls these steps make, and answers them the way the
# CLI documents: `--name` is an exact list, `--pattern` a glob, and a
# lone selected artifact is extracted into the target directory ITSELF
# while more than one each get a directory named after them.
_GH_DOUBLE = '''\
"""A `gh` double for the calls the timed-timings steps make.

Both answers that reach the shell go out through `sys.stdout.buffer`:
`sys.stdout` is a TEXT stream, and on Windows it translates every `\\n` it
writes to `os.linesep`, so a text write would put a carriage return on
every line of a list the step then reads with `read -r` -- which strips
the newline and keeps the CR. Bytes in, bytes out: the shell sees exactly
what `gh` emits.
"""
import fnmatch
import os
import shutil
import sys
from pathlib import Path

GLOB = ('-p', '--pattern')
EXACT = ('-n', '--name')


def _api(root, argv):
    for argument in argv:
        marker = '/actions/runs/'
        if marker in argument and argument.endswith('/artifacts'):
            run = argument.split(marker)[1].split('/')[0]
            if (root / (run + '.fails')).exists():
                sys.stderr.write('gh: HTTP 500\\n')
                return 1
            listing = root / (run + '.names')
            if listing.exists():
                sys.stdout.buffer.write(listing.read_bytes())
            return 0
        if '/actions/workflows/' in argument:
            sys.stdout.buffer.write(
                os.environ.get('GH_DOUBLE_WORKFLOW_RUNS', '').encode('utf-8'))
            return 0
    sys.stderr.write('gh: the double answers no call in %r\\n' % (argv,))
    return 1


def _download(root, argv):
    rest = list(argv[2:])
    run = ''
    if rest and not rest[0].startswith('-'):
        run = rest.pop(0)
    selector, target = None, '.'
    while rest:
        flag = rest.pop(0)
        if flag in GLOB + EXACT:
            selector = (flag, rest.pop(0))
        elif flag in ('-D', '--dir'):
            target = rest.pop(0)
        elif not flag.startswith('-'):
            sys.stderr.write('gh: unexpected argument %r\\n' % flag)
            return 1
    if selector is None:
        sys.stderr.write('gh: the double answers a selected download only\\n')
        return 1
    flag, pattern = selector
    listing = root / (run + '.names')
    names = listing.read_text(encoding='utf-8').split() \\
        if listing.exists() else []
    if flag in EXACT:
        chosen = [name for name in names if name == pattern]
    else:
        chosen = [name for name in names
                  if fnmatch.fnmatchcase(name, pattern)]
    if not chosen:
        sys.stderr.write(
            'no artifact matches any of the names or patterns provided\\n')
        return 1
    for name in chosen:
        destination = Path(target) if len(chosen) == 1 \\
            else Path(target) / name
        source = root / run / name
        if source.is_dir():
            shutil.copytree(str(source), str(destination), dirs_exist_ok=True)
        else:
            destination.mkdir(parents=True, exist_ok=True)
    return 0


def main(argv):
    root = Path(os.environ['GH_DOUBLE_FIXTURES'])
    if argv[:1] == ['api']:
        return _api(root, argv)
    if argv[:2] == ['run', 'download']:
        return _download(root, argv)
    sys.stderr.write('gh: the double answers no call in %r\\n' % (argv,))
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
'''


def _timed_workflow():
    return (WORKFLOWS / 'timed-timings.yml').read_text(encoding='utf-8')


def _tests_workflow():
    return (WORKFLOWS / 'tests.yml').read_text(encoding='utf-8')


def _download_step(source=None):
    return workflow_script(source or _timed_workflow(), 'refresh',
                           'Download the cell artifacts')


def _install_gh_double(workdir):
    """One PATH-leading `gh` that IS the double, and where it lives."""
    bindir = Path(workdir) / 'bin'
    bindir.mkdir(parents=True, exist_ok=True)
    module = bindir / 'gh_double.py'
    module.write_text(_GH_DOUBLE, encoding='utf-8')
    write_executable(bindir / 'gh',
                     '#!/bin/sh\n'
                     f'exec "{sys.executable}" "{module}" "$@"\n')
    return bindir


def _artifacts(root, run_id, cells=_ARTIFACTS, reference=True,
               api_fails=False):
    """One run's fixtures: the artifact names, and what each one holds.

    `reference=False` is a run from before the cells measured the
    reference workload: a complete cell set in which no cell carries a
    reading, which is the shape the walk steps over.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    # The `.names` file IS the bytes the double relays as `gh --jq
    # '.artifacts[].name'` output, so it is written as bytes: text mode
    # would put a CR on every line on Windows and the double relays it
    # verbatim.
    (root / f'{run_id}.names').write_bytes(
        ''.join(f'{name}\n' for name in cells).encode('utf-8'))
    if api_fails:
        (root / f'{run_id}.fails').write_text('', encoding='utf-8')
    for name in cells:
        artifact = root / str(run_id) / name
        for round_name in ('head-1', 'head-2'):
            report = artifact / round_name / 'test_a.json'
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text('{"tests": {"test_a": 4.0}, "outcomes": {}}',
                              encoding='utf-8')
        if reference:
            (artifact / _REFERENCE_FILE).write_text(
                '{"seconds": 2.0, "iterations": 16}', encoding='utf-8')
    return root


def _write_run_ids(path, run_ids):
    """The run-ids list the walk's `while read` reads, as BYTES.

    `gh api --jq '.id'` writes `\\n` and nothing else, on every platform.
    `Path.write_text` opens in TEXT mode, which translates that `\\n` to
    `os.linesep` -- `\\r\\n` on Windows -- and `read` strips the newline but
    NOT the carriage return, so every id arrives as `500\\r`, every
    `runs/500\\r/...` misses, and the step reports an honest `count=0`.
    Binary mode cannot translate what is not a newline, so the bytes the
    shell reads are the bytes `gh` emits on any host.
    """
    Path(path).write_bytes(
        ''.join(f'{run_id}\n' for run_id in run_ids).encode('utf-8'))


def _step_environment(workdir, **names):
    temp = Path(workdir) / 'runner-temp'
    temp.mkdir(parents=True, exist_ok=True)
    summary = Path(workdir) / 'summary.md'
    output = Path(workdir) / 'github-output'
    for path in (summary, output):
        path.write_text('', encoding='utf-8')
    _install_gh_double(workdir)
    return {
        'RUNNER_TEMP': str(temp),
        'SAMPLE': names.pop('SAMPLE', '3'),
        'REPOSITORY': 'example/example',
        'GH_DOUBLE_FIXTURES': str(names.pop(
            'GH_DOUBLE_FIXTURES', Path(workdir) / 'fixtures')),
        'GITHUB_STEP_SUMMARY': str(summary),
        'GITHUB_OUTPUT': str(output),
        **names,
    }


def _walk(workdir, run_ids, fixtures):
    """The download step's own run block, under GitHub's `bash -e`.

    Returns the completed process and what the step WROTE, so a test
    reads `$GITHUB_OUTPUT` and the summary rather than the log.
    """
    environment = _step_environment(workdir, GH_DOUBLE_FIXTURES=str(fixtures))
    _write_run_ids(Path(workdir) / 'runner-temp' / 'run-ids', run_ids)
    result = run_workflow_script(workdir, _download_step(), environment)
    written = Path(workdir) / 'github-output'
    summary = Path(workdir) / 'summary.md'
    return result, written.read_text(encoding='utf-8'), \
        summary.read_text(encoding='utf-8')


def _shell_value(word):
    """One shell word as the shell hands it to the command."""
    if len(word) > 1 and word[0] == word[-1] and word[0] in "'\"":
        return word[1:-1]
    return word


def _posix_relative(path, root):
    """`path` under `root`, in the spelling the step's own shell uses.

    The step and the probe that reads its output are POSIX shell, and
    the layout is a set of `/`-separated names -- so the forward slash
    is the correct spelling, not a platform-neutral one to be tolerated.
    `str()` renders the HOST's separator instead, which on Windows spells
    each entry `cell-01\\reference.json`: a spelling the step never
    writes, so the assertion was comparing two renderings rather than
    two layouts and reddened on the Windows legs. `as_posix()` is the
    one rendering that is the step's on every host.
    """
    return path.relative_to(root).as_posix()


def _download_selection(script):
    """`(flag, pattern, target)` decoded from the step's download command.

    The command is read out of the workflow and its selector flag is
    resolved to what the CLI MEANS by it: a test asserting only that a
    `-p` is present would pass on a command that selected nothing. The
    pattern and the target come back as shell values, so the layout
    assertions compare them with the path the step probes rather than
    with their quoting.
    """
    commands = re.findall(r'gh run download[^\n]*\\\n[^\n]*', script)
    assert len(commands) == 1, commands
    words = commands[0].replace('\\\n', ' ').split()
    flags = [word for word in words if word in _SELECTOR_FLAGS]
    assert len(flags) == 1, commands
    return (flags[0],
            _shell_value(words[words.index(flags[0]) + 1]),
            _shell_value(words[words.index('-D') + 1]))


def test_the_download_selects_the_cell_artifacts_with_the_glob_flag(tmp):
    """The selector's MEANING is a glob, and the pattern covers a cell.

    `--name` matches an artifact name EXACTLY and `--pattern` matches a
    glob, so the one question here is which of the two the workflow
    selected with. The pattern is then held to both readings: as a glob
    it must match a generated cell artifact name, and as an exact name it
    must match none -- a pattern that were also a real artifact name
    would not be what the walk is selecting.
    """
    del tmp
    flag, pattern, _target = _download_selection(_download_step())
    assert flag in GLOB_FLAGS, (
        f'{flag!r} selects artifacts by EXACT name: -n and --name match '
        f'one name each, and {pattern!r} is a glob, not a name. The cell '
        f'artifacts are generated, so they are only knowable as a pattern')
    assert flag not in EXACT_FLAGS, flag
    for name in _ARTIFACTS:
        assert fnmatch.fnmatchcase(name, pattern), (name, pattern)
        assert name != pattern, (name, pattern)


def test_the_probe_reads_the_layout_the_command_produces(tmp):
    """The path the walk probes is `<target>/<artifact>/reference.json`.

    The two halves of the step have to agree on where a cell's reading
    lands, and neither half can see the other: `gh run download -D` puts
    a directory named after each artifact under the target, and the cell
    loop looks for `speed-durations-$cell`. This is that agreement, read
    out of the step rather than restated beside it.
    """
    del tmp
    _flag, _pattern, target = _download_selection(_download_step())
    script = _download_step()
    # The probe is the command's target, then the artifact name the walk
    # derived from the cell, then the reading's own name.
    assert target == 'runs/$id', target
    probe = f'{target}/{_ARTIFACT_PREFIX}$cell/{_REFERENCE_FILE}'
    assert probe in script, script


def test_the_download_lays_every_cell_artifact_under_its_own_name(tmp):
    """Execute the step: the cells land where the probe will look.

    This is the defect the issue exists for, run end to end rather than
    described. A `gh run download -n 'speed-durations-*'` selects nothing,
    every candidate is stepped over, `count=0` is written, and the
    refresher below the step never runs -- all of it green.
    """
    fixtures = _artifacts(Path(tmp) / 'fixtures', 500)
    result, output, summary = _walk(tmp, [500], fixtures)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert 'count=1' in output, (output, result.stdout)
    laid_out = sorted(
        _posix_relative(path, Path(tmp) / 'runs' / '500')
        for path in (Path(tmp) / 'runs' / '500').rglob(_REFERENCE_FILE))
    assert laid_out == [f'{name}/{_REFERENCE_FILE}' for name in _ARTIFACTS], \
        laid_out
    # The round the refresher reads is inside the same artifact directory.
    report = (Path(tmp) / 'runs' / '500' / _ARTIFACTS[0] / 'head-1'
              / 'test_a.json')
    assert report.is_file(), report
    assert 'Downloaded 1 complete run' in summary, summary


def test_the_layout_is_spelled_the_way_the_step_spells_it(tmp):
    """The layout comparison, on a path whose separator is a backslash.

    The layout assertion above cannot see this class by itself: on Linux
    `str()` and `as_posix()` are the same string, so a revert to `str()`
    stays green here and only ever reddens on the Windows legs -- which is
    where it was found. So the rendering is exercised on a
    `PureWindowsPath`, whose native separator is `\\` on EVERY host, and
    the two spellings are held apart on the same path: the layout the step
    probes is the `/` one, and the host's own rendering is not.
    """
    del tmp
    root = PureWindowsPath('runs') / '500'
    laid_out = PureWindowsPath('runs', '500', _ARTIFACTS[0], _REFERENCE_FILE)
    # `format` renders through `__str__`, which is the host's own
    # spelling: a backslash here, whatever host this suite runs on.
    assert f'{laid_out.relative_to(root)}' == \
        f'{_ARTIFACTS[0]}\\{_REFERENCE_FILE}'
    assert _posix_relative(laid_out, root) == \
        f'{_ARTIFACTS[0]}/{_REFERENCE_FILE}'
    # Every name, not the first: the exact set is what the assertion
    # above it pins, so a renderer that got one right must get them all.
    assert [_posix_relative(PureWindowsPath('runs', '500', name,
                                            _REFERENCE_FILE), root)
            for name in _ARTIFACTS] == \
        [f'{name}/{_REFERENCE_FILE}' for name in _ARTIFACTS]
    # And the call site, because a revert can be spelled inline, where
    # the property above cannot reach it: no relative path in this module
    # is rendered with `str()`.
    module = Path(__file__).read_text(encoding='utf-8')
    assert not re.search(r'str\([^)]*relative_to', module), (
        'a relative path is rendered with str() somewhere in this module')


def test_a_walk_that_keeps_nothing_is_a_green_no_op(tmp):
    """Runs existed and none measured: `count=0`, exit 0, and said so.

    The documented no-op path, pinned because the CRITICAL defect reached
    it by accident: a selector matching nothing is indistinguishable from
    a night on which no run measured, unless the step's own output says
    which of the two it was.
    """
    fixtures = _artifacts(Path(tmp) / 'fixtures', 510, reference=False)
    result, output, summary = _walk(tmp, [510], fixtures)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert 'count=0' in output, (output, result.stdout)
    assert 'nothing was downloaded' in summary, summary
    assert not (Path(tmp) / 'runs' / '510').exists(), (
        'a stepped-over run was left on disk for the refresher to read')


def test_a_run_without_a_reference_reading_is_stepped_over_not_kept(tmp):
    """The walk's half of the run-selection rule, pinned by running it.

    `refresh_timings.select` REFUSES a tree whose cell has no
    `reference.json`, because stepping over it there is the silence the
    maintainer ruled out. The walk reaches the opposite disposition for
    the same run, on purpose: it is the gate in front, and a run from
    before the cells measured the reference workload would otherwise
    block every later candidate. Two authorities, so the walk's half is
    pinned here: the run is dropped from disk, it is NOT kept, and the
    reference cell set is released so the next candidate is judged on its
    own readings.
    """
    fixtures = Path(tmp) / 'fixtures'
    _artifacts(fixtures, 600, reference=False)
    _artifacts(fixtures, 599)
    result, output, summary = _walk(tmp, [600, 599], fixtures)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert 'count=1' in output, (output, result.stdout)
    assert 'no reference.json; stepped over' in result.stdout, result.stdout
    assert not (Path(tmp) / 'runs' / '600').exists(), 'the stepped-over run'
    kept = Path(tmp) / 'runs' / '599' / _ARTIFACTS[0] / _REFERENCE_FILE
    assert kept.is_file(), kept
    assert '599' in summary and '600' not in summary, summary


def test_a_run_whose_artifacts_query_fails_is_tolerated_and_counted(tmp):
    """One flaky API answer costs one candidate, and the summary says so.

    The walk tolerates a failing artifacts call for ONE candidate and
    nothing else: a sample smaller than SAMPLE because calls failed is
    not the search that was asked for, and the summary has to name it.
    """
    fixtures = Path(tmp) / 'fixtures'
    _artifacts(fixtures, 700, api_fails=True)
    _artifacts(fixtures, 699)
    result, output, summary = _walk(tmp, [700, 699], fixtures)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert 'count=1' in output, (output, result.stdout)
    assert 'artifacts query failed' in result.stdout, result.stdout
    # The double's own refusal is on stderr, so the step's tolerance is
    # a real `if !` branch and not a swallowed message.
    assert 'HTTP 500' in result.stderr, result.stderr
    assert '1 candidate run(s) were stepped over' in summary, summary


def test_every_list_the_step_reads_is_byte_exact_and_carries_no_cr(tmp):
    """Both lists the walk reads are the bytes `gh` emits: no CR anywhere.

    Two producers, one trap. The run-ids list is THIS fixture's, and the
    artifact-names list is the `gh` double's, standing in for `gh --jq
    '.artifacts[].name'`. Both were written in TEXT mode, which translates
    `\\n` to `os.linesep` -- `\\r\\n` on Windows -- and the step's `read -r`
    strips the newline but keeps the carriage return, so every id and every
    cell name misses its target and the step reports the honest-looking
    `count=0`. This asserts the BYTES at both sources rather than the
    walk's outcome, so what the shell reads does not depend on the
    platform that wrote the file, and a regression is caught where it is
    introduced instead of three layers down as a green no-op.
    """
    fixtures = _artifacts(Path(tmp) / 'fixtures', 500)

    # The run-ids list, straight from the writer the walk uses.
    ids_file = Path(tmp) / 'run-ids'
    _write_run_ids(ids_file, [500])
    assert ids_file.read_bytes() == b'500\n', ids_file.read_bytes()

    # The names the double relays, and the double's own answer for both
    # calls that write to stdout -- captured RAW, the way the shell reads.
    expected_names = b''.join(f'{n}\n'.encode() for n in _ARTIFACTS)
    assert (fixtures / '500.names').read_bytes() == expected_names
    module = _install_gh_double(tmp) / 'gh_double.py'
    environment = {**os.environ, 'GH_DOUBLE_FIXTURES': str(fixtures),
                   'GH_DOUBLE_WORKFLOW_RUNS': '500\n'}
    artifacts = subprocess.run(
        [sys.executable, str(module), 'api',
         'repos/example/example/actions/runs/500/artifacts'],
        env=environment, capture_output=True, check=True)
    assert artifacts.stdout == expected_names, artifacts.stdout
    listed = subprocess.run(
        [sys.executable, str(module), 'api',
         'repos/example/example/actions/workflows/tests.yml/runs'],
        env=environment, capture_output=True, check=True)
    assert listed.stdout == b'500\n', listed.stdout


def test_the_listing_step_is_fail_closed_on_zero_candidates(tmp):
    """Zero completed `tests` runs on main reddens the job, on purpose.

    The one fail-closed step in a job that is otherwise fail-tolerant,
    eleven lines above a documented green no-op for a different state. A
    bare `count="$(grep -c . file)"` assignment takes `grep`'s status,
    and a zero-match `grep -c` exits 1 under the `bash -e` GitHub
    starts a `run:` block with -- so this executes the step's own block
    against a `gh` that answers an empty run list and holds on the
    status rather than reading a count out of the log.
    """
    script = workflow_script(_timed_workflow(), 'refresh',
                             'List the recent tests runs on main')
    environment = _step_environment(tmp)
    empty = run_workflow_script(tmp, script, {
        **environment, 'GH_DOUBLE_WORKFLOW_RUNS': ''})
    assert empty.returncode != 0, (
        'a zero-candidate listing exited 0: the count assignment swallowed '
        f'the failure. stdout={empty.stdout!r} stderr={empty.stderr!r}')
    listed = run_workflow_script(tmp, script, {
        **environment, 'GH_DOUBLE_WORKFLOW_RUNS': '500\n'})
    assert listed.returncode == 0, (listed.returncode, listed.stderr)
    assert 'Listed 1 completed push runs' in listed.stderr, listed.stderr


def test_the_refresh_job_names_both_scopes_in_its_own_permissions(tmp):
    """A job-level block REPLACES the workflow-level one, not adds to it.

    This job checks out `main`, so it needs `contents: read`; the job
    block named only `actions: read` and every unnamed scope became
    `none`, so the checkout had no token to read `main` with. The
    mutation below is the shape a future edit takes -- one scope dropped
    from the pair -- and the pair is held to both of its scopes.
    """
    del tmp
    source = _timed_workflow()
    expected = {'actions': 'read', 'contents': 'read'}
    assert job_mapping(source, 'refresh', 'permissions') == expected, (
        job_mapping(source, 'refresh', 'permissions'))
    # The workflow-level block is unchanged and still names one scope.
    workflow_block = source.partition('\npermissions:\n')[2]
    assert workflow_block.startswith('  contents: read\n'), workflow_block
    for scope in ('actions', 'contents'):
        planted = re.sub(rf'^      {scope}: read[^\n]*\n', '', source,
                         count=1, flags=re.MULTILINE)
        assert planted != source, f'the job block does not name {scope}'
        decoded = job_mapping(planted, 'refresh', 'permissions')
        assert decoded != expected, (
            f'dropping {scope!r} left the pair assertion satisfied: '
            f'{decoded}')


def test_no_step_carries_an_id_nothing_reads(tmp):
    """`id: runs` set no output and had no consumer; the id is gone.

    The shape the download step's sibling once had: a value set and never
    read. `steps.download.outputs.count` is the only output reference in
    the workflow, and the listing step's `id` was not it.
    """
    del tmp
    source = _timed_workflow()
    ids = step_scalars(source, 'refresh', 'id') or []
    assert 'runs' not in ids, ids
    assert 'steps.runs' not in source
    assert 'steps.download.outputs.count' in source
    assert ids == ['download'], ids


def test_the_timed_matrix_is_the_planner_output_and_nothing_else(tmp):
    """`timed`'s matrix is the planner's, with its one documented default.

    A catch-all default planted beside the planner's output would time
    every suite in every cell while every shape test stayed green, so
    the expression is held to the whole string. The `|| '[]'` is
    load-bearing -- `fromJSON('')` fails matrix evaluation on the runner
    before the job's own `if:` is consulted -- and it is only ever read
    by a job that does not run.
    """
    del tmp
    source = _tests_workflow()
    job = complete_job_mapping(source, 'timed')
    assert job is not None, 'tests.yml has no timed job'
    strategy = job.get('strategy')
    assert strategy is not None, 'the timed job has no strategy block'
    assert strategy == {
        'fail-fast': 'false',
        'matrix': "${{ fromJSON(needs.plan-matrix.outputs.matrix "
                  "|| '[]') }}"}, strategy
    # No `strategy.include` row beside the expression: a row is a live
    # cell on any runner that does not cross it out, carrying empty
    # suites, which the instrument reads as "time every suite".
    assert 'include' not in strategy, strategy


def test_the_except_env_is_empty_because_no_plan_carries_an_except_key(tmp):
    """`SUITES_EXCEPT` is wired, reached, and empty -- and here is why.

    `time_tests.py` declares `--except`, this step is its only caller,
    and `test_every_option_a_script_declares_reaches_its_step` holds
    those two together, so the reach stays. What the review found
    condemnable was the comment defending an option that could never
    carry a value, and the answer to that is to pin WHY it is empty
    rather than to unplug it: the planner's matrix entry is `group` and
    `suites` and nothing else, so there is no `except` key for a matrix
    to carry, and a planner that grew one would be measured correctly
    the moment it did.
    """
    source = _tests_workflow()
    job = complete_job_mapping(source, 'timed')
    assert job is not None, 'tests.yml has no timed job'
    steps = job.get('steps') or []
    envs = [step.get('env') or {} for step in steps
            if 'SUITES_ONLY' in (step.get('env') or {})]
    assert len(envs) == 1, envs
    assert envs[0]['SUITES_EXCEPT'] == '${{ matrix.except }}', envs[0]
    text = '\n'.join(_job_section(source, 'timed'))
    assert 'add --except $SUITES_EXCEPT' in text, 'the flag is not reached'
    # The reason the value is empty, measured rather than asserted: no
    # plan the planner can make carries an `except` key.
    planner = _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                         'plan_timed_matrix')
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True)
    names = [f'test_{index:02d}.py' for index in range(6)]
    for name in names:
        (tree / 'tests' / name).write_text('pass\n', encoding='utf-8')
    # The planner enumerates the TRACKED tree, so the fixture is a git
    # checkout with these files in its index (`git ls-files` reads the
    # index, so no commit is made or needed).
    git_index(tree, 'init', '-q')
    git_index(tree, 'add', '--', 'tests/')
    data = {'target_cell_weight': 10.0, 'max_cells': 4, 'units': 'seconds',
            'suite_weights': {name: float(7 - index)
                              for index, name in enumerate(names)}}
    matrix = planner.plan(tree, data).matrix
    assert matrix, matrix
    for entry in matrix:
        assert set(entry) == {'group', 'suites'}, entry


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='timedwf_')


if __name__ == '__main__':
    raise SystemExit(main())
