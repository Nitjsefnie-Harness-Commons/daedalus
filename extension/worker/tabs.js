/* exported handleCloseTab, handleOpenTab, handleOpenTabs, handleFocusTab */
/* exported handleNavigate, handleReload, handleInjectCss, handleRemoveCss */
/* exported handleExtReload, handleFetchTimings, handleExtTabs */
/* global VERSION, _fetchTimings, _hasNativeToBase64, postResult */

async function handleCloseTab(cmd) {
  if (!cmd.tabId && !cmd.tabIds) return postResult(cmd._execution, null, 'Missing tabId or tabIds', 'extension');
  let ids = cmd.tabIds || [cmd.tabId];
  ids = ids.map(id => typeof id === 'number' ? id : parseInt(id));
  const closed = [];
  const errors = [];
  for (const id of ids) {
    try {
      await chrome.tabs.remove(id);
      closed.push(id);
    } catch (e) {
      errors.push({ id, error: e.message });
    }
  }
  // onRemoved listener handles unregister + sync
  await postResult(cmd._execution, { closed, errors }, null, 'extension');
}

async function handleFetchTimings(cmd) {
  const timings = _fetchTimings.slice();
  if (cmd.reset) _fetchTimings.length = 0;
  await postResult(cmd._execution, { timings, hasNativeToBase64: _hasNativeToBase64, count: timings.length }, null, 'extension');
}

async function handleExtReload(cmd) {
  // Post result before reloading — reload kills the service worker
  await postResult(cmd._execution, { reloading: true, version: VERSION }, null, 'extension');
  // Small delay to ensure result POST completes
  setTimeout(() => chrome.runtime.reload(), 500);
}

async function handleOpenTab(cmd) {
  try {
    if (!cmd.url) return postResult(cmd._execution, null, 'Missing url', 'extension');
    const opts = { url: cmd.url };
    if (cmd.active !== undefined) opts.active = cmd.active;
    if (cmd.pinned) opts.pinned = true;
    if (cmd.windowId) opts.windowId = typeof cmd.windowId === 'number' ? cmd.windowId : parseInt(cmd.windowId);
    const t0 = Date.now();
    const tab = await chrome.tabs.create(opts);
    const create_ms = Date.now() - t0;
    await postResult(cmd._execution, { tabId: tab.id, url: tab.url, windowId: tab.windowId, create_ms }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleOpenTabs(cmd) {
  try {
    const urls = Array.isArray(cmd.urls) ? cmd.urls : [];
    if (urls.length === 0) return postResult(cmd._execution, null, 'Missing urls', 'extension');
    const baseOpts = {};
    if (cmd.active !== undefined) baseOpts.active = cmd.active;
    if (cmd.pinned) baseOpts.pinned = true;
    if (cmd.windowId) baseOpts.windowId = typeof cmd.windowId === 'number' ? cmd.windowId : parseInt(cmd.windowId);
    // Dispatch every create before awaiting any. chrome.tabs.create resolves in
    // ~150ms, so awaiting them one at a time made a 6-tab call take ~1s with a
    // visible stagger between the first and last tab. The create IPCs stay
    // ordered, so the resulting tab order still follows `urls`.
    const t0 = Date.now();
    const settled = await Promise.allSettled(
      urls.map(url => chrome.tabs.create({ ...baseOpts, url }))
    );
    // create_ms is the create phase alone. roundtrip_ms - create_ms is everything
    // else (queue + SSE + registry traffic + the result POST), so the two together
    // account for the whole and a gap points at an unmeasured phase.
    const create_ms = Date.now() - t0;
    const opened = [];
    const errors = [];
    settled.forEach((r, i) => {
      if (r.status === 'fulfilled') {
        opened.push({ tabId: r.value.id, url: r.value.url, windowId: r.value.windowId });
      } else {
        errors.push({ url: urls[i], error: r.reason?.message || String(r.reason) });
      }
    });
    // No per-tab registerTab here: onCreated already schedules a full registry
    // sync that covers every one of them, and doing both meant N extra
    // chrome.tabs.get + POST /register round trips per call.
    await postResult(cmd._execution, { opened, errors, create_ms }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleFocusTab(cmd) {
  try {
    if (!cmd.tabId) return postResult(cmd._execution, null, 'Missing tabId', 'extension');
    const tabId = typeof cmd.tabId === 'number' ? cmd.tabId : parseInt(cmd.tabId);
    const tab = await chrome.tabs.update(tabId, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
    await postResult(cmd._execution, { tabId: tab.id, windowId: tab.windowId }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleNavigate(cmd) {
  try {
    if (!cmd.url) return postResult(cmd._execution, null, 'Missing url', 'extension');
    let tabId = cmd.tabId;
    if (!tabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      tabId = active.id;
    }
    tabId = typeof tabId === 'number' ? tabId : parseInt(tabId);
    const tab = await chrome.tabs.update(tabId, { url: cmd.url });
    await postResult(cmd._execution, { tabId: tab.id, url: cmd.url }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleReload(cmd) {
  try {
    let tabId = cmd.tabId;
    if (!tabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      tabId = active.id;
    }
    tabId = typeof tabId === 'number' ? tabId : parseInt(tabId);
    await chrome.tabs.reload(tabId, { bypassCache: !!cmd.bypassCache });
    await postResult(cmd._execution, { tabId, bypassCache: !!cmd.bypassCache }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleInjectCss(cmd) {
  try {
    if (!cmd.css) return postResult(cmd._execution, null, 'Missing css', 'extension');
    let tabId = cmd.tabId;
    if (!tabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      tabId = active.id;
    }
    tabId = typeof tabId === 'number' ? tabId : parseInt(tabId);
    await chrome.scripting.insertCSS({
      target: { tabId, allFrames: !!cmd.allFrames },
      css: cmd.css,
    });
    await postResult(cmd._execution, { tabId, injected: cmd.css.length }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleRemoveCss(cmd) {
  try {
    if (!cmd.css) return postResult(cmd._execution, null, 'Missing css', 'extension');
    let tabId = cmd.tabId;
    if (!tabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      tabId = active.id;
    }
    tabId = typeof tabId === 'number' ? tabId : parseInt(tabId);
    await chrome.scripting.removeCSS({
      target: { tabId, allFrames: !!cmd.allFrames },
      css: cmd.css,
    });
    await postResult(cmd._execution, { tabId, removed: cmd.css.length }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleExtTabs(cmd) {
  try {
    const tabs = await chrome.tabs.query({});
    const result = tabs.map(t => ({
      id: t.id, url: t.url, title: t.title,
      active: t.active, windowId: t.windowId,
    }));
    await postResult(cmd._execution, result, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}
