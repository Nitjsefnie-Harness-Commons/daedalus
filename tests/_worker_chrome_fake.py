"""The inert browser APIs a Node-hosted service worker needs at boot.

Not a suite itself — run_tests.py only loads `test_*.py`.

Every harness that runs the shipped worker in a VM has to supply the
`scripting`, `debugger`, `runtime` and `alarms` surfaces even when the
scenario never touches them, because the worker registers listeners on
all four while loading. The text is the tail of a `chrome` object literal
and assumes a `messageListeners` array and an `eventTarget` factory in
scope, so `runtime.onMessage` records the worker's listener for the
harness's own `send()`.
"""

INERT_WORKER_APIS = r"""
  scripting: {
    executeScript: async () => { throw new Error('unavailable'); },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => { throw new Error('unavailable'); },
    detach: async () => {},
    sendCommand: async () => ({}),
  },
  runtime: {
    lastError: null,
    onMessage: eventTarget(messageListeners),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.0.0' }),
  },
  alarms: {
    onAlarm: eventTarget(),
    create() {},
  },
"""
