// A hotfix that records every request the page makes.
//
// Install it once and it survives extension version bumps:
//
//   daedalus store-hotfix request-log --file examples/request-log-hotfix.js --permanent
//
// then read what it collected from any tab:
//
//   daedalus exec log 'JSON.stringify(window.__requestLog.slice(-20))'
//
// This is the smallest example that actually needs everything a hotfix gives
// you, which is why it is the one shipped here rather than something shorter.
//
// MAIN world, because a content-script world has its own `fetch` and its own
// `XMLHttpRequest`. Patching those changes nothing about the calls the page's
// own code makes — the two worlds share the DOM and nothing else, so an
// isolated-world patch records an empty log and looks like a page that made no
// requests.
//
// document_start, because the page only has to stash one reference before you
// arrive for the patch to miss everything after it. A bundle whose first lines
// are `const f = window.fetch` keeps calling the original forever, and a patch
// installed at DOMContentLoaded is already too late to matter.
//
// Permanent, because a hotfix that has to be reinstalled after every extension
// reload is one you will forget to reinstall before the run that mattered.
//
// The log is deliberately capped and deliberately shallow: it holds metadata,
// never response bodies. An uncapped recorder on a long-lived tab is a memory
// leak with a plausible excuse, and a recorder that keeps bodies quietly turns
// every page you visit into a document you are now storing.

(function () {
  const LIMIT = 500;
  if (window.__requestLog) return;          // idempotent across re-injection
  const log = window.__requestLog = [];

  function record(entry) {
    log.push(entry);
    if (log.length > LIMIT) log.splice(0, log.length - LIMIT);
  }

  // fetch: wrap rather than replace, so anything the page does with the
  // returned promise still works. The `.then`/`.catch` pair records the
  // outcome without swallowing it — a recorder that eats a rejection turns a
  // failing request into a hang somewhere else in the page.
  const nativeFetch = window.fetch;
  if (typeof nativeFetch === 'function') {
    window.fetch = function (input, init) {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      const method = (init && init.method)
        || (input && input.method) || 'GET';
      const started = Date.now();
      const entry = { kind: 'fetch', method, url, started };
      record(entry);
      return nativeFetch.apply(this, arguments).then(
        (resp) => {
          entry.status = resp.status;
          entry.ms = Date.now() - started;
          return resp;
        },
        (err) => {
          entry.error = String((err && err.message) || err);
          entry.ms = Date.now() - started;
          throw err;
        });
    };
  }

  // XMLHttpRequest: still the transport for plenty of libraries, and invisible
  // to a fetch-only patch. open() carries the method and URL, send() is where
  // the clock starts, and loadend fires for success, failure and abort alike.
  const proto = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
  if (proto) {
    const open = proto.open;
    const send = proto.send;
    proto.open = function (method, url) {
      this.__logEntry = { kind: 'xhr', method: method, url: String(url) };
      return open.apply(this, arguments);
    };
    proto.send = function () {
      const entry = this.__logEntry;
      if (entry) {
        entry.started = Date.now();
        record(entry);
        this.addEventListener('loadend', () => {
          entry.status = this.status;
          entry.ms = Date.now() - entry.started;
        });
      }
      return send.apply(this, arguments);
    };
  }
})();
