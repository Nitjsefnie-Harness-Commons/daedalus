#!/usr/bin/env python3
"""Focused runtime-observer and clean-tree worker controls."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _boundary  # noqa: E402
import _boundary_env  # noqa: E402
import _util  # noqa: E402
import _worker_runtime  # noqa: E402
from _repo import ROOT  # noqa: E402


def _tracked_tree(tmp):
    export_root = Path(tmp) / 'tracked'
    export_root.mkdir()
    listed = subprocess.run(
        ['git', 'ls-files', '-z'], cwd=ROOT,
        capture_output=True, check=True)
    for raw_path in listed.stdout.split(b'\0'):
        if not raw_path:
            continue
        relative = Path(os.fsdecode(raw_path))
        destination = export_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    return export_root


def test_runtime_observer_uses_javascript_global_scope(tmp):
    """Runtime scope, not a declaration lexer, decides binding ownership."""
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    collisions = (
        'var { _executionContext } = '
        '{ _executionContext: function () {} };',
        'let a = 1, _executionContext = 2;',
        'for (var _executionContext of [function () {}]) {}',
        'if (chrome.runtime) { var _executionContext = function () {}; }',
        'if (chrome.runtime) { var _executionContext\n}',
        'let q = 1; q++ / d; var _executionContext = n / d;',
        'let q = 1; q-- / d; var _executionContext = n / d;',
        'const q = class {} / d; var _executionContext = n / d;',
        'const q = function () {} / d; '
        'var _executionContext = n / d;',
    )
    details = []
    collision_paths = []
    for index, source in enumerate(collisions):
        path = root / f'collision-{index}.js'
        path.write_text(source + '\n', encoding='utf-8')
        collision_paths.append(path)
        details.append({
            'path': path, 'globals': {'d', 'n'}, 'watched': (),
        })

    harmless = root / 'harmless.js'
    harmless.write_text(r"""
function outer() {
  var _executionContext;
  function nested() { var _executionContext; }
  const nestedConst = 1;
  let nestedLet = nestedConst;
}
(function () { var _executionContext; }());
const arrow = () => { var _executionContext; };
class Holder {
  static { var _executionContext; }
  method() { var _executionContext; }
  get value() { var _executionContext; }
  set value(input) { var _executionContext; }
  async run() { var _executionContext; }
  *generate() { var _executionContext; }
}
const object = {
  if() { var _executionContext; },
  while() { var _executionContext; },
  for() { var _executionContext; },
  switch() { var _executionContext; },
  with() { var _executionContext; },
  catch() { var _executionContext; },
  _executionContext: 1,
};
const stringValue = 'var _executionContext;';
const templateValue = `var _executionContext;`;
const regexValue = /[\/;]var _executionContext;/;
// var _executionContext;
for (const item of (() => {
  var _executionContext;
  return [];
})()) { void item; }
""", encoding='utf-8')
    details.append({'path': harmless, 'globals': (), 'watched': ()})

    observed = _worker_runtime.observe_worker_runtime(
        details, background_path=background)['sources']
    for path in collision_paths:
        assert '_executionContext' in observed[str(path)]['bindings'], path
    assert '_executionContext' not in observed[str(harmless)]['bindings']


def test_runtime_observer_rejects_handler_reassignment(tmp):
    """Handler instantiation and later writes are separate runtime events."""
    path = Path(tmp) / 'handler.js'
    path.write_text("""
function handleCookies() {}
handleCookies = function replacement() {};
""", encoding='utf-8')
    observed = _worker_runtime.observe_worker_runtime([{
        'path': path, 'globals': (), 'watched': {'handleCookies'},
    }], background_path=path)['sources'][str(path)]
    assert observed['events']['handleCookies'] == {
        'declarations': 1, 'writes': 1,
    }


def test_worker_harness_programs_use_cleaned_temporary_files(tmp):
    """Every Node harness keeps its program out of argv and cleans its file."""
    root = Path(tmp)
    log_path = root / 'node-programs.jsonl'
    probe = f"""
