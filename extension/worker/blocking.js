/* exported handleBlockRequests, handleUnblockRequests */
/* exported handleListBlockRules */
/* global postResult, _serializer */

// ─── Request blocking via declarativeNetRequest ───

const BLOCK_RULE_BASE_ID = 9000; // Dynamic rule IDs start here
let blockRuleCounter = 0;
const _withBlockRuleLock = _serializer();

// Session rules survive service-worker suspension but `blockRuleCounter` does
// not, so a restarted worker would re-issue an id that is still installed and
// updateSessionRules would reject the duplicate. Seed the counter from the
// rules that are actually present before allocating.
async function _nextBlockRuleId() {
  const existing = await chrome.declarativeNetRequest.getSessionRules();
  let highest = BLOCK_RULE_BASE_ID + blockRuleCounter;
  for (const rule of existing) {
    if (typeof rule.id === 'number' && rule.id > highest) highest = rule.id;
  }
  blockRuleCounter = highest - BLOCK_RULE_BASE_ID;
  return BLOCK_RULE_BASE_ID + (++blockRuleCounter);
}

async function handleBlockRequests(cmd) {
  try {
    if (!cmd.pattern) return postResult(cmd._execution, null, 'Missing pattern', 'extension');
    // SAFETY: use session-scoped rules (required for tabIds support).
    // Always scope to tab IDs so only page-originated requests are blocked —
    // never extension service worker fetches (SSE, relay, result POSTs).
    let tabIds;
    if (cmd.tabId) {
      tabIds = [typeof cmd.tabId === 'number' ? cmd.tabId : parseInt(cmd.tabId)];
    } else {
      // No tab specified — block in ALL current tabs (but not the service worker)
      const tabs = await chrome.tabs.query({});
      tabIds = tabs.map(t => t.id).filter(id => id > 0);
    }
    // Extract server hostname to always exclude from blocking
    let serverHost;
    try {
      serverHost = new URL(cmd._execution.resultRoute.serverUrl).hostname;
    } catch (_) {}
    const rule = {
      priority: 1,
      action: { type: 'block' },
      condition: {
        urlFilter: cmd.pattern,
        resourceTypes: ['xmlhttprequest', 'media', 'other'],
        tabIds,
        excludedRequestDomains: serverHost ? [serverHost] : [],
      },
    };
    // Allocate and install under one lock: two concurrent adds that both read
    // the rule list before either wrote would otherwise pick the same id.
    const ruleId = await _withBlockRuleLock(async () => {
      const id = await _nextBlockRuleId();
      await chrome.declarativeNetRequest.updateSessionRules({
        addRules: [{ id, ...rule }],
        removeRuleIds: [],
      });
      return id;
    });
    await postResult(cmd._execution, { ruleId, pattern: cmd.pattern, tabIds }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleUnblockRequests(cmd) {
  try {
    if (cmd.ruleId !== undefined && cmd.ruleId !== null) {
      // Remove specific rule. A PRESENT id is never a request to remove
      // everything, however malformed it is: `if (cmd.ruleId)` was false for
      // 0, so the narrowest request fell through to the branch below and
      // destroyed every session rule while reporting them as removed.
      const ids = (Array.isArray(cmd.ruleId) ? cmd.ruleId : [cmd.ruleId]).map(Number);
      if (!ids.length || ids.some((id) => !Number.isInteger(id) || id <= 0)) {
        await postResult(cmd._execution, null,
          'ruleId must be a positive integer', 'extension');
        return;
      }
      await chrome.declarativeNetRequest.updateSessionRules({
        removeRuleIds: ids,
        addRules: [],
      });
      await postResult(cmd._execution, { removed: ids }, null, 'extension');
    } else {
      // Remove all session block rules
      const existing = await chrome.declarativeNetRequest.getSessionRules();
      const ids = existing.map(r => r.id);
      if (ids.length > 0) {
        await chrome.declarativeNetRequest.updateSessionRules({
          removeRuleIds: ids,
          addRules: [],
        });
      }
      await postResult(cmd._execution, { removed: ids }, null, 'extension');
    }
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleListBlockRules(cmd) {
  try {
    const rules = await chrome.declarativeNetRequest.getSessionRules();
    await postResult(cmd._execution, rules, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}
