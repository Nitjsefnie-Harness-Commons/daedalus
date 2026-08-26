/* exported _netCaptures, handleNetCapture */
/* exported handleNetCaptureStop, handleNetCaptureGet */
/* global _cdpSessions, postResult */

// ─── Network capture (CDP) ───

const _netCaptures = {}; // tabId → { requests: [], maxRequests }

// A capture buffer lives in the service worker and grows to hold headers and
// response bodies, so its size is a memory budget rather than a preference.
// `cmd.maxRequests || 1000` accepted anything a caller sent: -1 was kept and
// evicted the only event on arrival, leaving an empty capture, and 1e9 was
// kept and buffered everything.
const NET_CAPTURE_DEFAULT = 1000;
const NET_CAPTURE_MAX = 20000;

function _netCaptureLimit(value) {
  if (value === undefined || value === null || value === '') return NET_CAPTURE_DEFAULT;
  const limit = Number(value);
  if (!Number.isInteger(limit) || limit < 1) {
    throw new Error(`maxRequests must be an integer from 1 to ${NET_CAPTURE_MAX}`);
  }
  return Math.min(limit, NET_CAPTURE_MAX);
}

function _netEventHandler(source, method, params) {
  const tabId = source.tabId;
  const cap = _netCaptures[tabId];
  if (!cap) return;

  if (method === 'Network.requestWillBeSent') {
    const entry = {
      requestId: params.requestId,
      url: params.request.url,
      method: params.request.method,
      headers: params.request.headers,
      postData: params.request.postData || null,
      type: params.type || '',
      frameId: params.frameId || '',
      ts: params.wallTime || (params.timestamp ? params.timestamp * 1000 : Date.now()),
      initiator: params.initiator ? (params.initiator.url || params.initiator.type || '') : '',
    };
    cap.requests.push(entry);
    // Find matching entry to attach response later
  } else if (method === 'Network.responseReceived') {
    const entry = cap.requests.find(r => r.requestId === params.requestId);
    if (entry) {
      entry.status = params.response.status;
      entry.statusText = params.response.statusText || '';
      entry.responseHeaders = params.response.headers || {};
      entry.mimeType = params.response.mimeType || '';
      entry.responseUrl = params.response.url || '';
    }
  } else if (method === 'Network.loadingFinished') {
    const entry = cap.requests.find(r => r.requestId === params.requestId);
    if (entry) {
      entry.done = true;
      entry.encodedLength = params.encodedDataLength || 0;
    }
  }

  // Evict oldest if over limit
  if (cap.requests.length > cap.maxRequests) {
    cap.requests.shift();
  }
}

async function handleNetCapture(cmd) {
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    chromeTabId = typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId);
    // Before the attach: a limit this worker will not honour is not a reason
    // to attach a debugger to someone's tab. Throws, and the catch below
    // reports it to the caller.
    const limit = _netCaptureLimit(cmd.maxRequests);

    if (_netCaptures[chromeTabId]) {
      return postResult(cmd._execution, { already: true, tabId: chromeTabId, buffered: _netCaptures[chromeTabId].requests.length }, null, 'extension');
    }

    // Publish the capture only once attach AND Network.enable have succeeded.
    // A half-set-up capture would make the next call answer `already: true`
    // over a tab nothing is attached to, and leak the attachment when
    // Network.enable is what failed.
    let attached = false;
    try {
      await chrome.debugger.attach({ tabId: chromeTabId }, '1.3');
      attached = true;
      await chrome.debugger.sendCommand({ tabId: chromeTabId }, 'Network.enable', {});
    } catch (e) {
      if (attached) {
        try { await chrome.debugger.detach({ tabId: chromeTabId }); } catch (_) {}
      }
      return postResult(cmd._execution, null, e.message, 'extension');
    }
    _netCaptures[chromeTabId] = { requests: [], maxRequests: limit };
    await postResult(cmd._execution, { capturing: true, tabId: chromeTabId }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleNetCaptureStop(cmd) {
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    chromeTabId = typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId);

    const cap = _netCaptures[chromeTabId];
    if (!cap) return postResult(cmd._execution, { stopped: false, reason: 'not capturing' }, null, 'extension');

    // Optionally fetch response bodies before stopping
    if (cmd.bodies) {
      for (const entry of cap.requests) {
        if (entry.done && !entry.body) {
          try {
            const resp = await chrome.debugger.sendCommand({ tabId: chromeTabId }, 'Network.getResponseBody', { requestId: entry.requestId });
            entry.body = resp.body;
            entry.bodyBase64 = resp.base64Encoded || false;
          } catch (_) {}
        }
      }
    }

    const requests = cap.requests.slice();
    delete _netCaptures[chromeTabId];
    try { await chrome.debugger.detach({ tabId: chromeTabId }); } catch (_) {}
    await postResult(cmd._execution, { stopped: true, tabId: chromeTabId, count: requests.length, requests }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleNetCaptureGet(cmd) {
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    chromeTabId = typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId);

    const cap = _netCaptures[chromeTabId];
    if (!cap) return postResult(cmd._execution, null, 'Not capturing on this tab', 'extension');

    let requests = cap.requests;
    if (cmd.filter) {
      const pat = new RegExp(cmd.filter, 'i');
      requests = requests.filter(r => pat.test(r.url) || pat.test(r.type));
    }

    // Optionally fetch response bodies
    if (cmd.bodies) {
      for (const entry of requests) {
        if (entry.done && !entry.body) {
          try {
            const resp = await chrome.debugger.sendCommand({ tabId: chromeTabId }, 'Network.getResponseBody', { requestId: entry.requestId });
            entry.body = resp.body;
            entry.bodyBase64 = resp.base64Encoded || false;
          } catch (_) {}
        }
      }
    }

    await postResult(cmd._execution, { tabId: chromeTabId, count: requests.length, requests }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

// Wire up CDP event listener (once, globally)
chrome.debugger.onEvent.addListener(_netEventHandler);

// Clean up on tab close
chrome.tabs.onRemoved.addListener((tabId) => {
  if (_netCaptures[tabId]) {
    delete _netCaptures[tabId];
    try { chrome.debugger.detach({ tabId }); } catch (_) {}
  }
  if (_cdpSessions[tabId]) {
    delete _cdpSessions[tabId];
    try { chrome.debugger.detach({ tabId }); } catch (_) {}
  }
});

// Drop sticky CDP state when Chrome detaches us (DevTools opened, target
// crashed, etc.). A capture whose attachment is gone receives no further
// events, so it must not keep answering `already: true` either.
chrome.debugger.onDetach.addListener((source) => {
  if (source && source.tabId != null) {
    delete _cdpSessions[source.tabId];
    delete _netCaptures[source.tabId];
  }
});
