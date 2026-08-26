/* exported handleScreenshot */
/* global bridgeHeaders, postResult, _serializer */

async function handleScreenshot(cmd) {
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId || chromeTabId === 'extension') {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    const tab = await chrome.tabs.get(typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId));
    const fmt = cmd.format || 'png';
    // captureVisibleTab photographs whatever is ACTIVE in the window it is
    // given, so a tab that is merely named is not the tab that gets captured:
    // a screenshot aimed at an inactive tab returned the active sibling's
    // pixels under the requested tab's url and title. Bring the target
    // forward, capture, and put the window back as it was found.
    //
    // Serialized, because two captures interleaving would each restore the
    // other's tab and both would photograph the wrong page.
    const dataUrl = await _captureQueue(async () => {
      let restore = null;
      if (!tab.active) {
        const [previous] = await chrome.tabs.query({
          active: true, windowId: tab.windowId,
        });
        if (previous && previous.id !== tab.id) restore = previous.id;
        await chrome.tabs.update(tab.id, { active: true });
      }
      try {
        return await chrome.tabs.captureVisibleTab(tab.windowId, {
          format: fmt,
          quality: cmd.quality || 80,
        });
      } finally {
        if (restore !== null) await chrome.tabs.update(restore, { active: true });
      }
    });
    const base64 = dataUrl.replace(/^data:image\/\w+;base64,/, '');
    const uploadResp = await fetch(cmd._execution.resultRoute.serverUrl + '/upload', {
      method: 'POST',
      headers: bridgeHeaders(cmd._execution.resultRoute.token),
      body: JSON.stringify({
        token: cmd._execution.resultRoute.token,
        id: cmd.id,
        data: base64,
        format: fmt,
      }),
    });
    let uploadResult = null;
    try {
      uploadResult = await uploadResp.json();
    } catch (_) {}
    // A rejected upload stored nothing. Reporting success here would hand the
    // caller an envelope with no path and no size but `error: null`.
    if (!uploadResp.ok) {
      const detail = (uploadResult && uploadResult.error)
        || 'HTTP ' + uploadResp.status;
      await postResult(
        cmd._execution, null, 'Screenshot upload failed: ' + detail,
        'extension');
      return;
    }
    await postResult(cmd._execution, {
      path: uploadResult && uploadResult.path,
      size: uploadResult && uploadResult.size,
      format: fmt, tabUrl: tab.url, tabTitle: tab.title,
    }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

// Captures activate the tab they photograph, so they cannot overlap.
const _captureQueue = _serializer();
