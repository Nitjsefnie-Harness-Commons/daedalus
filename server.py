#!/usr/bin/env python3
"""Daedalus debug server — SSE command bridge + tab registry."""
import hmac, itertools, json, math, os, pathlib, secrets, shutil, threading, time, uuid
import ctypes, ctypes.util
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import TCPServer, ThreadingMixIn
from urllib.parse import urlparse, parse_qs

from daedalus_cli import ambiguous_request_carrier
from daedalus_cli.cli import token as _configured_token


def _log_safe(value):
    """Render a caller-supplied value safe for a log line.

    json.loads accepts lone surrogates (U+D800..U+DFFF), and a filesystem
    name containing an undecodable byte arrives as U+DC80..U+DCFF via
    surrogateescape; f-string interpolation passes either straight through,
    and print() then raises UnicodeEncodeError at the stdout encode wherever
    sys.stdout.errors is strict — an uncaught ValueError that kills the
    request thread before any HTTP answer, tears down a live SSE stream, or
    exits the process at startup. Encoding through backslashreplace escapes
    them ('\\ud800'), so a log line can carry the value without ever raising
    on it.

    Every step of the rendering is guarded for the same reason: str() raises
    on a conversion-limited huge int or an exception object whose __str__
    fails (broad except clauses pass those objects straight here), and a str
    subclass can reach the encode step carrying an encode() that raises or a
    decode() that returns a non-string. The result leaves only when its type
    is exactly str — never a subclass — because the caller's interpolation
    must not see a caller-controlled __format__. The fallback is a fixed
    ASCII string that never interpolates the object that just failed —
    interpolating it would reopen the hole. except Exception is deliberate:
    KeyboardInterrupt and SystemExit still propagate.
    """
    try:
        rendered = str(value).encode('utf-8', 'backslashreplace').decode('utf-8')
    except Exception:
        return '<unprintable value>'
    # Exact type, not isinstance: a str subclass is itself the hostile shape.
    if type(rendered) is not str:  # pylint: disable=unidiomatic-typecheck
        return '<unprintable value>'
    return rendered


# ─── glibc malloc tuning ───
# ThreadingMixIn spawns a thread per request; glibc otherwise creates up to
# 8*nproc memory arenas and never returns their freed memory to the OS, which
# inflates RSS to a high-water-mark (~900MB observed) that never recedes. Cap
# the arenas and actively trim freed heap after large request bodies/files.
_TRIM_THRESHOLD = 256 * 1024  # only trim after handling payloads larger than this
try:
    _LIBC = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
    _LIBC.mallopt(-8, 2)  # M_ARENA_MAX = -8: cap concurrent arenas at 2
except Exception as _e:  # non-glibc / unavailable
    _LIBC = None
    print(f'[Daedalus] malloc tuning unavailable: {_log_safe(_e)}', flush=True)


def _malloc_trim():
    """Return freed heap back to the OS (glibc). No-op where unavailable."""
    if _LIBC is not None:
        try:
            _LIBC.malloc_trim(0)
        except Exception:
            pass


if 'DAEDALUS_DIR' not in os.environ:
    raise SystemExit('DAEDALUS_DIR env var required (e.g. /srv/daedalus)')
if 'DAEDALUS_PORT' not in os.environ:
    raise SystemExit('DAEDALUS_PORT env var required (e.g. 8081)')


def _env_int(name, default, minimum, maximum=None):
    """Read one integer setting and stop startup with a specific error."""
    raw = os.environ.get(name, str(default))
    requirement = (f'an integer from {minimum} to {maximum}' if maximum is not None
                   else 'a non-negative integer')
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(
            f'{name} must be {requirement}; got {raw!r}') from None
    if value < minimum or (maximum is not None and value > maximum):
        raise SystemExit(f'{name} must be {requirement}; got {raw!r}')
    return value


def _env_positive_float(name, default):
    """Read one finite positive floating-point setting or stop startup."""
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(
            f'{name} must be a finite positive number; got {raw!r}') from None
    if not math.isfinite(value) or value <= 0:
        raise SystemExit(
            f'{name} must be a finite positive number; got {raw!r}')
    return value


BASE = pathlib.Path(os.environ['DAEDALUS_DIR'])
CMD_DIR = BASE / 'commands'
RES_DIR = BASE / 'results'
UPLOAD_DIR = BASE / 'uploads'
SEG_DIR = BASE / 'segments'
DASHBOARD_DIR = pathlib.Path(__file__).resolve().parent / 'dashboard'
# 0 lets the kernel pick a free port; the startup line always prints the
# port actually bound, so an ephemeral deployment (and the test fixture)
# never has to choose a number another process could take first.
PORT = _env_int('DAEDALUS_PORT', 0, 0, 65535)

# ─── Path-component safety ───
# Caller-controlled path components use these helpers. Dashboard asset URLs
# apply the same checks to each component before a resolved-root containment
# check. The helpers are shared so every component validation gets the same
# traversal, drive, and Windows-name rules.
#
# Backslash is rejected alongside the forward slash because it is a separator
# on Windows; leaving it out made `a\\b` a nested path there and a plain name
# everywhere else.
_WINDOWS_DEVICE_NAMES = frozenset({
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
})
_WINDOWS_INVALID_PATH_CHARS = frozenset('<>:"/\\|?*')
_MAX_COMPONENT_BYTES = 240


def _unsafe_component(value):
    """Return whether `value` violates the bridge's component policy.

    Rejects non-strings, traversal markers (`..`), C0/C1 control characters,
    surrogate code points, every Windows-invalid path character
    (`<>:"/\\|?*`), Windows device names (`CON`, `PRN`, `AUX`, `NUL`,
    `COM1`-`COM9`, `LPT1`-`LPT9`, case-insensitively, with or without an
    extension), trailing dots or spaces, and UTF-8 encodings longer than 240
    bytes. Derived result filenames and command-queue target directories are
    checked after construction. Legacy command files, dashboard event files,
    screenshot names, and segment record/data/temp names use fixed
    server-generated affixes whose complete components are bounded by
    construction. This policy does not claim later filesystem operations
    cannot fail for unrelated reasons.
    """
    if not isinstance(value, str):
        return True
    invalid_codepoint = any(
        ord(char) < 32 or 127 <= ord(char) <= 159
        or 0xD800 <= ord(char) <= 0xDFFF
        for char in value)
    if ('..' in value or any(char in _WINDOWS_INVALID_PATH_CHARS for char in value)
            or invalid_codepoint or value.endswith(('.', ' '))):
        return True
    if len(value.encode('utf-8')) > _MAX_COMPONENT_BYTES:
        return True
    device_stem = value.split('.', 1)[0].rstrip(' .').upper()
    return device_stem in _WINDOWS_DEVICE_NAMES


def _bad_token(token):
    """True when `token` is unusable as a directory name.

    Stricter than _unsafe_component on two counts. A token may not contain a
    dot, so it can never collide with one of the `<token>.json` files beside
    it. And it may not contain an underscore, because per-tab paths are
    `<token>_<tab>`: without that rule the token `victim_x` and the pair
    (token `victim`, tab `x`) name the same file, so one caller reads another's
    results and both write the same command queue. Tokens are generated UUIDs,
    which contain neither.
    """
    return (not token or _unsafe_component(token)
            or '.' in token or '_' in token)


