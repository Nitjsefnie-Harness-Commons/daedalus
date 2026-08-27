"""Runtime observations for classic scripts sharing one worker global."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _boundary_env import ENVIRONMENT, run_node_program  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402


OBSERVER = ENVIRONMENT + r"""
const sourceDetails = JSON.parse(commandText);

function observationContext(details) {
  const workerContext = makeContext();
  workerContext.importScripts = () => {};
  for (const name of details.globals) {
    workerContext[name] = dependencyStub();
  }
  return workerContext;
}

function escapedIdentifier(name) {
  let escaped = '';
  for (const point of name) {
    escaped += `\\u{${point.codePointAt(0).toString(16)}}`;
  }
  return escaped;
}

function readBinding(workerContext, name, source) {
  const identifier = escapedIdentifier(name);
  if (identifier === '') return { probeable: false };
  let lexicalProbe;
  try {
    new vm.Script(
      `async function* bindingProbe() { let ${identifier}; }`);
    lexicalProbe = new vm.Script(`let ${identifier};`);
  } catch {
    return { probeable: false };
  }
  const globalObject = new vm.Script('this').runInContext(workerContext);
  const descriptor = Object.getOwnPropertyDescriptor(globalObject, name);
  if (!descriptor) {
    try {
      lexicalProbe.runInContext(workerContext);
      return { probeable: true, available: false };
    } catch {
      return { probeable: true, available: true };
    }
  }
  try {
    new vm.Script(identifier).runInContext(workerContext);
  } catch {
    throw new Error(
      `reading binding ${JSON.stringify(name)} from ${source}`);
  }
  return { probeable: true, available: true };
}

function descriptorChanged(before, after) {
  if (!before && !after) return false;
  if (!before || !after) return true;
  return before.configurable !== after.configurable
    || before.enumerable !== after.enumerable
    || before.writable !== after.writable
    || before.value !== after.value
    || before.get !== after.get
    || before.set !== after.set;
}

function dependencyStub() {
  let stub;
  const callable = function observedDependency() { return stub; };
  stub = new Proxy(callable, {
    apply() { return stub; },
    construct() { return stub; },
    get(target, name) {
      if (name === Symbol.toPrimitive) return () => 0;
      return Reflect.has(target, name) ? Reflect.get(target, name) : stub;
    },
  });
  return stub;
}

function ownDescriptors(workerContext) {
  return new Map(Object.getOwnPropertyNames(workerContext).map((name) => [
    name, Object.getOwnPropertyDescriptor(workerContext, name),
  ]));
}

function observeBindingState(details) {
  const baselineContext = observationContext(details);
  const workerContext = observationContext(details);
  const before = ownDescriptors(workerContext);
  let executionError = null;
  try {
    vm.runInContext(
      fs.readFileSync(details.path, 'utf8'), workerContext,
      { filename: details.path });
  } catch (error) {
    executionError = { name: error.name, message: error.message };
  }
  const propertyBindings = new Set();
  for (const name of Object.getOwnPropertyNames(workerContext)) {
    const prior = before.get(name);
    const after = Object.getOwnPropertyDescriptor(workerContext, name);
    if (!prior || descriptorChanged(prior, after)) {
      propertyBindings.add(name);
    }
  }
  return {
    details, baselineContext, workerContext, propertyBindings,
    bindingExecutionError: executionError,
  };
}

