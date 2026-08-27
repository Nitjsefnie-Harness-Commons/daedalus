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
from _worker_sources import imported_worker_paths  # noqa: E402


def _tracked_paths():
    listed = subprocess.run(
        ['git', 'ls-files', '-z'], cwd=ROOT,
        capture_output=True, check=True)
    return tuple(
        Path(os.fsdecode(raw_path))
        for raw_path in listed.stdout.split(b'\0')
        if raw_path)


def _assert_imported_modules_are_tracked(tracked):
    background = ROOT / 'extension' / 'background.js'
    imported = tuple(
        path.relative_to(ROOT)
        for path in imported_worker_paths(background))
    missing = sorted(set(imported) - set(tracked))
    if not missing:
        return
    names = ' '.join(path.as_posix() for path in missing)
    if len(missing) == 1:
        description = f'{names} is imported by extension/background.js '
        description += 'but is not tracked'
    else:
        listed = ', '.join(path.as_posix() for path in missing)
        description = f'{listed} are imported by extension/background.js '
        description += 'but are not tracked'
    raise AssertionError(
        f'{description}; '
        f'run git add -f {names}, then python3 scripts/gen_gitignore.py')


def _tracked_tree(tmp):
    tracked = _tracked_paths()
    _assert_imported_modules_are_tracked(tracked)
    export_root = Path(tmp) / 'tracked'
    export_root.mkdir()
    for relative in tracked:
        destination = export_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    return export_root


def _assert_binding_read_failure(tmp, thrown):
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    worker = root / 'property.js'
    worker.write_text(f"""
Object.defineProperty(globalThis, 'sharedName', {{
  configurable: true,
  get() {{ throw {thrown}; }},
}});
""", encoding='utf-8')

    failure = None
    try:
        _worker_runtime.observe_worker_runtime([{
            'path': worker, 'globals': (), 'watched': (),
        }], background_path=background)
    except AssertionError as error:
        failure = str(error)
    else:
        raise AssertionError('the sharedName getter failure was swallowed')

    assert repr(str(worker))[1:-1] in failure, failure
    assert 'sharedName' in failure, failure
    assert 'Error: reading binding' in failure, failure
    return failure


def _assert_candidate_does_not_enter_program(tmp, candidate, marker):
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    worker = root / 'property.js'
    worker.write_text(
        'Object.defineProperty(globalThis, '
        f'{json.dumps(candidate)}, {{ configurable: true, value: 1 }});\n',
        encoding='utf-8')

    observed = _worker_runtime.observe_worker_runtime([{
        'path': worker,
        'globals': (),
        'probes': {marker},
        'watched': (),
    }], background_path=background)['sources'][str(worker)]

    assert observed['bindingExecutionError'] is None
    assert observed['bindings'] == []


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


def test_runtime_observer_reports_unicode_binding_collisions(tmp):
    """Runtime probing keeps valid Unicode binding names observable."""
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    paths = [root / 'first.js', root / 'second.js']
    declarations = (
        r'var caf\u00e9 = 1;',
        r'var \u{10400} = 1;',
        r'var joiner\u200Cname = 1;',
        r'var joiner\u200Dname = 1;',
        'var static = 1, arguments = 1, eval = 1, constructor = 1;',
    )
    paths[0].write_text('\n'.join(declarations) + '\n', encoding='utf-8')
    paths[1].write_text('\n'.join(declarations) + '\n', encoding='utf-8')

    observed = _worker_runtime.observe_worker_runtime([
        {'path': path, 'globals': (), 'watched': ()}
        for path in paths
    ], background_path=background)['sources']

    expected = sorted(['arguments', 'café', 'constructor', 'eval',
                       '\U00010400', 'joiner\u200cname', 'joiner\u200dname',
                       'static'])
    assert [observed[str(path)]['bindings'] for path in paths] == [
        expected, expected,
    ]


def test_runtime_observer_skips_non_binding_property_names(tmp):
    """Only valid binding identifiers enter the binding inventory."""
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    worker = root / 'reserved.js'
    worker.write_text(
        'globalThis.class = 1;\n'
        'globalThis.if = 1;\n'
        'globalThis.for = 1;\n'
        'globalThis.with = 1;\n'
        'globalThis.yield = 1;\n'
        'globalThis.await = 1;\n'
        "globalThis['not-an-identifier'] = 1;\n"
        "globalThis['one + two'] = 1;\n"
        "globalThis['#private'] = 1;\n"
        "globalThis['line\\nbreak'] = 1;\n"
        "globalThis['brace}name'] = 1;\n",
        encoding='utf-8')

    observed = _worker_runtime.observe_worker_runtime([{
        'path': worker, 'globals': (), 'watched': (),
    }], background_path=background)['sources'][str(worker)]

    assert observed['bindingExecutionError'] is None
    assert observed['bindings'] == []


