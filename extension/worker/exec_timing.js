/* exported _timingFor, _stamp, _copyStamps, _stampReceipt */

// The bridge turns timing on per command by sending a `_timing` object;
// without one nothing here reads a clock.
function _timingFor(cmd) {
  const timing = cmd._timing;
  return typeof timing === 'object' && !Array.isArray(timing) ? timing : null;
}

function _browserNow() {
  return performance.timeOrigin + performance.now();
}

function _stamp(timing, name) {
  if (timing) timing[name] = _browserNow();
}

// Stamps another realm took; only numbers, since that realm may be the page.
function _copyStamps(timing, from, names) {
  if (!timing) return;
  for (const name of names) {
    if (typeof from?.[name] === 'number') timing[name] = from[name];
  }
}

function _stampReceipt(cmd) {
  const timing = _timingFor(cmd);
  if (!timing) return;
  _stamp(timing, 'sw_recv');
  // How long a zero-delay timer queued at receipt waits: worker loop lag.
  setTimeout(() => _stamp(timing, 'sw_tick'), 0);
}
