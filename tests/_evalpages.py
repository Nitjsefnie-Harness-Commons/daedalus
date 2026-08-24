"""Pages and probes the real-browser eval tests serve.

Not a suite itself — run_tests.py only loads `test_*.py`.

Each of these is a page whose own rules are the point: one that replaces
every evaluator it can reach, one whose CSP forbids dynamic compilation, one
that poisons the timing API the relay reads. They live apart from the fixture
that serves them because they are content, not machinery.
"""


CDP_CALL_HARNESS = r"""
const [target, method, paramsText] = process.argv.slice(1);
const socket = new WebSocket(target);
const timer = setTimeout(() => {
  process.stderr.write('CDP response timed out\n');
  socket.close();
  process.exitCode = 1;
}, 10000);

socket.addEventListener('open', () => {
  socket.send(JSON.stringify({ id: 1, method, params: JSON.parse(paramsText) }));
});
socket.addEventListener('message', (event) => {
  const message = JSON.parse(String(event.data));
  if (message.id !== 1) return;
  clearTimeout(timer);
  if (message.error) {
    process.stderr.write(JSON.stringify(message.error) + '\n');
    process.exitCode = 1;
  } else {
    process.stdout.write(JSON.stringify(message.result || {}));
  }
  socket.close();
});
socket.addEventListener('error', () => {
  clearTimeout(timer);
  process.stderr.write('CDP websocket failed\n');
  process.exitCode = 1;
});
"""


HOSTILE_EVAL_SCRIPT = r"""
(() => {
  try {
    const FORGED = 'FORGED-BY-PAGE';
    const forged = function () { return 'FORGED'; };
    // Descriptors are null-prototype: once `Object.prototype` carries a
    // `value` accessor, an ordinary descriptor literal inherits it and
    // `defineProperty` rejects the whole poison.
    const define = (target, key, descriptor) => Object.defineProperty(
      target, key, Object.assign({ __proto__: null }, descriptor));

    // Evaluator bindings: `eval`, `Function`, the four function-constructor
    // prototypes, both same-origin iframe access routes and `Worker`.
    const constructors = [
      Function,
      (async function () {}).constructor,
      (function* () {}).constructor,
      (async function* () {}).constructor,
    ];
    for (const constructor of constructors) {
      define(constructor.prototype, 'constructor', {
        configurable: true,
        value: forged,
        writable: true,
      });
    }
    const fakeWindow = { eval: forged, Function: forged };
    define(HTMLIFrameElement.prototype, 'contentWindow', {
      configurable: true,
      get() { return fakeWindow; },
    });
    define(HTMLIFrameElement.prototype, 'contentDocument', {
      configurable: true,
      get() { return { defaultView: fakeWindow }; },
    });
    const contentWindowFrame = document.createElement('iframe');
    const defaultViewFrame = document.createElement('iframe');
    document.body.append(contentWindowFrame, defaultViewFrame);
    void contentWindowFrame.contentWindow;
    void defaultViewFrame.contentDocument.defaultView;
    globalThis.eval = forged;
    globalThis.Function = forged;
    globalThis.Worker = forged;
    document.title = 'Hostile eval page';

    // Retrieval bindings. Promise resolution reads `constructor` and `then`
    // off page-writable prototypes and assimilates anything callable it finds
    // there, so an evaluator whose value rides back through page promise
    // machinery is forgeable even when its compilation is not.
    const poisonedThen = function (resolve) {
      if (typeof resolve === 'function') resolve(FORGED);
      return this;
    };
    function Poisoned() {}
    Poisoned[Symbol.species] = function (executor) {
      executor(function () {}, function () {});
      return this;
    };
    define(Promise.prototype, 'constructor', {
      configurable: true,
      value: Poisoned,
      writable: true,
    });
    const valueProtos = [Object.prototype, Number.prototype, String.prototype,
      Boolean.prototype, Array.prototype, Function.prototype, Error.prototype];
    for (const proto of [Promise.prototype].concat(valueProtos)) {
      define(proto, 'then', {
        configurable: true,
        value: poisonedThen,
        writable: true,
      });
    }
    for (const proto of valueProtos) {
      define(proto, Symbol.toPrimitive, {
        configurable: true,
        value: function () { return FORGED; },
        writable: true,
      });
    }
    define(Array.prototype, Symbol.iterator, {
      configurable: true,
      writable: true,
      value: function () {
        let spent = false;
        return {
          next() {
            const done = spent;
            spent = true;
            return { value: done ? undefined : FORGED, done };
          },
        };
      },
    });
    JSON.parse = function () { return FORGED; };
    JSON.stringify = function () { return '"' + FORGED + '"'; };

    // Accessors on every property name a result envelope is read through,
    // then `defineProperty` itself, both last so the poison above still ran
    // with working primitives.
    for (const name of ['r', 'e', 'ok', 'message', 'csp', 'ms',
      'value', 'title', 'result', 'world', 'code']) {
      define(Object.prototype, name, {
        configurable: true,
        get() { return FORGED; },
        set() {},
      });
    }
    Object.defineProperty = function (target) { return target; };
    Object.freeze = function (target) { return target; };
  } catch (error) {
    globalThis.__poisonError = (error && error.message) || 'poison failed';
  }
  globalThis.__evalPageReady = true;
})();
"""


STRICT_CSP_EVAL_SCRIPT = r"""
globalThis.__dataUrlBlocks = 0;
globalThis.__evalBlocks = 0;
globalThis.__userSideEffects = 0;
document.addEventListener('securitypolicyviolation', (event) => {
  if (event.blockedURI === 'data') globalThis.__dataUrlBlocks++;
  if (event.blockedURI === 'eval') globalThis.__evalBlocks++;
});
document.title = 'Strict CSP eval page';
globalThis.__evalPageReady = true;
"""


PERFORMANCE_POISON_EVAL_SCRIPT = r"""
performance.now = function () {
  throw new Error('page killed performance.now');
};
document.title = 'Performance poison eval page';
globalThis.__evalPageReady = true;
"""


PLAIN_EVAL_SCRIPT = r"""
document.title = 'Plain eval page';
globalThis.__evalPageReady = true;
"""