def _normalized_tab_id(value):
    """Normalize string or integer tab-id JSON values; return None otherwise."""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _derived_component(value):
    """Return a checked derived path component, or raise ValueError."""
    if _unsafe_component(value):
        raise ValueError('unsafe derived path component')
    return value


def _command_target_names(token, tab=''):
    """Return the checked queue directory and bounded legacy filename."""
    queue_name = _derived_component(
        f'{token}_{tab}' if tab else token)
    return queue_name, f'{queue_name}.json'


# ─── Dashboard event queue ───
# Directory-per-token queue: commands/{token}_dashboard/<ts>_<uuid>.json
# Directory form (not single file) because concurrent writes to one file truncate each other.
_command_fs_lock = threading.Lock()


def _notify_dashboard(token, payload):
    """Enqueue a dashboard SSE event. No-op if its queue cannot be named."""
    if _bad_token(token):
        return
    try:
        queue_name, _ = _command_target_names(token, 'dashboard')
    except ValueError:
        return
    dash_dir = CMD_DIR / queue_name
    try:
        with _command_fs_lock:
            dash_dir.mkdir(parents=True, exist_ok=True)
            event_id = f'{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}'
            event = {'id': event_id, 'kind': 'event', **payload}
            (dash_dir / f'{event_id}.json').write_text(
                json.dumps(event, ensure_ascii=False))
        _cmd_event(token).set()  # wake the dashboard stream immediately
    except Exception as e:
        print(f'[DASH-NOTIFY-FAIL] {_log_safe(e)}', flush=True)

# ─── Tab registry ───
# Authoritative source: /sync-tabs (replaces all). /register only updates existing.


_tab_registry = {}  # {token: {tabId: {url, title, ts}}}
_tab_lock = threading.Lock()

# ─── Stream dedup: kill old SSE when same tab reconnects ───
_active_streams = {}  # {(token, tab): threading.Event}  — set() means "die"
_stream_lock = threading.Lock()

# Result files are single-value delivery slots. POST replacement and
# conditional GET consumption share this lock so a newer result cannot land
# between the consumer's generation check and unlink.
_result_lock = threading.Lock()


def _atomic_result_write(path, data):
    """Replace one result slot only after its temp file is fully written."""
    tmp = path.parent / f'.result-{uuid.uuid4().hex}.tmp'
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# Stream lifetime ceiling. A stream's liveness is policed by the keepalive write
# (a dead peer raises on the next one) and by replacement-on-reconnect, so this
# is only a last-resort ceiling on a wedged connection — not a rollover timer.
STREAM_MAX_AGE = _env_positive_float('DAEDALUS_STREAM_MAX_AGE', 3600)
STREAM_KEEPALIVE = _env_positive_float('DAEDALUS_STREAM_KEEPALIVE', 15)
# Maximum bytes read from any HTTP request body; override for larger relays.
MAX_BODY_SIZE = _env_int('DAEDALUS_MAX_BODY_SIZE', 64 * 1024 * 1024, 0)
# Fixed HLS quotas copied into each trusted job record when its capability is
# minted. A page-visible job signature cannot alter these per-request.
MAX_SEGMENT_INDEX = _env_int('DAEDALUS_MAX_SEGMENT_INDEX', 99999, 0)
MAX_SEGMENTS_PER_JOB = _env_int(
    'DAEDALUS_MAX_SEGMENTS_PER_JOB', 10000, 0)
MAX_SEGMENT_JOB_SIZE = _env_int(
    'DAEDALUS_MAX_SEGMENT_JOB_SIZE', 4 * 1024 * 1024 * 1024, 0)
_SEGMENT_DECIMAL_MAX_DIGITS = 20

# ─── Command queue (directory-per-target, FIFO) ───
# PUT /command enqueues into commands/{token}_{tab}/<seq>.json (per-tab) or
# commands/{token}/<seq>.json (broadcast). Directory form so back-to-back
# commands to the same target queue instead of overwriting a single file.
# Legacy single-file drops (commands/{token}[_{tab}].json) are still delivered
# for the documented raw-write escape hatch.
CMD_TTL = _env_positive_float('DAEDALUS_CMD_TTL', 90)
_COMMAND_GC_INTERVAL = max(0.05, min(30.0, CMD_TTL))
_seq_counter = itertools.count(1)


def _remove_expired_command_file(path, now, legacy=False):
    """Remove one expired, complete command artifact without following symlinks.

    Queue entries are published by this process with rename, so their `.json`
    and stale `.tmp` names are safe to collect. A top-level legacy file is
    caller-published: malformed content may still have an open writer and is
    left untouched until it becomes a complete JSON object.
    """
    if not path.name.endswith(('.json', '.tmp')):
        return
    try:
        if now - path.lstat().st_mtime <= CMD_TTL:
            return
        if legacy:
            if path.name.startswith('.') or not path.name.endswith('.json'):
                return
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                return
        path.unlink()
    except (OSError, json.JSONDecodeError, RecursionError, ValueError):
        pass