const fs = require('fs');
fs.appendFileSync({json.dumps(str(log_path))}, JSON.stringify({{
  filename: __filename,
  dirname: __dirname,
  argv: process.argv.slice(1),
}}) + '\\n');
const scenario = process.argv[2];
if (scenario === 'worker-sources') process.stdout.write('[]');
else if (scenario === 'worker-bindings') {{
  process.stdout.write(JSON.stringify({{
    sources: {{}}, shared: {{ loaded: [], error: null }},
  }}));
}} else process.stdout.write('{{}}');
"""
    boundary_program = _boundary.HARNESS
    observer_program = _worker_runtime.OBSERVER
    _boundary.HARNESS = probe
    _worker_runtime.OBSERVER = probe
    try:
        _boundary.run_extension_result_boundary('capacity')
        _boundary.run_extension_capability_routes([])
        _boundary.observe_extension_worker_paths()
        _worker_runtime.observe_worker_runtime([])
    finally:
        _boundary.HARNESS = boundary_program
        _worker_runtime.OBSERVER = observer_program

    records = [json.loads(line) for line in log_path.read_text(
        encoding='utf-8').splitlines()]
    assert len(records) == 4, records
    assert [record['argv'][1] for record in records] == [
        'capacity', 'capability-routes', 'worker-sources', 'worker-bindings',
    ]
    for record in records:
        program_path = Path(record['filename'])
        assert program_path.name == 'program.js', record
        assert record['dirname'] == str(program_path.parent), record
        assert not program_path.exists(), record


def test_failing_worker_harness_cleans_its_temporary_file(tmp):
    """A Node failure cannot leak the file that carried its program."""
    marker = Path(tmp) / 'failed-program-path.txt'
    program = _boundary.HARNESS
    _boundary.HARNESS = f"""
require('fs').writeFileSync(
  {json.dumps(str(marker))}, __filename, 'utf8');
throw new Error('forced harness failure');
"""
    try:
        try:
            _boundary.run_extension_result_boundary('capacity')
        except AssertionError:
            pass
        else:
            raise AssertionError('forced harness failure unexpectedly passed')
    finally:
        _boundary.HARNESS = program

    program_path = Path(marker.read_text(encoding='utf-8'))
    assert program_path.name == 'program.js', program_path
    assert not program_path.exists(), program_path


def test_worker_harness_command_line_is_module_count_independent(tmp):
    """Serialized module records never make Node's argv grow."""
    counts = (1, 4, 7, 10)
    measurements = {'HARNESS': {}, 'OBSERVER': {}}
    active = {'label': None, 'count': None}
    real_run = _boundary_env.subprocess.run

    def measured_run(argv, **kwargs):
        del kwargs
        measurements[active['label']][active['count']] = len(
            subprocess.list2cmdline(argv))
        return subprocess.CompletedProcess(argv, 0, '{}', '')

    _boundary_env.subprocess.run = measured_run
    try:
        for count in counts:
            routes = [
                {
                    'symbol': f'handleRoute{index}',
                    'command': {'type': f'route-{index}'},
                    'publishedSymbols': [f'handleRoute{item}'
                                         for item in range(count)],
                }
                for index in range(count)
            ]
            active.update(label='HARNESS', count=count)
            _boundary.run_extension_capability_routes(routes)

            details = [
                {
                    'path': Path(tmp) / f'worker-{index}.js',
                    'globals': {f'global{index}'},
                    'probes': {f'probe{index}'},
                    'watched': {f'handler{index}'},
                }
                for index in range(count)
            ]
            active.update(label='OBSERVER', count=count)
            _worker_runtime.observe_worker_runtime(
                details, background_path=Path(tmp) / 'background.js')
    finally:
        _boundary_env.subprocess.run = real_run

    assert all(len(set(values.values())) == 1
               for values in measurements.values()), measurements


def test_sibling_mutation_failure_names_module_type_and_handlers(tmp):
    """The boundary diagnostic distinguishes mutation from route bypass."""
    export_root = _tracked_tree(tmp)
    background_path = export_root / 'extension' / 'background.js'
    background = background_path.read_text(encoding='utf-8')
    route = "    case 'block-requests': return handleBlockRequests(cmd);"
    mutation = """    case 'block-requests':
      handleCookies = function corruptedCookies() { return false; };
      return handleBlockRequests(cmd);"""
    assert background.count(route) == 1
    background_path.write_text(
        background.replace(route, mutation), encoding='utf-8')

    result = subprocess.run(
        [sys.executable, 'tests/test_worker_module_boundary.py'],
        cwd=export_root, capture_output=True, text=True, timeout=30)

    assert result.returncode != 0, result.stdout
    expected = (
        "worker/blocking.js dispatch block-requests mutated published "
        "handlers: ['handleCookies']")
    assert expected in result.stdout, (
        result.returncode, result.stdout, result.stderr)


def test_worker_boundary_runs_without_untracked_node_modules(tmp):
    """The tracked tree alone supplies every boundary-suite dependency."""
    export_root = _tracked_tree(tmp)
    result = subprocess.run(
        [sys.executable, 'tests/test_worker_module_boundary.py'],
        cwd=export_root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
