"""Runtime observations for classic scripts sharing one worker global."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _boundary_env import ENVIRONMENT  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402


OBSERVER = ENVIRONMENT + r"""
const espree = require('espree');
const sourceDetails = JSON.parse(commandText);

function identifierCandidates(source) {
  return [...new Set(espree.tokenize(source, {
    ecmaVersion: 'latest', sourceType: 'script',
  }).filter((token) => token.type === 'Identifier')
    .map((token) => token.value))];
}

function readBinding(workerContext, name) {
  try {
    return {
      available: true,
      value: vm.runInContext(name, workerContext),
      descriptor: Object.getOwnPropertyDescriptor(workerContext, name),
    };
  } catch (error) {
    if (error.name === 'ReferenceError') return { available: false };
    throw error;
  }
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

function observeBindings(details) {
  const source = fs.readFileSync(details.path, 'utf8');
  const candidates = identifierCandidates(source);
  const workerContext = makeContext();
  workerContext.importScripts = () => {};
  for (const name of details.globals) {
    workerContext[name] = dependencyStub();
  }
  const before = new Map(candidates.map((name) => [
    name, readBinding(workerContext, name),
  ]));
  let executionError = null;
  try {
    vm.runInContext(source, workerContext, { filename: details.path });
  } catch (error) {
    executionError = { name: error.name, message: error.message };
  }
  const bindings = [];
  for (const name of candidates) {
    const prior = before.get(name);
    const after = readBinding(workerContext, name);
    if (after.available && (!prior.available
        || after.value !== prior.value
        || descriptorChanged(prior.descriptor, after.descriptor))) {
      bindings.push(name);
    }
  }
  return { bindings: bindings.sort(), bindingExecutionError: executionError };
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
    vm.runInContext(
      '__markWorkerObservationStarted();\n' + source,
      workerContext, { filename: details.path });
  } catch (error) {
    executionError = { name: error.name, message: error.message };
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

const observations = {};
for (const details of sourceDetails) {
  observations[details.path] = Object.assign(
    observeBindings(details), observeHandlerWrites(details));
}
process.stdout.write(JSON.stringify({
  sources: observations,
  shared: observeSharedLoad(),
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
            'watched': sorted(details.get('watched', ())),
        }
        for details in source_details
    ]
    result = subprocess.run(
        [node, '-e', OBSERVER, str(background_path), 'worker-bindings',
         json.dumps(payload)],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)
