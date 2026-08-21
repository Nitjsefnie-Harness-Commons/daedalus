// Destroy a page's HLS.js instance.
//
// Why: an HLS.js player that can no longer reach its segments does not stop
// asking. It retries hard enough to saturate Chrome's per-host connection
// pool, and every other request the page makes then queues behind it — so a
// script doing its own fetching in the same tab crawls for reasons that look
// like network trouble rather than like a player nobody told to stop.
//
// Finding the instance: players rarely expose it at a documented path, so this
// scans `window` two levels deep for an object carrying both `.destroy()` and
// `.levels`, which together are an HLS.js hallmark. `stopLoad()` and
// `detachMedia()` come first because `destroy()` alone can leave an in-flight
// fragment request running.

const destroyed = [];

const kill = (hls, path) => {
  if (!hls || typeof hls.destroy !== 'function') return false;
  try { hls.stopLoad && hls.stopLoad(); } catch(e) {}
  try { hls.detachMedia && hls.detachMedia(); } catch(e) {}
  try { hls.destroy(); } catch(e) { destroyed.push(path + ':error(' + e.message + ')'); return false; }
  destroyed.push(path);
  return true;
};

// Depth-2 scan: window.X.Y where Y has .destroy + .levels
for (const k of Object.keys(window)) {
  try {
    const v = window[k];
    if (!v || typeof v !== 'object') continue;
    for (const kk of Object.keys(v)) {
      try {
        const vv = v[kk];
        if (vv && typeof vv === 'object' && typeof vv.destroy === 'function' && vv.levels) {
          if (kill(vv, 'window.' + k + '.' + kk)) { v[kk] = null; }
        }
      } catch(e) {}
    }
  } catch(e) {}
}

return destroyed.length ? 'killed: ' + destroyed.join(', ') : 'no hls instance found';
