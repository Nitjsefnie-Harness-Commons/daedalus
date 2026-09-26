"""The tail of a fake `chrome` object literal for a Node-hosted worker.

Not a suite itself — run_tests.py only loads `test_*.py`. The text assumes
a `messageListeners` array and an `eventTarget` factory in scope, so
`runtime.onMessage` records the worker's listener for the harness's own
`send()`. The factory is the shared `event_target_stub`, which every
caller of this text splices in from `_worker_sources`.
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
