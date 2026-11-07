"""Render an exec result's `_timing` stamps as legs on the server's clock.

Server stamps are integer us, browser stamps float ms. Each exec bounds the
browser-minus-server offset by `sw_recv - srv_write` (above) and `sw_post -
srv_result` (below); a bridge's `Window` intersects its recent bounds, and
the midpoint, applied once, places a leg that crosses the two clocks.
"""
import collections
import time

MAX_SAMPLES = 64
MAX_AGE_S = 600

ORDER = (
    'srv_enqueue_us', 'srv_write_us', 'sw_recv', 'sw_probe_call', 'pg_probe',
    'sw_probe_done', 'sw_probe_failed', 'sw_eval_call', 'pg_eval_start',
    'pg_eval_end', 'sw_eval_done', 'sw_eval_failed', 'sw_cdp_attach',
    'sw_cdp_ready', 'sw_cdp_eval_done', 'sw_relay_send', 'cs_recv',
    'pg_recv', 'pg_done', 'cs_result', 'sw_relay_result', 'sw_post',
    'srv_result_us', 'mcp_read_us')
CONSUMED = frozenset(ORDER) | {'sw_tick'}
_COMMON = ('srv_enqueue_us', 'srv_write_us', 'sw_recv', 'sw_probe_call',
           'sw_post', 'srv_result_us', 'mcp_read_us')
REQUIRED = {
    None: _COMMON,
    'page-main': _COMMON + (
        'sw_eval_call', 'pg_eval_start', 'pg_eval_end', 'sw_eval_done'),
    'cdp': _COMMON + ('sw_cdp_attach', 'sw_cdp_ready', 'sw_cdp_eval_done'),
    'relay': _COMMON + ('sw_relay_send', 'cs_recv', 'pg_recv', 'pg_done',
                        'cs_result', 'sw_relay_result'),
}


def _server(name):
    return name.startswith(('srv_', 'mcp_'))


def _world(stamps):
    for world, marker in (('relay', 'sw_relay_send'),
                          ('cdp', 'sw_cdp_attach'),
                          ('page-main', 'sw_eval_call')):
        if marker in stamps:
            return world
    return None


class Window:
    """One bridge's recent offset bounds, aged on a monotonic clock."""

    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._samples = collections.deque(maxlen=MAX_SAMPLES)

    def bounds(self, lo, hi):
        """Add this exec's bounds; answer `(lo, hi, samples, consistent)`."""
        now = self._clock()
        while self._samples and now - self._samples[0][0] > MAX_AGE_S:
            self._samples.popleft()
        self._samples.append((now, lo, hi))
        low = max(sample[1] for sample in self._samples)
        high = min(sample[2] for sample in self._samples)
        if low <= high:
            return low, high, len(self._samples), True
        return lo, hi, 1, False


def _bounds(stamps):
    try:
        return (stamps['sw_post'] - stamps['srv_result_us'] / 1000,
                stamps['sw_recv'] - stamps['srv_write_us'] / 1000)
    except KeyError:
        return None


def _leg(a, b, stamps, theta):
    clock = ('server' if _server(a) and _server(b) else
             'browser' if not (_server(a) or _server(b)) else 'offset')
    leg = {'leg': f'{a} -> {b}', 'clock': clock, 'ms': None, 'raw_ms': None,
           'clamped': False}
    if clock == 'offset' and theta is None:
        return leg, None
    shift = 0 if clock == 'browser' else theta
    raw = _on_server(b, stamps[b], shift) - _on_server(a, stamps[a], shift)
    shown = max(raw, 0.0)
    leg.update(ms=round(shown, 1), raw_ms=round(raw, 1), clamped=raw < 0)
    return leg, shown


def _on_server(name, value, theta):
    return value / 1000 if _server(name) else value - theta


def render(timing, mcp_read_us, window):
    """`timing` is the result's `_timing`, `mcp_read_us` the MCP's own
    server-clock read once the result arrived, `window` its bridge's."""
    stamps = {name: value for name, value in (
        timing.items() if isinstance(timing, dict) else ())
        if isinstance(value, (int, float)) and not isinstance(value, bool)}
    stamps['mcp_read_us'] = mcp_read_us
    theta = offset = None
    bounds = _bounds(stamps)
    if bounds is not None:
        lo, hi, samples, consistent = window.bounds(*bounds)
        theta = (hi + lo) / 2
        offset = {'estimate_ms': round(theta, 1),
                  'uncertainty_ms': round((hi - lo) / 2, 1),
                  'lo_ms': round(lo, 1), 'hi_ms': round(hi, 1),
                  'consistent': consistent, 'samples': samples}
    present = [name for name in ORDER if name in stamps]
    legs = [_leg(a, b, stamps, theta) for a, b in zip(present, present[1:])]
    world = _world(stamps)
    return {
        'world': world,
        'offset': offset,
        'legs': [leg for leg, _ in legs],
        'missing': [name for name in ORDER
                    if name in REQUIRED[world] and name not in stamps],
        'tick_ms': (round(stamps['sw_tick'] - stamps['sw_recv'], 1)
                    if {'sw_tick', 'sw_recv'} <= stamps.keys() else None),
        'parts_ms': round(sum(ms for _, ms in legs if ms is not None), 1),
        'total_ms': (round((mcp_read_us - stamps['srv_enqueue_us']) / 1000, 1)
                     if 'srv_enqueue_us' in stamps else None),
    }
