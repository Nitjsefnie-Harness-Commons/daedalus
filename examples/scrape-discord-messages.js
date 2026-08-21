// Discord scraper — runs inside an open Discord tab via Daedalus put.
// Action dispatched via window.__SCRAPE_ARGS set in a wrapper script
// (since put cannot pass args). Default action: scroll-back from current
// view until oldest visible message is older than CUTOFF_MS, then return
// all unique messages.
const args = window.__SCRAPE_ARGS || {};
const action = args.action || "scroll-back";

function chatScroller() {
  return document.querySelector("[data-list-id=chat-messages]")
    || document.querySelector("ol[class*=scrollerInner]")?.parentElement
    || document.querySelector("[class*=messagesWrapper] [class*=scroller]");
}

function extract() {
  const items = document.querySelectorAll("li[id^=chat-messages-]");
  const out = [];
  for (const li of items) {
    const contentEl = li.querySelector("[id^=message-content-]");
    if (!contentEl) continue;
    const msgId = contentEl.id.replace("message-content-", "");
    const time = li.querySelector("time")?.getAttribute("datetime") || null;
    const userEl = li.querySelector("[id^=message-username-]") || li.previousElementSibling?.querySelector("[id^=message-username-]");
    // Walk up siblings until we find the group header for username
    let user = userEl ? userEl.textContent : null;
    if (!user) {
      let prev = li.previousElementSibling;
      while (prev) {
        const u = prev.querySelector?.("[id^=message-username-]");
        if (u) { user = u.textContent; break; }
        prev = prev.previousElementSibling;
      }
    }
    const replyContent = li.querySelector("[id^=message-reply-context-]")?.textContent || null;
    const attachments = [...li.querySelectorAll("a[class*=originalLink], img[class*=embedImage], video")]
      .map(a => a.href || a.src).filter(Boolean);
    out.push({
      id: msgId,
      ts: time,
      user,
      text: contentEl.textContent,
      reply: replyContent,
      attachments,
    });
  }
  return out;
}

function waitForScrollSettle(scroller, prevTopId, timeoutMs) {
  return new Promise((resolve) => {
    let resolved = false;
    const t = setTimeout(() => { if (!resolved) { resolved = true; obs.disconnect(); resolve("timeout"); } }, timeoutMs);
    const obs = new MutationObserver(() => {
      const top = document.querySelector("li[id^=chat-messages-] [id^=message-content-]")?.id;
      if (top && top !== prevTopId) {
        if (!resolved) { resolved = true; clearTimeout(t); obs.disconnect(); setTimeout(() => resolve("ok"), 150); }
      }
    });
    obs.observe(scroller, { childList: true, subtree: true });
  });
}

async function scrollBackUntil(cutoffIso) {
  const all = new Map();
  const cutoff = Date.parse(cutoffIso);
  const scroller = chatScroller();
  if (!scroller) return { error: "no chat scroller found" };
  let oldestTs = null;
  let iterations = 0;
  const MAX_ITER = 200;
  while (iterations < MAX_ITER) {
    iterations++;
    for (const m of extract()) all.set(m.id, m);
    const sorted = [...all.values()].sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
    oldestTs = sorted[0]?.ts;
    if (oldestTs && Date.parse(oldestTs) <= cutoff) break;
    const topMsgId = document.querySelector("li[id^=chat-messages-] [id^=message-content-]")?.id;
    scroller.scrollTop = 0;
    const r = await waitForScrollSettle(scroller, topMsgId, 6000);
    if (r === "timeout") break;
  }
  return {
    iterations,
    oldestTs,
    count: all.size,
    msgs: [...all.values()].sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts)),
  };
}

if (action === "scroll-back") {
  return await scrollBackUntil(args.cutoffIso);
}
if (action === "extract-only") {
  return extract();
}
return { error: "unknown action " + action };