def test_candidate_program_text_does_not_enter_probe(tmp):
    """Statements, closing braces and newlines cannot alter probe grammar."""
    candidates = (
        ('undefined; { globalThis.taskThreeInjected = 1; }',
         'taskThreeInjected'),
        ('undefined; } globalThis.taskThreeBraceInjected = 1; {',
         'taskThreeBraceInjected'),
        ('undefined;\nglobalThis.taskThreeNewlineInjected = 1',
         'taskThreeNewlineInjected'),
    )
    for candidate, marker in candidates:
        _assert_candidate_does_not_enter_program(tmp, candidate, marker)


def test_node_harness_decodes_utf8_independent_of_locale(tmp):
    """A legacy Windows code page cannot decode the Node JSON stream."""
    del tmp
    node = shutil.which('node')
    assert node, 'node is required to check harness output decoding'
    original_text_encoding = _boundary_env.subprocess._text_encoding
    _boundary_env.subprocess._text_encoding = lambda: 'cp1252'
    try:
        program = (
            r"process.stdout.write(JSON.stringify("
            r"{name:'joiner\u200Dname'}));")
        result = _boundary_env.run_node_program(
            node, program, [], cwd=ROOT)
    finally:
        _boundary_env.subprocess._text_encoding = original_text_encoding

    assert result.returncode == 0, result
    assert json.loads(result.stdout) == {'name': 'joiner\u200dname'}


def test_runtime_observer_tracks_probeable_global_properties(tmp):
    """A global property write can overwrite another classic-script binding."""
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    worker = root / 'property.js'
    worker.write_text('globalThis.sharedName = 1;\n', encoding='utf-8')

    observed = _worker_runtime.observe_worker_runtime([{
        'path': worker, 'globals': (), 'watched': (),
    }], background_path=background)['sources'][str(worker)]

    assert observed['bindings'] == ['sharedName']


def test_runtime_observer_reads_binding_getter_once(tmp):
    """Descriptor discovery does not add a second getter invocation."""
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    worker = root / 'property.js'
    worker.write_text("""
let bindingReads = 0;
Object.defineProperty(globalThis, 'sharedName', {
  configurable: true,
  get() {
    bindingReads++;
    if (bindingReads > 1) throw new Error('getter read more than once');
    return 1;
  },
});
""", encoding='utf-8')

    observed = _worker_runtime.observe_worker_runtime([{
        'path': worker, 'globals': (), 'watched': (),
    }], background_path=background)['sources'][str(worker)]

    assert observed['bindings'] == ['sharedName']


def test_runtime_observer_skips_missing_probe_property(tmp):
    """A candidate absent from the context remains unavailable."""
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    worker = root / 'property.js'
    worker.write_text('const localMarker = true;\n', encoding='utf-8')

    observed = _worker_runtime.observe_worker_runtime([{
        'path': worker, 'globals': (), 'probes': {'missingName'},
        'watched': (),
    }], background_path=background)['sources'][str(worker)]

    assert observed['bindingExecutionError'] is None
    assert observed['bindings'] == []


def test_runtime_observer_rejects_prototype_proxy_owner(tmp):
    """A prototype Proxy cannot fabricate a module-owned binding."""
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    worker = root / 'prototype.js'
    worker.write_text("""
const prototype = new Proxy({}, {
  has(_target, name) { return name === 'fabricatedName'; },
  get(_target, name) {
    return name === 'fabricatedName' ? 1 : undefined;
  },
  getOwnPropertyDescriptor(_target, name) {
    if (name !== 'fabricatedName') return undefined;
    return { configurable: true, enumerable: true, value: 1 };
  },
});
Object.setPrototypeOf(this, prototype);
""", encoding='utf-8')

    observed = _worker_runtime.observe_worker_runtime([{
        'path': worker,
        'globals': (),
        'probes': {'fabricatedName'},
        'watched': (),
    }], background_path=background)['sources'][str(worker)]

    assert 'fabricatedName' not in observed['bindings']


def test_runtime_observer_owns_uninitialised_lexical_binding(tmp):
    """An uninitialised top-level let remains visible to the inventory."""
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    worker = root / 'lexical.js'
    worker.write_text('let dormantBinding;\n', encoding='utf-8')

    observed = _worker_runtime.observe_worker_runtime([{
        'path': worker,
        'globals': (),
        'probes': {'dormantBinding'},
        'watched': (),
    }], background_path=background)['sources'][str(worker)]

    assert observed['bindings'] == ['dormantBinding']


