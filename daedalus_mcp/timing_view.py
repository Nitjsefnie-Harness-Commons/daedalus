"""Render an exec result's `_timing` stamps as legs on the server's clock.

Server stamps are integer us, browser stamps float ms; the browser-minus-
server offset is bounded by `sw_recv - srv_write` (above) and `sw_post -
srv_result` (below), and its midpoint converts every browser stamp once.
"""

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


def _bounds(stamps):
    try:
        return (stamps['sw_recv'] - stamps['srv_write_us'] / 1000,
                stamps['sw_post'] - stamps['srv_result_us'] / 1000)
    except KeyError:
        return None


def _leg(a, b, at):
    clock = ('server' if _server(a) and _server(b) else
             'browser' if not (_server(a) or _server(b)) else 'offset')
    raw = None if at[a] is None or at[b] is None else at[b] - at[a]
    shown = None if raw is None else max(raw, 0.0)
    return {'leg': f'{a} -> {b}', 'clock': clock,
            'ms': None if raw is None else round(shown, 1),
            'raw_ms': None if raw is None else round(raw, 1),
            'clamped': raw is not None and raw < 0}, shown


def render(timing, mcp_read_us):
    """`timing` is the result's `_timing`; `mcp_read_us` the MCP's own
    server-clock read once the result arrived."""
    stamps = {name: value for name, value in (
        timing.items() if isinstance(timing, dict) else ())
        if isinstance(value, (int, float)) and not isinstance(value, bool)}
    stamps['mcp_read_us'] = mcp_read_us
    bounds = _bounds(stamps)
    theta = None if bounds is None else sum(bounds) / 2
    at = {name: value / 1000 if _server(name) else
          None if theta is None else value - theta
          for name, value in stamps.items()}
    present = [name for name in ORDER if name in stamps]
    legs = [_leg(a, b, at) for a, b in zip(present, present[1:])]
    world = _world(stamps)
    return {
        'world': world,
        'offset': None if bounds is None else {
            'estimate_ms': round(theta, 1),
            'uncertainty_ms': round((bounds[0] - bounds[1]) / 2, 1),
            'lo_ms': round(bounds[1], 1), 'hi_ms': round(bounds[0], 1),
            'consistent': bounds[0] >= bounds[1]},
        'legs': [leg for leg, _ in legs],
        'unreached': [name for name in ORDER
                      if name in REQUIRED[world] and name not in stamps],
        'tick_ms': (round(stamps['sw_tick'] - stamps['sw_recv'], 1)
                    if {'sw_tick', 'sw_recv'} <= stamps.keys() else None),
        'parts_ms': round(sum(ms for _, ms in legs if ms is not None), 1),
        'total_ms': (round((mcp_read_us - stamps['srv_enqueue_us']) / 1000, 1)
                     if 'srv_enqueue_us' in stamps else None),
    }
