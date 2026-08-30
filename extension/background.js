// Daedalus Extension — background service worker
// @version 0.23.0
/* global handleScreenshot */
/* global handleCookies, handleSetCookie */
/* global handleRemoveCookie, handleClearCookies */
/* global handleBlockRequests, handleUnblockRequests */
/* global handleListBlockRules */
/* global handleCloseTab, handleOpenTab, handleOpenTabs, handleFocusTab */
/* global handleNavigate, handleReload, handleInjectCss, handleRemoveCss */
/* global handleExtReload, handleFetchTimings, handleExtTabs */
/* global handleCdp */
/* global handleNetCapture, handleNetCaptureStop */
/* global handleNetCaptureGet */
/* global handleStoreHotfix, handleClearHotfix */
/* global handleClearAllHotfixes, handleSetPermanent */
/* global handleListHotfixes */
/* global handleEval */
/* global config, loadConfig, _executionContext, postResult */
/* global registerAllTabs */
/* global sseAbort, startStream, ensureKeepAlive */
/* exported DEFAULT_SERVER, dispatchCommand */

const VERSION = '0.23.0';
// No default server. A bridge URL is deployment-specific, and a build that
// ships someone's hostname would have every install of it call home to that
// host. The extension stays idle until a URL is set in its options page.
/* eslint-disable-next-line no-unused-vars */
const DEFAULT_SERVER = '';

importScripts(
  'worker/util.js',
  'worker/config.js',
  'worker/registry.js',
  'worker/capture.js',
  'worker/cookies.js',
  'worker/blocking.js',
  'worker/tabs.js',
  'worker/cdp.js',
  'worker/netcapture.js',
  'worker/hotfixes.js',
  'worker/evaluate.js',
  'worker/stream.js',
  'worker/messaging.js');

// ─── Command dispatch ───

/* eslint-disable-next-line no-unused-vars */
function dispatchCommand(receivedCommand) {
  const cmd = Object.freeze({
    ...receivedCommand,
    _execution: _executionContext(receivedCommand),
  });
  const type = cmd.type || (cmd.code ? 'eval' : 'unknown');
  switch (type) {
    case 'screenshot': return handleScreenshot(cmd);
    case 'cookies': return handleCookies(cmd);
    case 'set-cookie': return handleSetCookie(cmd);
    case 'remove-cookie': return handleRemoveCookie(cmd);
    case 'clear-cookies': return handleClearCookies(cmd);
    case 'block-requests': return handleBlockRequests(cmd);
    case 'unblock-requests': return handleUnblockRequests(cmd);
    case 'list-block-rules': return handleListBlockRules(cmd);
    case 'fetch-timings': return handleFetchTimings(cmd);
    case 'ext-reload': return handleExtReload(cmd);
    case 'open-tab': return handleOpenTab(cmd);
    case 'open-tabs': return handleOpenTabs(cmd);
    case 'focus-tab': return handleFocusTab(cmd);
    case 'navigate': return handleNavigate(cmd);
    case 'reload': return handleReload(cmd);
    case 'close-tab': return handleCloseTab(cmd);
    case 'inject-css': return handleInjectCss(cmd);
    case 'remove-css': return handleRemoveCss(cmd);
    case 'cdp': return handleCdp(cmd);
    case 'net-capture': return handleNetCapture(cmd);
    case 'net-capture-stop': return handleNetCaptureStop(cmd);
    case 'net-capture-get': return handleNetCaptureGet(cmd);
    case 'store-hotfix': return handleStoreHotfix(cmd);
    case 'clear-hotfix': return handleClearHotfix(cmd);
    case 'clear-all-hotfixes': return handleClearAllHotfixes(cmd);
    case 'list-hotfixes': return handleListHotfixes(cmd);
    case 'set-permanent': return handleSetPermanent(cmd);
    case 'tabs': return handleExtTabs(cmd);
    case 'eval': return handleEval(cmd);
    default: return postResult(cmd._execution, null, 'Unknown command type: ' + type, 'extension');
  }
}

// ─── Alarms: survive MV3 service worker kills ───

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === 'daedalus-heartbeat') {
    // Ensure config is loaded (race: alarm can fire before loadConfig resolves on worker restart)
    if (!config.token) await loadConfig();
    ensureKeepAlive();
    registerAllTabs();
    // Restart stream if dead
    if (!sseAbort && config.token) {
      console.log('[Daedalus] Heartbeat: stream dead, restarting');
      startStream();
    }
  }
});

// ─── Keep-alive: content script ports prevent service worker termination ───

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === 'keepalive') {
    // Hold the port open — as long as any content script has a port,
    // Chrome keeps the service worker alive. Port messages reset the
    // 5-minute timeout Chrome imposes on long-lived ports.
    port.onMessage.addListener(() => {
      // Receiving a ping keeps the SW alive and resets port timeout
    });
    port.onDisconnect.addListener(() => {});
    // Also ensure stream is running — if we just got revived, restart it
    if (!sseAbort && config.token) {
      console.log('[Daedalus] Keep-alive connect: restarting stream');
      startStream();
    }
  }
});

// ─── Boot ───

loadConfig().then(() => {
  console.log(`[Daedalus] Extension v${VERSION} — token: ${config.token.substring(0, 8)}...`);
  ensureKeepAlive();
  startStream();
  registerAllTabs();
  // chrome.alarms survives service worker kills (setInterval does not)
  chrome.alarms.create('daedalus-heartbeat', { periodInMinutes: 0.5 });
});
