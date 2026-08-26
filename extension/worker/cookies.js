async function handleCookies(cmd) {
  try {
    const details = {};
    if (cmd.domain) details.domain = cmd.domain;
    if (cmd.url) details.url = cmd.url;
    const cookies = await chrome.cookies.getAll(details);
    await postResult(cmd._execution, cookies, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleSetCookie(cmd) {
  try {
    const details = { url: cmd.url, name: cmd.name, value: cmd.value };
    if (cmd.domain) details.domain = cmd.domain;
    if (cmd.path) details.path = cmd.path;
    if (cmd.httpOnly !== undefined) details.httpOnly = cmd.httpOnly;
    if (cmd.secure !== undefined) details.secure = cmd.secure;
    if (cmd.sameSite) details.sameSite = cmd.sameSite;
    if (cmd.expirationDate) details.expirationDate = cmd.expirationDate;
    const cookie = await chrome.cookies.set(details);
    await postResult(cmd._execution, cookie, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleRemoveCookie(cmd) {
  try {
    if (!cmd.url || !cmd.name) return postResult(cmd._execution, null, 'Missing url or name', 'extension');
    const result = await chrome.cookies.remove({ url: cmd.url, name: cmd.name });
    await postResult(cmd._execution, result, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleClearCookies(cmd) {
  try {
    const details = {};
    if (cmd.domain) details.domain = cmd.domain;
    if (cmd.url) details.url = cmd.url;
    const cookies = await chrome.cookies.getAll(details);
    let removed = 0;
    const failed = [];
    for (const c of cookies) {
      const protocol = c.secure ? 'https' : 'http';
      const url = `${protocol}://${c.domain.replace(/^\./, '')}${c.path}`;
      const target = { url, name: c.name };
      // A partitioned cookie is matched only when its partition is named, and
      // a cookie in a non-default store only by that store. Dropping either
      // left the cookie in place -- and the count was incremented per
      // iteration rather than per removal, so it said the cookie had gone.
      if (c.partitionKey) target.partitionKey = c.partitionKey;
      if (c.storeId) target.storeId = c.storeId;
      if (await chrome.cookies.remove(target)) removed++;
      else failed.push(c.name);
    }
    await postResult(cmd._execution, { removed, failed }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}
