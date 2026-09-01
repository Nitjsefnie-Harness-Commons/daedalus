"""Dashboard static serving behind `GET /dashboard[/<asset>]`.

The route returns a `BytesAnswer` carrying the framing headers, and takes the
dashboard directory as a parameter rather than importing `config`.
"""
from daedalus_bridge.route_answer import BytesAnswer
from daedalus_bridge import path_safety


# This page holds the bridge token and drives the browser, so being framed by
# another origin puts those controls under someone else's overlay.
# frame-ancestors is the one that governs; X-Frame-Options is carried beside
# it for anything that never learned CSP. Sent on every dashboard response,
# assets included: a framed script or stylesheet is a smaller prize than the
# document, but the header costs nothing and the exception would be the thing
# to get wrong.
DASHBOARD_HEADERS = (
    ('Content-Security-Policy', "frame-ancestors 'none'"),
    ('X-Frame-Options', 'DENY'),
)

_MIME_MAP = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
}


def dashboard_asset(dashboard_dir, path):
    """GET /dashboard[/<asset>] — serve dashboard static assets from repo."""
    rel = path[len('/dashboard'):].lstrip('/')
    if not rel:
        rel = 'index.html'
    if any(path_safety.unsafe_component(part) for part in rel.split('/')):
        return 400, {'error': 'bad path'}
    # Same containment as every other root, through the same helper:
    # this route grew its own resolve-and-contain before there was one,
    # and two spellings of one rule is one more than anybody will keep
    # in step.
    try:
        target = path_safety.under(dashboard_dir, *rel.split('/'))
    except (ValueError, OSError):
        return 400, {'error': 'bad path'}
    if not target.is_file():
        return 404, {'error': 'not found'}
    mime = _MIME_MAP.get(target.suffix.lower(), 'application/octet-stream')
    try:
        data = target.read_bytes()
    except OSError:
        return 500, {'error': 'dashboard storage failure'}
    return BytesAnswer(data, mime, DASHBOARD_HEADERS)