function observeHandlerWrites(details) {
  const source = fs.readFileSync(details.path, 'utf8');
  const watched = new Set(details.watched);
  const events = new Map(details.watched.map((name) => [name, {
    declarations: 0, writes: 0,
  }]));
  let started = false;
  const base = makeContext();
  const target = Object.create(base);
  target.importScripts = () => {};
  for (const name of details.globals) target[name] = dependencyStub();
  target.__markWorkerObservationStarted = () => { started = true; };
  const proxy = new Proxy(target, {
    defineProperty(object, name, descriptor) {
      return Reflect.defineProperty(object, name, descriptor);
    },
    set(object, name, value, receiver) {
      if (watched.has(name)) {
        const event = events.get(name);
        if (started) event.writes++;
        else event.declarations++;
      }
      return Reflect.set(object, name, value, receiver);
    },
  });
  const workerContext = vm.createContext(proxy);
  let executionError = null;
  try {
    // V8 instantiates top-level declarations before the first statement
    // runs, so only a statement inside the observed script can separate
    // declarations from writes. That marker shifts every byte, so this
    // counting run must not name details.path: a V8 coverage record is
    // measured against the bytes actually executed, and offsets taken
    // from the concatenated source cannot fit the file on disk.
    // vm-load-exempt: runs marker plus source, so it must not name the file
    vm.runInContext(
      '__markWorkerObservationStarted();\n' + source,
      workerContext, { filename: '[worker-handler-observation]' });
  } catch (error) {
    executionError = { name: error.name, message: error.message };
  }
  const coverageContext = observationContext(details);
  try {
    // The verbatim run keeps this observer's coverage contribution: the
    // bytes executed are exactly the bytes on disk, so every offset in
    // the record maps onto details.path.
    vm.runInContext(
      fs.readFileSync(details.path, 'utf8'), coverageContext,
      { filename: details.path });
  } catch {
    // The counting run above reports execution errors; this run exists
    // only to execute the shipped bytes under their own name.
  }
  return {
    events: Object.fromEntries(events),
    handlerExecutionError: executionError,
  };
}

function observeSharedLoad() {
  const workerContext = makeContext();
  const loaded = [];
  let activeSource = backgroundPath;
  workerContext.importScripts = (...sourceNames) => {
    for (const sourceName of sourceNames) {
      const sourcePath = require('path').resolve(
        require('path').dirname(backgroundPath), sourceName);
      loaded.push(sourcePath);
      activeSource = sourcePath;
      vm.runInContext(
        fs.readFileSync(sourcePath, 'utf8'), workerContext,
        { filename: sourcePath });
    }
  };
  try {
    vm.runInContext(
      fs.readFileSync(backgroundPath, 'utf8'), workerContext,
      { filename: backgroundPath });
    return { loaded, error: null };
  } catch (error) {
    return {
      loaded,
      error: {
        source: activeSource, name: error.name, message: error.message,
      },
    };
  }
}

function observedBindings(states, shared) {
  const candidates = new Set();
  for (const state of states) {
    for (const name of state.propertyBindings) candidates.add(name);
    for (const name of state.details.probes) candidates.add(name);
  }
  if (shared.error) {
    const match = /Identifier '([^']+)' has already been declared/.exec(
      shared.error.message);
    if (match) candidates.add(match[1]);
  }
  const observations = {};
  for (const state of states) {
    const bindings = [];
    for (const name of candidates) {
      const before = readBinding(
        state.baselineContext, name, state.details.path);
      const after = readBinding(
        state.workerContext, name, state.details.path);
      if (!before.probeable || !after.probeable) continue;
      // A lexical binding whose name no other source supplies is unseen:
      // discovery is by name; a classic-script lexical publishes no name.
      if (state.propertyBindings.has(name)
          || (!before.available && after.available)) {
        bindings.push(name);
      }
    }
    observations[state.details.path] = {
      bindings: bindings.sort(),
      bindingExecutionError: state.bindingExecutionError,
    };
  }
  return observations;
}

const shared = observeSharedLoad();
const states = sourceDetails.map(observeBindingState);
const observations = observedBindings(states, shared);
for (const details of sourceDetails) {
  Object.assign(observations[details.path], observeHandlerWrites(details));
}
process.stdout.write(JSON.stringify({
  sources: observations,
  shared,
}));
"""


def observe_worker_runtime(source_details, background_path=None):
    """Run sources to observe global bindings and handler publication.

    The Node vm is not a security boundary; host functions expose their realm
    intrinsics to deliberately hostile source. This guard catches honest
    classic-worker split drift, not a worker author trying to forge evidence.
    """
    node = shutil.which('node')
    assert node, 'node is required to observe worker declarations'
    if background_path is None:
        background_path = EXTENSION_ROOT / 'background.js'
    payload = [
        {
            'path': str(Path(details['path']).resolve()),
            'globals': sorted(details.get('globals', ())),
            'probes': sorted(details.get('probes', ())),
            'watched': sorted(details.get('watched', ())),
        }
        for details in source_details
    ]
    result = run_node_program(
        node, OBSERVER,
        [str(background_path), 'worker-bindings'], cwd=ROOT,
        payload=json.dumps(payload))
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)