def test_runtime_observer_owns_lexical_and_property_collision(tmp):
    """A lexical binding and same-named global property have two owners."""
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    lexical = root / 'lexical.js'
    lexical.write_text('let sharedName;\n', encoding='utf-8')
    property_source = root / 'property.js'
    property_source.write_text(
        'globalThis.sharedName = 1;\n', encoding='utf-8')

    paths = (lexical, property_source)
    observed = _worker_runtime.observe_worker_runtime([
        {'path': path, 'globals': (), 'watched': ()}
        for path in paths
    ], background_path=background)['sources']

    assert [observed[str(path)]['bindings'] for path in paths] == [
        ['sharedName'], ['sharedName'],
    ]


def test_runtime_observer_uses_this_with_hostile_global_this(tmp):
    """Context discovery does not ask a replaced globalThis accessor."""
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    worker = root / 'global-this.js'
    worker.write_text("""
const actualGlobal = this;
const hostile = new Proxy({}, {
  get() { throw new Error('hostile globalThis get'); },
  has() { throw new Error('hostile globalThis has'); },
  getOwnPropertyDescriptor() {
    throw new Error('hostile globalThis descriptor');
  },
});
Object.defineProperty(actualGlobal, 'globalThis', {
  configurable: true,
  get() { return hostile; },
});
var contextualOwner = 1;
""", encoding='utf-8')

    observed = _worker_runtime.observe_worker_runtime([{
        'path': worker, 'globals': (), 'watched': (),
    }], background_path=background)['sources'][str(worker)]

    assert observed['bindings'] == ['contextualOwner', 'globalThis']


def test_runtime_observer_surfaces_every_binding_read_failure(tmp):
    """Thrown values cannot suppress or launder the harness diagnostic."""
    cases = (
        ("new RangeError('range failed')", None),
        ("'string failed'", None),
        ("new SyntaxError('getter failed')", 'SyntaxError'),
        ("new TypeError('type failed')", None),
        ("{ toString() { throw new Error('coercion escaped'); } }", None),
        ("{ name: 'ReferenceError', message: 'forged failure' }",
         'ReferenceError'),
        ("{ name: 'SyntaxError', message: 'forged failure' }",
         'SyntaxError'),
    )
    for thrown, forbidden_name in cases:
        failure = _assert_binding_read_failure(tmp, thrown)
        if forbidden_name is not None:
            assert f'{forbidden_name}: reading binding' not in failure


def test_payload_keeps_harness_source_on_the_second_line(tmp):
    """A serialized payload does not shift program stack locations."""
    node = shutil.which('node')
    assert node, 'node is required to check harness source locations'
    result = _boundary_env.run_node_program(
        node, '      missingName;\n', [],
        cwd=ROOT, payload='{}')

    assert result.returncode != 0, result
    assert 'program.js:2:7' in result.stderr, result.stderr


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


def test_tracked_tree_preflight_names_untracked_imported_module(tmp):
    """An imported worker omitted from Git fails before the export runs."""
    root = Path(tmp)
    source_root = root / 'source'
    worker_root = source_root / 'extension' / 'worker'
    worker_root.mkdir(parents=True)
    background = source_root / 'extension' / 'background.js'
    background.write_text(
        "importScripts('worker/untracked.js');\n", encoding='utf-8')
    (worker_root / 'untracked.js').write_text(
        '// deliberately untracked\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(source_root), 'init', '-q'], check=True)
    subprocess.run(
        ['git', '-C', str(source_root), 'add', '-f',
         'extension/background.js'], check=True)

    export_parent = root / 'export'
    export_parent.mkdir()
    original_root = globals()['ROOT']
    failure = None
    globals()['ROOT'] = source_root
    try:
        try:
            _tracked_tree(export_parent)
        except AssertionError as error:
            failure = str(error)
        else:
            raise AssertionError(
                'untracked imported worker module passed preflight')
    finally:
        globals()['ROOT'] = original_root

    expected = (
        'extension/worker/untracked.js is imported by '
        'extension/background.js but is not tracked; run git add -f '
        'extension/worker/untracked.js, then '
        'python3 scripts/gen_gitignore.py')
    assert failure == expected, failure


def test_imported_worker_modules_are_tracked(tmp):
    """Every module loaded by importScripts is present in Git's file set."""
    del tmp
    _assert_imported_modules_are_tracked(_tracked_paths())


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
        cwd=export_root, capture_output=True, text=True,
        encoding='utf-8', timeout=30)

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
        cwd=export_root, capture_output=True, text=True,
        encoding='utf-8', timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def main():
    tests = _util.collect(globals())
    try:
        _assert_imported_modules_are_tracked(_tracked_paths())
    except AssertionError:
        tests = [test_imported_worker_modules_are_tracked]
    return _util.runner(tests)


if __name__ == '__main__':
    raise SystemExit(main())