def _collect_expired_commands():
    """Expire command files and empty queue directories without an SSE reader."""
    now = time.time()
    with _command_fs_lock:
        try:
            entries = list(CMD_DIR.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_symlink():
                continue
            if not entry.is_dir():
                _remove_expired_command_file(entry, now, legacy=True)
                continue
            try:
                children = list(entry.iterdir())
            except OSError:
                continue
            for child in children:
                _remove_expired_command_file(child, now)
            try:
                entry.rmdir()
            except OSError:
                pass


def _command_gc_loop():
    """Run command expiry independently of producers and SSE consumers."""
    while True:
        time.sleep(_COMMAND_GC_INTERVAL)
        _collect_expired_commands()


def _next_seq():
    """Monotonic, lexically-sortable queue filename stem: <ms>_<counter>."""
    return f'{int(time.time() * 1000):013d}_{next(_seq_counter):06d}'

# ─── Per-token wake events: writers signal, SSE streams wait (near-zero latency) ───


_cmd_events = {}  # {token: threading.Event}
_cmd_events_lock = threading.Lock()


def _cmd_event(token):
    with _cmd_events_lock:
        ev = _cmd_events.get(token)
        if ev is None:
            ev = threading.Event()
            _cmd_events[token] = ev
        return ev


def _enqueue_command(token, tab, cmd):
    """Append a command to the target's directory queue. Returns the delivery id.

    Refuses an unsafe `tab` itself rather than trusting the caller: this is the
    single place the value becomes a directory name, and the handler that used
    to be its only caller did not check it.
    """
    if tab and _unsafe_component(tab):
        raise ValueError(f'unsafe tab component: {tab!r}')
    queue_name, _ = _command_target_names(token, tab)
    qdir = CMD_DIR / queue_name
    with _command_fs_lock:
        qdir.mkdir(parents=True, exist_ok=True)
        seq = _next_seq()
        cmd = {**cmd, '_did': seq}
        tmp = qdir / f'.{seq}.tmp'
        try:
            tmp.write_text(json.dumps(cmd, ensure_ascii=False))
            os.replace(str(tmp), str(qdir / f'{seq}.json'))
        except (OSError, UnicodeEncodeError):
            # A refused enqueue must not leave its hidden temp behind: the
            # zero-byte artifact would sit in the queue until the background
            # collector's TTL sweep. Same rollback as _atomic_result_write,
            # plus the encode failure write_text raises after creating it.
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
    _cmd_event(token).set()
    return seq


# ─── HLS segment relay ───
# One flat job namespace under the data root: segments/<job>/ holds the .ts
# files, and segments/<job>.json beside the directory records the owning token,
# minted capability, and fixed index/count/byte quotas. The page-JavaScript
# relay presents the capability (sig) rather than the bridge token, because
# anything that script carries the visited page can read.
_seg_lock = threading.Lock()


def _segment_record_path(job):
    return SEG_DIR / f'{job}.json'


def _load_segment_record(job):
    """Return `job`'s JSON object, or None when absent, unreadable, or malformed."""
    try:
        record = json.loads(_segment_record_path(job).read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _segment_record_for_sig(job, sig):
    """Return `job` metadata when `sig` matches its minted capability.

    compare_digest raises TypeError on non-ASCII str input, and the sig arrives
    as a query string, so both sides are gated before the comparison.
    """
    record = _load_segment_record(job)
    expected = record.get('sig', '') if record else ''
    if not isinstance(expected, str) or not expected or not expected.isascii():
        return None
    if not sig or not sig.isascii():
        return None
    return record if hmac.compare_digest(expected, sig) else None


def _segment_sig_ok(job, sig):
    """Constant-time check of `sig` against the capability minted for `job`."""
    return _segment_record_for_sig(job, sig) is not None


def _segment_quota(record):
    """Return trusted (max index, file count, bytes), or None if malformed."""
    max_index = record.get('max_segment_index')
    max_count = record.get('max_segment_count')
    max_bytes = record.get('max_bytes')
    if (not isinstance(max_index, int) or isinstance(max_index, bool)
            or max_index < 0):
        return None
    if (not isinstance(max_count, int) or isinstance(max_count, bool)
            or max_count < 0):
        return None
    if (not isinstance(max_bytes, int) or isinstance(max_bytes, bool)
            or max_bytes < 0):
        return None
    return max_index, max_count, max_bytes


# ─── Health / observability ───


_server_start_ts = time.time()
_last_delivery_ts = 0.0


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def server_bind(self):
        """Bind and record the address without a reverse-DNS lookup.

        HTTPServer.server_bind resolves the bound host through
        socket.getfqdn, i.e. a name-service round trip, after the socket is
        already listening and before the caller regains control. Where that
        lookup is slow or answered by nothing — a host with no reverse zone
        for loopback, a resolver behind a firewall — startup stops here, so
        the Listening line, which is the only readiness signal the bridge
        emits, arrives minutes late or not at all while the port is in fact
        open. Nothing reads server_name, so record the literal bind host and
        keep startup free of the network.
        """
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class _JSONObject(dict):
    def __init__(self, pairs):
        super().__init__(pairs)
        self.duplicate_carrier = ambiguous_request_carrier(
            key for key, _value in pairs)


class Handler(BaseHTTPRequestHandler):
    def _request_target(self):
        """Parse the request target, answering 400 when it is malformed.

        An absolute-form target such as `GET http://[ HTTP/1.1` makes
        urlparse raise ValueError; uncaught, that killed the request thread
        and the client saw the connection close with zero response bytes.
        Every verb that parses the target goes through here.
        """
        try:
            return urlparse(self.path)
        except ValueError:
            self._json(400, {'error': 'invalid request target'})
            return None

    def _parse_query(self, query):
        """Retain blank values and reject ambiguous security carriers."""
        params = parse_qs(query, keep_blank_values=True)
        duplicate = ambiguous_request_carrier(
            key for key, values in params.items() for _value in values)
        if duplicate is not None:
            self._json(400, {'error': f'duplicate {duplicate}'})
            return None
        return params

    def _query_bridge_token(self, params):
        """Return the query token — always a str, '' when absent — after the
        request-wide ambiguity check."""
        credentials = params.get('token', [])
        return credentials[0] if credentials else ''

    def _require_bridge_token(self, token):
        """Answer an error unless `token` matches the configured bridge secret."""
        if _bad_token(token):
            self._json(400, {'error': 'bad token'})
            return False
        try:
            authorized = _configured_token()
        except SystemExit:
            authorized = ''
        if (not isinstance(authorized, str) or not authorized
                or not hmac.compare_digest(
                    token.encode('utf-8', 'surrogatepass'),
                    authorized.encode('utf-8', 'surrogatepass'))):
            self._json(401, {'error': 'unauthorized'})
            return False
        return True

    def do_GET(self):
        parsed = self._request_target()
        if parsed is None:
            return
        params = self._parse_query(parsed.query)
        if params is None:
            return

        if parsed.path == '/result':
            return self._handle_get_result(params)

        if parsed.path == '/screenshot':
            return self._handle_get_screenshot(params)

        if parsed.path == '/upload':
            return self._handle_list_uploads(params)

        if parsed.path == '/tabs':
            token = self._query_bridge_token(params)
            if not self._require_bridge_token(token):
                return
            with _tab_lock:
                tabs = _tab_registry.get(token, {})
                result = [
                    {'tabId': tid, 'url': info.get('url', ''), 'title': info.get('title', ''), 'age': round(time.time() - info.get('ts', 0))}
                    for tid, info in tabs.items()
                ]
            return self._json(200, result)

        if parsed.path == '/segment-status':
            return self._handle_segment_status(params)

        if parsed.path == '/health':
            return self._handle_health()

        if parsed.path == '/dashboard' or parsed.path.startswith('/dashboard/'):
            return self._handle_get_dashboard(parsed.path)

        if parsed.path != '/stream':
            return self._json(404, {'error': 'not found'})
        token = self._query_bridge_token(params)
        tab = params.get('tab', [''])[0]
        if not self._require_bridge_token(token):
            print('[STREAM] REJECTED unauthorized token', flush=True)
            return
        if tab and _unsafe_component(tab):
            print(f'[STREAM] REJECTED unsafe tab: {tab!r}', flush=True)
            return self._json(400, {'error': 'invalid path component'})
        try:
            target_queue_name, target_legacy_name = _command_target_names(
                token, tab)
            broadcast_queue_name, broadcast_legacy_name = (
                _command_target_names(token))
        except ValueError:
            print('[STREAM] REJECTED unsafe derived target', flush=True)
            return self._json(400, {'error': 'invalid path component'})
        # Kill old stream for same tab, register new one
        stream_key = (token, tab) if tab else None
        killed_event = None
        if stream_key:
            with _stream_lock:
                old = _active_streams.get(stream_key)
                if old:
                    old.set()  # signal old thread to die
                    print(f'[STREAM] REPLACED tab={tab[:8]}', flush=True)
                killed_event = threading.Event()
                _active_streams[stream_key] = killed_event
        print(f'[STREAM] CONNECT token={token[:8]} tab={tab[:8] if tab else "none"} from={self.client_address[0]}', flush=True)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        # MUST be 'close', not 'keep-alive'. BaseHTTPRequestHandler.send_header
        # reads this value: 'keep-alive' sets close_connection=False, so when the
        # stream loop below ends the handler returns and the socket is held open
        # for a next request that never comes. The client then sees silence, not
        # EOF — its reconnect waits out a watchdog instead of firing immediately
        # (measured: ~25s direct, and several times that through a proxy). A stream response
        # is the connection's last, so say so.
        self.send_header('Connection', 'close')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        last_ka = time.time()
        stream_start = time.time()
        try:
            while True:
                if killed_event and killed_event.is_set():
                    print(f'[STREAM] KILLED-BY-RECONNECT tab={tab[:8] if tab else "none"}', flush=True)
                    break
                if time.time() - stream_start > STREAM_MAX_AGE:
                    print(f'[STREAM] MAX-AGE tab={tab[:8] if tab else "none"}', flush=True)
                    break
                # Clear the wake event before scanning: event.set is sticky, so a
                # signal that lands during/after the scan is observed on the next wait.
                ev = _cmd_event(token)
                ev.clear()
                delivered = 0
                if tab == 'dashboard':
                    delivered += self._drain_queue(
                        CMD_DIR / target_queue_name, None, killed_event)
                elif tab == 'extension':
                    # Typed commands addressed to the extension itself
                    delivered += self._drain_queue(
                        CMD_DIR / target_queue_name, None, killed_event)
                    delivered += self._drain_legacy_file(
                        CMD_DIR / target_legacy_name, None)
                    # Per-tab eval queues for every other tab (tag chromeTab so bg can route)
                    prefix = f'{token}_'
                    for entry in sorted(CMD_DIR.iterdir()):
                        if not entry.is_dir() or not entry.name.startswith(prefix):
                            continue
                        sub = entry.name[len(prefix):]
                        if sub in ('extension', 'dashboard'):
                            continue
                        delivered += self._drain_queue(entry, sub, killed_event)
                    # Broadcast queue + legacy per-tab raw-file drops
                    delivered += self._drain_queue(
                        CMD_DIR / broadcast_queue_name, None, killed_event)
                    delivered += self._drain_legacy_ext(
                        token, target_legacy_name, killed_event)
                else:  # specific-tab stream (rare — normal clients use tab=extension)
                    delivered += self._drain_queue(
                        CMD_DIR / target_queue_name, None, killed_event)
                    if tab:
                        delivered += self._drain_queue(
                            CMD_DIR / broadcast_queue_name, None, killed_event)
                        delivered += self._drain_legacy_file(
                            CMD_DIR / target_legacy_name, None)
                # Broadcast legacy raw-file — skip for dashboard so it doesn't steal commands
                if tab != 'dashboard':
                    delivered += self._drain_legacy_file(
                        CMD_DIR / broadcast_legacy_name, None)

                now = time.time()
                if now - last_ka >= STREAM_KEEPALIVE:
                    self.wfile.write(b': keepalive\n\n')
                    self.wfile.flush()
                    last_ka = now
                if not delivered:
                    # Wake immediately when a command is enqueued; 1s fallback keeps
                    # the max-age / keepalive / kill checks live during idle.
                    ev.wait(timeout=1.0)
        except (BrokenPipeError, ConnectionError, OSError) as e:
            print(f'[STREAM] DISCONNECT tab={tab[:8] if tab else "none"} err={type(e).__name__}', flush=True)
        finally:
            if stream_key:
                with _stream_lock:
                    if _active_streams.get(stream_key) is killed_event:
                        del _active_streams[stream_key]

    def _write_frame(self, data):
        """Serialize + write+flush one SSE command frame. Raises on socket error."""
        self.wfile.write(f'event: command\ndata: {json.dumps(data)}\n\n'.encode())
        self.wfile.flush()

    def _drain_queue(self, qdir, chrome_tab, killed_event):
        """Deliver every ready command from a directory queue, FIFO. Returns the
        count delivered. TTL-expired, unreadable, invalid-JSON, and non-object
        entries are skipped after an unlink attempt. The socket write happens
        BEFORE unlink, so a failed write leaves the command queued for redelivery
        (a socket error propagates out to tear the stream down)."""
        global _last_delivery_ts
        if not qdir.is_dir():
            return 0
        count = 0
        try:
            queued_files = sorted(qdir.iterdir())
        except OSError:
            return 0
        for f in queued_files:
            if killed_event and killed_event.is_set():
                break
            name = f.name
            if name.startswith('.') or not name.endswith('.json'):
                continue  # skip .tmp in-flight writes
            try:
                age = time.time() - f.stat().st_mtime
            except OSError:
                continue  # vanished or became unavailable during the scan
            if age > CMD_TTL:
                try: f.unlink()
                except OSError: pass
                print(f'[STREAM] TTL-DROP {_log_safe(qdir.name)}/{_log_safe(name)}', flush=True)
                continue
            try:
                data = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError, ValueError):
                try: f.unlink()  # malformed/vanished — attempt removal
                except OSError: pass
                continue
            if not isinstance(data, dict):
                try: f.unlink()
                except OSError: pass
                continue
            if chrome_tab is not None:
                data['chromeTab'] = chrome_tab
            self._write_frame(data)  # BEFORE unlink
            try: f.unlink()
            except OSError: pass
            _last_delivery_ts = time.time()
            count += 1
            print(f'[STREAM] DELIVERED q={_log_safe(qdir.name)} id={_log_safe(data.get("id", ""))} did={_log_safe(data.get("_did", ""))}', flush=True)
        return count

    def _drain_legacy_file(self, path, chrome_tab):
        """Deliver one atomically published legacy command file.

        A malformed visible file may still have an open writer from an older,
        non-atomic publisher. Leave it in place and retry on the next scan;
        deleting it would discard the writer's eventual complete command.
        """
        global _last_delivery_ts
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, RecursionError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return 0
        if age > CMD_TTL:
            try: path.unlink()
            except OSError: pass
            return 0
        if chrome_tab is not None:
            data['chromeTab'] = chrome_tab
        self._write_frame(data)  # BEFORE unlink
        try: path.unlink()
        except OSError: pass
        _last_delivery_ts = time.time()
        print(f'[STREAM] DELIVERED legacy={_log_safe(path.name)} id={_log_safe(data.get("id", ""))}', flush=True)
        return 1

    def _drain_legacy_ext(self, token, extension_legacy_name, killed_event):
        """Extension stream: deliver legacy per-tab raw-write files ({token}_<tab>.json),
        tagging chromeTab for routing. Queue dirs and dashboard/extension are skipped."""
        prefix = f'{token}_'
        count = 0
        for f in sorted(CMD_DIR.iterdir()):
            if killed_event and killed_event.is_set():
                break
            name = f.name
            if not f.is_file() or not name.startswith(prefix) or not name.endswith('.json'):
                continue
            if name == extension_legacy_name:
                continue  # handled separately (no chromeTab tag)
            sub = name[len(prefix):-5]
            if sub == 'dashboard':
                continue
            count += self._drain_legacy_file(f, sub)
        return count

    def do_POST(self):
        clen = self._declared_body_length()
        if clen is None:
            return
        try:
            return self._dispatch_post(clen)
        finally:
            if clen > _TRIM_THRESHOLD:
                _malloc_trim()

    def _declared_body_length(self):
        """Parse and bound the Content-Length header of a body-reading request.

        Every verb that reads a body (POST, PUT, DELETE) gates on this, so the
        refusal rules live in exactly one place. Returns the byte count the
        handler may read, or None once the request has already been answered:
        400 for a value int() cannot parse (an uncaught ValueError here used
        to kill the request thread, dropping the connection with no answer),
        400 for a negative value (rfile.read(-1) reads to EOF, so a negative
        length is not a small body — it is an unbounded one, and testing only
        `clen > MAX_BODY_SIZE` let it straight through), and 413 for one over
        MAX_BODY_SIZE.
        """
        try:
            clen = int(self.headers.get('Content-Length', 0))
        except ValueError:
            self._json(400, {'error': 'invalid Content-Length'})
            return None
        if clen < 0:
            self._json(400, {'error': 'invalid Content-Length'})
            return None
        if clen > MAX_BODY_SIZE:
            # The refused body is NOT drained: the unread bytes die with the
            # socket. That holds only while protocol_version stays at the
            # HTTP/1.0 default, which keeps close_connection true on every
            # request — raise it and this guard must drain first, or the
            # leftover body would be parsed as the next kept-alive request.
            self._json(413, {'error': 'request body too large'})
            return None
        return clen

    def _load_json_object(self, clen):
        """Read one JSON body, answering 400 unless it is an object."""
        try:
            body = json.loads(
                self.rfile.read(clen), object_pairs_hook=_JSONObject)
        except RecursionError:
            self._json(400, {'error': 'JSON body too deeply nested'})
            return None
        except (json.JSONDecodeError, ValueError):
            self._json(400, {'error': 'invalid JSON body'})
            return None
        if not isinstance(body, _JSONObject):
            self._json(400, {'error': 'JSON body must be an object'})
            return None
        if body.duplicate_carrier is not None:
            self._json(
                400, {'error': f'duplicate {body.duplicate_carrier}'})
            return None
        return body

    def _dispatch_post(self, clen):
        content_type = self.headers.get('Content-Type', '')
        parsed = self._request_target()
        if parsed is None:
            return
        if (parsed.path == '/segment'
                and ('octet-stream' in content_type
                     or 'application/json' not in content_type)):
            return self._handle_segment(self.rfile.read(clen))
        body = self._load_json_object(clen)
        if body is None:
            return
        token = body.get('token', '')
        if not self._require_bridge_token(token):
            return

        if self.path == '/register':
            raw_tab_id = body.get('tabId', '')
            tab_id = _normalized_tab_id(raw_tab_id)
            url = body.get('url', '')
            title = body.get('title', '')
            if tab_id == '':
                return self._json(400, {'error': 'missing tabId'})
            if tab_id is None:
                return self._json(400, {'error': 'invalid tabId'})
            updated = False
            with _tab_lock:
                tabs = _tab_registry.get(token, {})
                if tab_id in tabs:
                    # Update-only: refresh existing tab, never create new entries
                    tabs[tab_id] = {'url': url, 'title': title, 'ts': time.time()}
                    updated = True
            if updated:
                _notify_dashboard(token, {'type': 'tab-updated', 'tabId': tab_id, 'url': url, 'title': title})
            return self._json(200, {'ok': True})

        elif self.path == '/sync-tabs':
            # Replace entire tab registry for this token with provided list
            tabs_list = body.get('tabs', [])
            if (not isinstance(tabs_list, list)
                    or any(not isinstance(tab, dict) for tab in tabs_list)):
                return self._json(400, {'error': 'invalid tabs'})
            normalized_tabs = []
            for tab_info in tabs_list:
                tab_id = _normalized_tab_id(tab_info.get('tabId', ''))
                if tab_id is None:
                    return self._json(400, {'error': 'invalid tabs'})
                if tab_id:
                    normalized_tabs.append((tab_id, tab_info))
            with _tab_lock:
                _tab_registry[token] = {}
                for tab_id, tab_info in normalized_tabs:
                    _tab_registry[token][tab_id] = {
                        'url': tab_info.get('url', ''),
                        'title': tab_info.get('title', ''),
                        'ts': time.time(),
                    }
            count = len(_tab_registry.get(token, {}))
            _notify_dashboard(token, {'type': 'tabs-synced', 'count': count})
            return self._json(200, {'ok': True, 'count': count})

        elif self.path == '/unregister':
            tab_id = body.get('tabId', '')
            if not tab_id:
                return self._json(400, {'error': 'missing tabId'})
            with _tab_lock:
                tabs = _tab_registry.get(token, {})
                removed = tabs.pop(str(tab_id), None)
            _notify_dashboard(token, {'type': 'tab-unregistered', 'tabId': str(tab_id)})
            return self._json(200, {'ok': True, 'removed': removed is not None})

        elif self.path == '/poll':
            _, legacy_name = _command_target_names(token)
            cmd_file = CMD_DIR / legacy_name
            data = {}
            with _command_fs_lock:
                if cmd_file.exists():
                    try:
                        candidate = json.loads(cmd_file.read_text())
                        if isinstance(candidate, dict):
                            data = candidate
                            cmd_file.unlink()
                    except (OSError, json.JSONDecodeError,
                            RecursionError, ValueError):
                        pass
            return self._json(200, data)

        elif self.path == '/upload':
            return self._handle_upload(body)

        elif self.path == '/segment-job':
            return self._handle_segment_job(body)

        elif self.path == '/result':
            tab_id = body.get('tabId', '')
            if tab_id and _unsafe_component(tab_id):
                return self._json(400, {'error': 'invalid path component'})
            try:
                token_result_name = _derived_component(f'{token}.json')
                tab_result_name = (_derived_component(
                    f'{token}_{tab_id}.json') if tab_id else '')
            except ValueError:
                return self._json(400, {'error': 'invalid path component'})
            print(f'[RESULT] tab={tab_id[:8] if tab_id else "none"} id={_log_safe(body.get("id", ""))}', flush=True)
            # Full server-observed roundtrip: _did's leading ms is the enqueue
            # instant (same clock as now), so no skew. Skip if _did is absent/malformed.
            # _did remains internal on the extension wire. Surface its value as
            # deliveryId so waiters can correlate a result with this invocation.
            did = body.pop('_did', '')
            body.pop('deliveryId', None)
            body['resultGeneration'] = uuid.uuid4().hex
            if isinstance(did, str) and did:
                body['deliveryId'] = did
            if isinstance(did, str) and '_' in did:
                try:
                    body['roundtrip_ms'] = int(time.time() * 1000) - int(did.split('_')[0])
                except ValueError:
                    pass
            try:
                serialized = json.dumps(
                    body, ensure_ascii=False).encode('utf-8')
            except (TypeError, ValueError, RecursionError):
                return self._json(400, {'error': 'result is not encodable'})
            try:
                with _result_lock:
                    # Per-tab result file
                    if tab_id:
                        _atomic_result_write(
                            RES_DIR / tab_result_name, serialized)
                    # Backward compat: also write to token-only file
                    _atomic_result_write(
                        RES_DIR / token_result_name, serialized)
            except OSError:
                return self._json(500, {'error': 'result storage failure'})
            _notify_dashboard(token, {
                'type': 'result',
                'tabId': str(tab_id) if tab_id else '',
                'resultId': body.get('id', ''),
                'world': body.get('world', ''),
                'ok': body.get('error') is None,
                'ts': body.get('ts', int(time.time() * 1000)),
            })
            return self._json(200, {'ok': True})

        return self._json(404, {'error': 'not found'})

    def do_DELETE(self):
        clen = self._declared_body_length()
        if clen is None:
            return
        body = self._load_json_object(clen)
        if body is None:
            return
        token = body.get('token', '')
        if not self._require_bridge_token(token):
            return
        if self.path == '/upload':
            return self._handle_delete_upload(body)
        return self._json(404, {'error': 'not found'})

    def _handle_delete_upload(self, body):
        """DELETE /upload — remove uploaded files.
        {token, id} — delete all files under token/id/
        {token, id, filename} — delete specific file
        {token} — delete all uploads for token
        """
        token = body['token']
        upload_id = body.get('id', '')
        filename = body.get('filename', '')
        for val in (upload_id, filename):
            if _unsafe_component(val):
                return self._json(400, {'error': 'invalid path component'})
        try:
            if filename and upload_id:
                target = UPLOAD_DIR / token / upload_id / filename
                if not target.is_file():
                    return self._json(404, {'error': 'file not found'})
                target.unlink()
                print(f'[DELETE] {token}/{upload_id}/{filename}', flush=True)
            elif upload_id:
                target = UPLOAD_DIR / token / upload_id
                if not target.is_dir():
                    return self._json(404, {'error': 'id not found'})
                shutil.rmtree(target)
                print(f'[DELETE] {token}/{upload_id}/', flush=True)
            else:
                target = UPLOAD_DIR / token
                if not target.is_dir():
                    return self._json(404, {'error': 'token not found'})
                shutil.rmtree(target)
                print(f'[DELETE] {token}/', flush=True)
        except OSError:
            return self._json(500, {'error': 'upload delete failure'})
        return self._json(200, {'ok': True})

    def do_PUT(self):
        clen = self._declared_body_length()
        if clen is None:
            return
        parsed = self._request_target()
        if parsed is None:
            return
        if parsed.path == '/command':
            return self._handle_put_command(clen)
        return self._json(404, {'error': 'not found'})

    def _handle_put_command(self, clen):
        """PUT /command — write a command for delivery via SSE."""
        body = self._load_json_object(clen)
        if body is None:
            return
        token = body.get('token', '')
        tab = str(body.get('tab', ''))
        cmd_id = body.get('id', '')
        code = body.get('code', '')
        cmd_type = body.get('type', '')
        if not self._require_bridge_token(token):
            return
        if tab and _unsafe_component(tab):
            return self._json(400, {'error': 'invalid path component'})
        if not cmd_id or (not code and not cmd_type):
            return self._json(400, {'error': 'missing id or code/type'})
        # token and tab are ROUTING and never reach the client, which is
        # why a browser target travels as `tabId` -- the field the rest of
        # the command set already uses. Screenshot and CDP used to send it
        # as `tab`, so this strip removed it, and in one sender it also
        # overwrote the routing value: both silently hit the active tab.
        cmd = {k: v for k, v in body.items() if k not in ('token', 'tab')}
        try:
            did = _enqueue_command(token, tab, cmd)
        except UnicodeEncodeError:
            # A lone surrogate in a body value fails the queue-file encode;
            # that is an unencodable body, not a bad path component. Must
            # precede except ValueError, which it subclasses.
            return self._json(400, {'error': 'command is not encodable'})
        except ValueError:
            return self._json(400, {'error': 'invalid path component'})
        except OSError:
            return self._json(500, {'error': 'command storage failure'})
        target = f'tab={tab[:8]}' if tab else 'broadcast'
        print(f'[PUT-CMD] {target} id={_log_safe(cmd_id)} did={did}', flush=True)
        return self._json(200, {'ok': True, 'target': target, 'did': did})

    def _handle_get_result(self, params):
        """Fetch a result, optionally consuming one expected generation."""
        token = self._query_bridge_token(params)
        tab = params.get('tab', [''])[0]
        consume = params.get('consume', [''])[0] == '1'
        expected = params.get('expected', [''])[0]
        if not self._require_bridge_token(token):
            return
        if tab and _unsafe_component(tab):
            return self._json(400, {'error': 'invalid path component'})
        # A requested tab selects its own slot; otherwise use the token slot.
        try:
            result_name = _derived_component(
                f'{token}_{tab}.json' if tab else f'{token}.json')
        except ValueError:
            return self._json(400, {'error': 'invalid path component'})
        res_file = RES_DIR / result_name
        try:
            with _result_lock:
                if not res_file.exists():
                    response = {'pending': True}
                else:
                    data = json.loads(res_file.read_text())
                    if not isinstance(data, dict):
                        raise ValueError('result slot is not a JSON object')
                    generation = data.get('resultGeneration', '')
                    if consume and expected and generation != expected:
                        response = {'consumed': False}
                    elif consume:
                        res_file.unlink()
                        response = ({'consumed': True,
                                     'resultGeneration': generation}
                                    if expected else data)
                    else:
                        response = data
        except (OSError, json.JSONDecodeError, ValueError):
            return self._json(500, {'error': 'result storage failure'})
        return self._json(200, response)

    def _handle_list_uploads(self, params):
        """GET /upload?token=X[&id=Y][&limit=N&offset=M] — list uploaded files.
        When limit or offset is provided, returns {items, total, limit, offset}.
        Without either, returns a bare array (back-compat)."""
        token = self._query_bridge_token(params)
        upload_id = params.get('id', [''])[0]
        limit_p = params.get('limit', [None])[0]
        offset_p = params.get('offset', [None])[0]
        if not self._require_bridge_token(token):
            return
        if upload_id and _unsafe_component(upload_id):
            return self._json(400, {'error': 'invalid path component'})
        token_dir = UPLOAD_DIR / token
        if not token_dir.is_dir():
            if limit_p is not None or offset_p is not None:
                return self._json(200, {'items': [], 'total': 0, 'limit': 0, 'offset': 0})
            return self._json(200, [])
        results = []
        if upload_id:
            id_dir = token_dir / upload_id
            if id_dir.is_dir():
                for f in sorted(id_dir.iterdir()):
                    if f.is_file():
                        results.append({
                            'id': upload_id,
                            'filename': f.name,
                            'size': f.stat().st_size,
                            'mtime': int(f.stat().st_mtime),
                            'path': f'{token}/{upload_id}/{f.name}',
                        })
        else:
            # Sort id_dirs by mtime desc so newest uploads appear first
            id_dirs = [d for d in token_dir.iterdir() if d.is_dir()]
            id_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
            for id_dir in id_dirs:
                for f in sorted(id_dir.iterdir()):
                    if f.is_file():
                        results.append({
                            'id': id_dir.name,
                            'filename': f.name,
                            'size': f.stat().st_size,
                            'mtime': int(f.stat().st_mtime),
                            'path': f'{token}/{id_dir.name}/{f.name}',
                        })
        if limit_p is not None or offset_p is not None:
            try:
                lim = int(limit_p) if limit_p is not None else 200
                off = int(offset_p) if offset_p is not None else 0
            except ValueError:
                return self._json(400, {'error': 'invalid limit/offset'})
            lim = max(1, min(lim, 1000))
            off = max(0, off)
            total = len(results)
            return self._json(200, {'items': results[off:off + lim], 'total': total, 'limit': lim, 'offset': off})
        return self._json(200, results)

    def _handle_segment(self, raw):
        """POST /segment?job=X&seg=N&total=T&sig=S — store raw binary HLS segment."""
        # The documented poster is page JavaScript running in a hostile page's
        # MAIN world, so it must never hold the bridge token. It carries the
        # job-scoped capability minted by POST /segment-job instead. A stolen
        # sig authorizes status reads and segment writes only for that job. The
        # finalized .ts set stays inside the record's index, count, and byte
        # quotas; stale temp writes are removed before the next admission.
        parsed = self._request_target()
        if parsed is None:
            return
        params = self._parse_query(parsed.query)
        if params is None:
            return
        job = params.get('job', [''])[0]
        seg = params.get('seg', [''])[0]
        total = params.get('total', [''])[0]
        sig = params.get('sig', [''])[0]
        if not job or not seg:
            return self._json(400, {'error': 'missing job or seg'})
        if (seg.isascii() and seg.isdecimal()
                and len(seg) > _SEGMENT_DECIMAL_MAX_DIGITS):
            return self._json(
                400, {'error': 'seg must be a bounded ASCII decimal'})
        for val in (job, seg, total):
            if _unsafe_component(val):
                return self._json(400, {'error': 'invalid param'})
        if not seg.isascii() or not seg.isdecimal():
            return self._json(400, {'error': 'seg must be a bounded ASCII decimal'})
        try:
            segment_index = int(seg)
        except (ValueError, OverflowError):
            return self._json(400, {'error': 'seg must be a bounded ASCII decimal'})

        # `total` is untrusted progress metadata supplied by the page on every
        # request. Only the server-minted record below controls storage.
        with _seg_lock:
            record = _segment_record_for_sig(job, sig)
            quota = _segment_quota(record) if record is not None else None
            if quota is None:
                return self._json(403, {'error': 'bad sig'})
            max_index, max_count, max_bytes = quota
            if segment_index > max_index:
                return self._json(400, {'error': 'seg out of range'})

            seg_dir = SEG_DIR / job
            filename = f'{segment_index:06d}.ts'
            tmp = seg_dir / f'.{filename}.tmp'
            final = seg_dir / filename
            try:
                seg_dir.mkdir(parents=True, exist_ok=True)
                for path in seg_dir.iterdir():
                    if path.name.startswith('.') and path.name.endswith('.ts.tmp'):
                        path.unlink()
                segment_files = [
                    path for path in seg_dir.iterdir()
                    if path.is_file() and path.suffix == '.ts'
                ]
                if not final.is_file() and len(segment_files) >= max_count:
                    return self._json(
                        413, {'error': 'segment count limit exceeded'})
                stored_bytes = sum(path.stat().st_size for path in segment_files)
                replaced_bytes = final.stat().st_size if final.is_file() else 0
                if stored_bytes - replaced_bytes + len(raw) > max_bytes:
                    return self._json(413, {'error': 'job byte limit exceeded'})
                try:
                    tmp.write_bytes(raw)
                    os.replace(tmp, final)
                finally:
                    try:
                        tmp.unlink()
                    except FileNotFoundError:
                        pass
            except OSError:
                return self._json(500, {'error': 'segment storage failure'})
        print(f'[SEGMENT] {job}/{filename} ({len(raw)} bytes)', flush=True)
        return self._json(200, {'ok': True})

    def _handle_health(self):
        """GET /health — bridge liveness for detecting a silently-dead stream."""
        now = time.time()
        with _stream_lock:
            stream_tabs = sorted({k[1] for k in _active_streams})
        with _tab_lock:
            tokens = len(_tab_registry)
            tabs = sum(len(v) for v in _tab_registry.values())
        return self._json(200, {
            'ok': True,
            'uptime_s': round(now - _server_start_ts, 1),
            'active_streams': len(stream_tabs),
            'stream_tabs': stream_tabs,
            'registry': {'tokens': tokens, 'tabs': tabs},
            'last_delivery_s_ago': round(now - _last_delivery_ts, 1) if _last_delivery_ts else None,
            'cmd_ttl_s': CMD_TTL,
            'stream_max_age_s': STREAM_MAX_AGE,
        })

    def _handle_segment_status(self, params):
        """GET /segment-status?job=X&sig=S — list received segments."""
        job = params.get('job', [''])[0]
        sig = params.get('sig', [''])[0]
        if not job or _unsafe_component(job):
            return self._json(400, {'error': 'bad job'})
        if not _segment_sig_ok(job, sig):
            # Unknown job and wrong sig get the same answer: no existence oracle.
            return self._json(403, {'error': 'bad sig'})
        seg_dir = SEG_DIR / job
        try:
            done = sorted(int(f.stem) for f in seg_dir.iterdir()
                          if f.suffix == '.ts' and f.stem.isascii()
                          and f.stem.isdecimal()) if seg_dir.is_dir() else []
        except OSError:
            return self._json(500, {'error': 'segment storage failure'})
        return self._json(200, {'done': done, 'count': len(done)})

    def _handle_segment_job(self, body):
        """POST /segment-job — mint (or re-fetch) the capability for an HLS job.

        Idempotent for the owning token: the relay is documented as resumable,
        so re-minting returns the same sig and a resume keeps working. A job
        already owned by a different token answers 409. The record lives beside
        the job's directory so both survive together.
        """
        token = body['token']
        job = body.get('job', '')
        if not job or _unsafe_component(job):
            return self._json(400, {'error': 'bad job'})
        with _seg_lock:
            record = _load_segment_record(job)
            if record is not None:
                if record.get('token') != token:
                    return self._json(
                        409, {'error': 'job owned by a different token'})
                sig = record.get('sig', '')
                if not isinstance(sig, str) or not sig or not sig.isascii():
                    return self._json(409, {'error': 'job record cannot resume'})
                quota = _segment_quota(record)
                if quota is not None:
                    return self._json(200, {'ok': True, 'sig': sig})

                quota_fields = (
                    'max_segment_index', 'max_segment_count', 'max_bytes')
                if any(field in record for field in quota_fields):
                    return self._json(409, {'error': 'job record cannot resume'})
                if any(value < 0 for value in (
                        MAX_SEGMENT_INDEX, MAX_SEGMENTS_PER_JOB,
                        MAX_SEGMENT_JOB_SIZE)):
                    return self._json(409, {'error': 'job record cannot resume'})

                job_dir = SEG_DIR / job
                try:
                    if not job_dir.is_dir():
                        return self._json(
                            409, {'error': 'job record cannot resume'})
                    segment_files = [
                        path for path in job_dir.iterdir()
                        if path.is_file() and path.suffix == '.ts'
                    ]
                    stored_bytes = sum(
                        path.stat().st_size for path in segment_files)
                except OSError:
                    return self._json(
                        500, {'error': 'segment storage failure'})
                stored_indices = [
                    int(path.stem) for path in segment_files
                    if path.stem.isascii() and path.stem.isdecimal()
                ]
                if (len(segment_files) > MAX_SEGMENTS_PER_JOB
                        or stored_bytes > MAX_SEGMENT_JOB_SIZE
                        or any(index > MAX_SEGMENT_INDEX
                               for index in stored_indices)):
                    return self._json(
                        409, {'error': 'legacy job exceeds current quotas'})

                record = {
                    **record,
                    'max_segment_index': MAX_SEGMENT_INDEX,
                    'max_segment_count': MAX_SEGMENTS_PER_JOB,
                    'max_bytes': MAX_SEGMENT_JOB_SIZE,
                }
                tmp = SEG_DIR / f'.{job}.json.tmp'
                try:
                    tmp.write_text(json.dumps(record))
                    os.replace(tmp, _segment_record_path(job))
                except OSError:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    return self._json(
                        500, {'error': 'segment storage failure'})
                return self._json(200, {'ok': True, 'sig': sig})
            sig = secrets.token_urlsafe(32)
            record = {
                'token': token,
                'sig': sig,
                'max_segment_index': MAX_SEGMENT_INDEX,
                'max_segment_count': MAX_SEGMENTS_PER_JOB,
                'max_bytes': MAX_SEGMENT_JOB_SIZE,
            }
            job_dir = SEG_DIR / job
            made_dir = not job_dir.exists()
            tmp = SEG_DIR / f'.{job}.json.tmp'
            try:
                job_dir.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(record))
                os.replace(tmp, _segment_record_path(job))  # atomic publish
            except OSError:
                # Job names may contain dots, so the flat namespace collides
                # in EITHER minting order: with job 'a' taken, this mkdir for
                # job 'a.json' hits the existing 'a.json' record file; with
                # job 'a.json' taken, this os.replace for job 'a' targets the
                # existing 'a.json' directory. Both raise OSError and both
                # mean the name is unavailable. A refused mint must write
                # nothing, so the half-publish is rolled back: the tmp record,
                # and the job directory when this call created it.
                try:
                    tmp.unlink()
                except OSError:
                    pass
                if made_dir:
                    try:
                        job_dir.rmdir()
                    except OSError:
                        pass
                return self._json(409, {'error': 'job name unavailable'})
        return self._json(200, {'ok': True, 'sig': sig})

    def _handle_upload(self, body):
        """POST /upload — store binary data. Body: {token, id, data (base64), filename (optional)}.
        Screenshots: omit filename, stored as <token>/<id>/<timestamp>.<format>
        Generic: provide filename, stored as <token>/<id>/<filename>
        """
        token = body.get('token', '')
        upload_id = body.get('id', '')
        data_b64 = body.get('data', '')
        filename = body.get('filename', '')
        fmt = body.get('format', 'png')
        if fmt not in ('png', 'jpeg', 'jpg', 'webp'):
            return self._json(400, {'error': 'unsupported format'})
        if not upload_id:
            return self._json(400, {'error': 'missing id'})
        if not data_b64:
            return self._json(400, {'error': 'missing data'})
        # Sanitize path components
        for val in (token, upload_id, filename):
            if _unsafe_component(val):
                return self._json(400, {'error': 'invalid path component'})
        import base64
        try:
            raw = base64.b64decode(data_b64, validate=True)
        except Exception:
            return self._json(400, {'error': 'invalid base64'})
        dest_dir = UPLOAD_DIR / token / upload_id
        if filename:
            dest = dest_dir / filename
        else:
            ts = int(time.time() * 1000)
            dest = dest_dir / f'{ts}.{fmt}'
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        except OSError:
            return self._json(500, {'error': 'upload storage failure'})
        size = len(raw)
        del raw  # drop the decoded copy before responding
        # as_posix, not str: the wire format has to be one shape on
        # every platform, and the listing routes already build these
        # with forward slashes. str() yields backslashes on Windows,
        # so POST /upload and GET /uploads disagreed about the same
        # file and a client could not feed one to the other.
        rel = dest.relative_to(UPLOAD_DIR).as_posix()
        print(f'[UPLOAD] {rel} ({size} bytes)', flush=True)
        return self._json(200, {'ok': True, 'path': rel, 'size': size})

    def _handle_get_screenshot(self, params):
        """GET /screenshot?token=X&id=Y — serve latest screenshot for that id. Or token only for latest across all ids."""
        token = self._query_bridge_token(params)
        upload_id = params.get('id', [''])[0]
        if not self._require_bridge_token(token):
            return
        if upload_id and _unsafe_component(upload_id):
            return self._json(400, {'error': 'invalid path component'})
        token_dir = UPLOAD_DIR / token
        if not token_dir.is_dir():
            return self._json(404, {'error': 'no uploads'})
        # If id specified, look in that subdir; otherwise search all subdirs
        search_dirs = [token_dir / upload_id] if upload_id else sorted(token_dir.iterdir())
        # Find most recent image file
        latest = None
        for d in search_dirs:
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.suffix.lower() in ('.png', '.jpeg', '.jpg'):
                    if not latest or f.stat().st_mtime > latest.stat().st_mtime:
                        latest = f
        if not latest:
            return self._json(404, {'error': 'no screenshot'})
        fmt = latest.suffix.lstrip('.')
        return self._serve_file(latest, fmt)

    def _handle_get_dashboard(self, path):
        """GET /dashboard[/<asset>] — serve dashboard static assets from repo."""
        rel = path[len('/dashboard'):].lstrip('/')
        if not rel:
            rel = 'index.html'
        if any(_unsafe_component(part) for part in rel.split('/')):
            return self._json(400, {'error': 'bad path'})
        target = DASHBOARD_DIR / rel
        try:
            target.resolve().relative_to(DASHBOARD_DIR.resolve())
        except (ValueError, OSError):
            return self._json(400, {'error': 'bad path'})
        if not target.is_file():
            return self._json(404, {'error': 'not found'})
        mime_map = {
            '.html': 'text/html; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.json': 'application/json',
            '.svg': 'image/svg+xml',
            '.png': 'image/png',
            '.ico': 'image/x-icon',
            '.woff2': 'font/woff2',
        }
        mime = mime_map.get(target.suffix.lower(), 'application/octet-stream')
        try:
            data = target.read_bytes()
        except OSError:
            return self._json(500, {'error': 'dashboard storage failure'})
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, path, fmt):
        """Serve a binary file, streamed so large files aren't fully buffered in RAM."""
        mime_map = {'png': 'image/png', 'jpeg': 'image/jpeg', 'jpg': 'image/jpeg',
                    'json': 'application/json', 'txt': 'text/plain'}
        mime = mime_map.get(fmt, 'application/octet-stream')
        size = path.stat().st_size
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        with open(path, 'rb') as fh:
            shutil.copyfileobj(fh, self.wfile, 256 * 1024)
        if size > _TRIM_THRESHOLD:
            _malloc_trim()

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _cors(self):
        pass  # no CORS headers here by design -- see "Deployment" in README.md

    def log_message(self, format, *args):  # noqa: A002 — match base signature
        del format, args  # silence per-request access logging


if __name__ == '__main__':
    CMD_DIR.mkdir(parents=True, exist_ok=True)
    RES_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SEG_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(
        target=_command_gc_loop, name='command-gc', daemon=True).start()
    httpd = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    bridge_port = httpd.server_address[1]
    try:
        import mcp_server
        mcp_server.start_in_thread(f'http://127.0.0.1:{bridge_port}')
    except Exception as e:
        print(f'[Daedalus] MCP bootstrap failed: {_log_safe(e)}', flush=True)
    # ASCII only, deliberately: this line is the bridge's sole readiness
    # signal, and a console whose code page cannot encode a decorative
    # character raises rather than degrading, so the announcement would be
    # lost and every caller waiting on it would time out against a bridge
    # whose port is already open. cp437, still a Windows console default,
    # has no em dash.
    print(f'[Daedalus] Listening on 127.0.0.1:{bridge_port} - '
          f'base={_log_safe(BASE)}', flush=True)
    httpd.serve_forever()
