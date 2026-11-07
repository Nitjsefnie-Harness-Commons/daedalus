/* exported _timingFor, _stamp, _copyStamps, _stampReceipt */

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
  // Worker loop lag.
  setTimeout(() => _stamp(timing, 'sw_tick'), 0);
}
