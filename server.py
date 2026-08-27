#!/usr/bin/env python3
"""Daedalus debug server — SSE command bridge + tab registry."""
import hmac, json, os, pathlib, shutil, threading, time, uuid
import ctypes, ctypes.util
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import TCPServer, ThreadingMixIn
from urllib.parse import urlparse, parse_qs

from daedalus_cli import SEGMENT_SIG_HEADER, ambiguous_request_carrier
from daedalus_cli.output import configure_stdio
from daedalus_cli.transport import token as _configured_token
import atomic_file
import command_queue
from log_safe import log_safe
import result_store
import segment_store
import stream_service
from bridge_config import (
    BASE, CMD_DIR, CMD_TTL, DASHBOARD_DIR,
    MAX_BODY_SIZE, MAX_JSON_DEPTH,
    MAX_REQUEST_WORKERS, MAX_SEGMENT_INDEX, MAX_SEGMENT_JOB_SIZE,
    MAX_SEGMENTS_PER_JOB, MAX_UNAUTHENTICATED_BODY, PORT, REQUEST_TIMEOUT,
    RES_DIR, SEG_DIR, STREAM_KEEPALIVE, STREAM_MAX_AGE,
    UPLOAD_DIR,
)
from env_config import REFUSED_BODY_DRAIN
import path_safety

# The bridge logs ids and page-supplied text it did not choose, to a console
# whose encoding it did not choose either: under a C locale a result id
# carrying an accent killed the handler thread mid-request and the client saw
# the connection close. This used to happen by accident — importing the CLI
# ran it as an import side effect — so it is called here, where a reader can
# see the bridge depends on it.
configure_stdio()


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
    print(f'[Daedalus] malloc tuning unavailable: {log_safe(_e)}', flush=True)


def _malloc_trim():
    """Return freed heap back to the OS (glibc). No-op where unavailable."""
    if _LIBC is not None:
        try:
            _LIBC.malloc_trim(0)
        except Exception:
            # Returning freed heap is an optimisation with no fallback to
            # attempt. A libc without the symbol, or one that refuses the
            # call, leaves the heap where it is and the bridge keeps serving.
            pass


def _stored_uploads(token_dir, upload_id):
    """Every stored file, newest id first and by name within an id.

    os.scandir rather than iterdir: the kernel already said whether an entry
    is a file or a directory, and asking pathlib the same question is a stat
    per entry. Deciding WHICH entries exist is separated here from describing
    them, so a caller can count everything while statting only what it is
    about to return.

    The order is the one the listing has always had, because a page is only
    meaningful if the sequence it slices is stable between requests.
    """
    if upload_id:
        id_dirs = [path_safety.under(token_dir, upload_id)]
    else:
        with os.scandir(token_dir) as entries:
            dirs = [entry for entry in entries if entry.is_dir()]
        dirs.sort(key=lambda entry: os.stat(entry.path).st_mtime, reverse=True)
        id_dirs = [pathlib.Path(entry.path) for entry in dirs]
    for id_dir in id_dirs:
        try:
            with os.scandir(id_dir) as entries:
                files = [entry for entry in entries if entry.is_file()]
        except (FileNotFoundError, NotADirectoryError):
            continue
        files.sort(key=lambda entry: entry.name)
        for entry in files:
            yield id_dir.name, entry


def _json_nests_deeper_than(raw, limit):
    """True when `raw` opens more than `limit` unclosed containers at once.

    A scan of the bytes rather than a parse: the answer has to be settled
    before json.loads builds anything, and before the interpreter's own
    recursion limit gets to decide it — which it did, differently per version.

    Bytes rather than text, so a hostile body is never decoded to be measured.
    Only ASCII structure counts, and a UTF-8 continuation byte is never an
    ASCII byte, so a multi-byte character cannot be mistaken for a brace. The
    string state matters for the same reason: a `{` inside a string literal
    opens nothing, and a `\\"` inside one does not close it.

    Malformed input is not this function's problem — a body with more closers
    than openers drives the count negative and json.loads rejects it on its
    own terms. This answers one question only.
    """
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:      # backslash
                escaped = True
            elif byte == 0x22:      # quote
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):  # { [
            depth += 1
            if depth > limit:
                return True
        elif byte in (0x7D, 0x5D):  # } ]
            depth -= 1
    return False


# ─── Tab registry ───
# Authoritative source: /sync-tabs (replaces all). /register only updates existing.


_tab_registry = {}  # {token: {tabId: {url, title, ts}}}
_tab_lock = threading.Lock()

_COMPAT_CONSUME_RETRY_ATTEMPTS = 8
_SEGMENT_DECIMAL_MAX_DIGITS = 20

# ─── Health / observability ───


_server_start_ts = time.time()


_UNDECLARED_BODY_DRAIN_SECONDS = 0.25


class _WorkerCount:
    """Live request workers, and the cap on how many may exist at once.

    The count and the lock guarding it are one object because every mutation
    has to hold that lock, and a module-level pair invites a call site that
    takes one without the other. Two operations, so the pairing an admitted
    worker owes a release is checkable by reading them rather than by finding
    every place that touches a counter.
    """

    def __init__(self, cap):
        self._cap = cap
        self._lock = threading.Lock()
        self._live = 0

    def admit(self):
        """True when a slot was taken, and then the caller owes a release."""
        with self._lock:
            if self._live >= self._cap:
                return False
            self._live += 1
            return True

    def release(self):
        with self._lock:
            self._live -= 1


_workers = _WorkerCount(MAX_REQUEST_WORKERS)


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def process_request(self, request, client_address):
        """Admit a connection only while the bridge is under its worker cap.

        The count is kept here rather than around the handler because this
        runs in the accept loop, before a thread exists: past the cap the
        connection is closed instead of being given one, which is the whole
        point — a thread spawned and then refused has already cost what the
        cap exists to bound. The refusal is a close rather than a 503, since
        writing one would put a blocking send in the accept loop, and a peer
        that is over the cap is the last one to hand the listener to.
        """
        if not _workers.admit():
            print(f'[HTTP] REFUSED at worker cap {MAX_REQUEST_WORKERS}',
                  flush=True)
            return self.shutdown_request(request)
        try:
            return super().process_request(request, client_address)
        except BaseException:
            # Spawning the worker failed, so nothing will run the release
            # below. Exhaustion is exactly what the cap guards against, and
            # a leaked count here would make the cap tighten permanently.
            _workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            _workers.release()

    def server_bind(self):
        """Bind and record the address without a reverse-DNS lookup.

        HTTPServer.server_bind resolves the bound host through
        socket.getfqdn, i.e. a name-service round trip, after the socket is
        already listening and before the caller regains control. Where that
        lookup is slow or answered by nothing — a host with no reverse zone
        for loopback, a resolver behind a firewall — startup stops here, so
        the Listening line, which is the only readiness signal the bridge
        emits, arrives minutes late or not at all while the port is in fact
        open. This repository's handler never reads server_name — the
        standard library's CGIHTTPRequestHandler does, so the claim is about
        Daedalus, not about the attribute — so record the literal bind host
        and keep startup free of the network. The lookup is the only
        deliberate difference: the port is assigned exactly as the standard
        library assigns it, so an address the stdlib method binds is not
        rejected here.
        """
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port


class _JSONObject(dict):
    """A parsed JSON object that remembers whether a key arrived twice.

    Duplicate keys collapse when the pairs become a dict, so the carrier says
    something the items no longer can. Equality has to include it for the same
    reason: two bodies with identical items are not the same request when one
    of them named an authority carrier twice, and inherited dict equality
    would call them equal. Comparison against a plain dict is unchanged.
    """

    def __init__(self, pairs):
        super().__init__(pairs)
        self.duplicate_carrier = ambiguous_request_carrier(
            key for key, _value in pairs)

    def __eq__(self, other):
        if isinstance(other, _JSONObject):
            return (super().__eq__(other)
                    and self.duplicate_carrier == other.duplicate_carrier)
        return super().__eq__(other)


def _normalized_tab_id(value):
    """Normalize string or integer tab-id JSON values; return None otherwise."""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


# One table for every place a screenshot format is decided: what /upload
# accepts, what /screenshot will discover on disk, and what content type it is
# served with. They were three separate lists, and `webp` was in the first
# only -- so the upload stored a file and answered 200 that /screenshot then
# reported as absent.
SCREENSHOT_TYPES = {
    'png': 'image/png',
    'jpeg': 'image/jpeg',
    'jpg': 'image/jpeg',
    'webp': 'image/webp',
}


class Handler(BaseHTTPRequestHandler):
    # socketserver applies this to the connection, so it bounds the request
    # line, the headers and the body alike. It is per socket operation: a
    # transfer that keeps making progress renews it and is never cut short.
    timeout = REQUEST_TIMEOUT

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

    def _authorization_bearer(self):
        """The Bearer credential this request carries, before it is checked.

        Returns `(present, token)` — `present` says whether an Authorization
        header was sent at all, which is not the same question as whether it
        carried anything — or None once the request has been answered. An
        empty Bearer value is a credential that fails, not an absent one.
        """
        duplicate = ambiguous_request_carrier(
            name.encode('latin-1', 'replace')
            for name in self.headers.keys() if name.lower() == 'authorization')
        if duplicate is not None:
            self._json(400, {'error': 'duplicate Authorization header'})
            return None
        header = self.headers.get('Authorization', '')
        if not header:
            return False, ''
        if not header.lower().startswith('bearer '):
            self._json(401, {'error': 'missing Bearer token'})
            return None
        return True, header[7:].strip()

    def _bridge_token(self, params):
        """The bridge token a GET route is acting under, or None once answered.

        A request target is retained by reverse-proxy access logs, browser
        tooling, and anything that copies a URL, so a reusable token written
        into one is durably recorded by every deployment that logs. The
        header is the carrier that keeps it out of all of them.

        The query form still works, because removing it would break every
        deployed caller and the leak is what this is about. A request that
        uses both must agree, on the same rule a body token already follows.
        """
        carrier = self._authorization_bearer()
        if carrier is None:
            return None
        present, header_token = carrier
        query = self._query_bridge_token(params)
        if not present:
            return query if self._require_bridge_token(query) else None
        if query and query != header_token:
            self._json(400, {'error': 'conflicting token'})
            return None
        return header_token if self._require_bridge_token(header_token) else None

    def _segment_capability(self, params):
        """The job capability a segment route acts under, or None once answered.

        A sig authorizes every write and status read for its job for as long
        as the job exists, so it is exactly as reusable as the bridge token
        and belongs out of the request target for the same reason. It follows
        the same rules: the header decides, both carriers must agree, and the
        query form still works because every deployed relay script uses it.
        """
        duplicate = ambiguous_request_carrier(
            name.encode('latin-1', 'replace') for name in self.headers.keys()
            if name.lower() == SEGMENT_SIG_HEADER.lower())
        if duplicate is not None:
            self._json(400, {'error': 'duplicate segment capability header'})
            return None
        header = self.headers.get(SEGMENT_SIG_HEADER, '')
        query = params.get('sig', [''])[0]
        if not header:
            return query
        if query and query != header:
            self._json(400, {'error': 'conflicting sig'})
            return None
        return header

    def _require_bridge_token(self, token):
        """Answer an error unless `token` matches the configured bridge secret."""
        if path_safety.bad_token(token):
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

    def _authenticate_before_body(self, clen):
        """Settle credentials before a byte of the body is read.

        Returns the header-authenticated token, `''` when there is no header
        and the body is small enough to still decide it, or None once the
        request has been answered.

        A body token cannot be checked without reading the body, which is the
        cost this exists to avoid: a 24 MiB request with an invalid token was
        received and parsed in full on its way to a 401, and every concurrent
        worker could be made to do the same. The Bearer header is the carrier
        that makes the decision reachable first. It is the same header, and
        the same comparison, the MCP listener already requires.

        The older body-token form still works below MAX_UNAUTHENTICATED_BODY,
        because a body that small is not the problem this is about.
        """
        carrier = self._authorization_bearer()
        if carrier is None:
            return None
        present, token = carrier
        if not present:
            if clen > MAX_UNAUTHENTICATED_BODY:
                # Answered without reading: naming the size would tell an
                # unauthenticated caller what the bound is, and 401 is the
                # true answer either way.
                self._json(401, {'error': 'unauthorized'})
                return None
            return ''
        return token if self._require_bridge_token(token) else None

    def _body_token(self, body, authenticated):
        """The token a parsed body is acting under, or None once answered.

        A request that authenticated by header need not repeat itself, so the
        token is put back into the body for the handlers that route on it.
        One that repeats it must agree: two different tokens in one request
        is an ambiguous carrier, which this bridge refuses rather than
        picking a side.
        """
        token = body.get('token', '')
        if authenticated:
            if token and token != authenticated:
                self._json(400, {'error': 'conflicting token'})
                return None
            body['token'] = authenticated
            return authenticated
        return token if self._require_bridge_token(token) else None

    def do_GET(self):
        parsed = self._request_target()
        if parsed is None:
            return None
        params = self._parse_query(parsed.query)
        if params is None:
            return None

        if parsed.path == '/result':
            return self._handle_get_result(params)

        if parsed.path == '/screenshot':
            return self._handle_get_screenshot(params)

        if parsed.path == '/upload':
            return self._handle_list_uploads(params)

        if parsed.path == '/tabs':
            token = self._bridge_token(params)
            if token is None:
                return None
            with _tab_lock:
                tabs = _tab_registry.get(token, {})
                result = [
                    {'tabId': tid, 'url': info.get('url', ''), 'title': info.get('title', ''), 'age': round(time.time() - info.get('ts', 0))}
                    for tid, info in tabs.items()
                ]
            return self._json(200, result)

        if parsed.path == '/segment-job':
            return self._handle_segment_job_lookup(params)

        if parsed.path == '/segment-status':
            return self._handle_segment_status(params)

        if parsed.path == '/health':
            return self._handle_health()

        if parsed.path == '/dashboard' or parsed.path.startswith('/dashboard/'):
            return self._handle_get_dashboard(parsed.path)

        if parsed.path != '/stream':
            return self._json(404, {'error': 'not found'})
        token = self._bridge_token(params)
        tab = params.get('tab', [''])[0]
        if token is None:
            print('[STREAM] REJECTED unauthorized token', flush=True)
            return None
        if tab and path_safety.unsafe_component(tab):
            print(f'[STREAM] REJECTED unsafe tab: {tab!r}', flush=True)
            return self._json(400, {'error': 'invalid path component'})
        # Resolved once, here, rather than per tick: the loop below runs
        # for the life of the connection, and the namespace a stream reads
        # from is decided when it is admitted, not re-decided every second.
        try:
            target_queue_name, legacy_name = (
                command_queue.command_target_names(token, tab))
            broadcast_queue_name, broadcast_legacy_name = (
                command_queue.command_target_names(token))
            target_queue = path_safety.under(CMD_DIR, target_queue_name)
            target_legacy = path_safety.under(CMD_DIR, legacy_name)
            broadcast_queue = path_safety.under(CMD_DIR, broadcast_queue_name)
            broadcast_legacy = path_safety.under(
                CMD_DIR, broadcast_legacy_name)
        except ValueError:
            print('[STREAM] REJECTED unsafe derived target', flush=True)
            return self._json(400, {'error': 'invalid path component'})
        # Kill old stream for the same tab, register this one. Every stream is
        # registered, tabless ones included: one that is not is a worker and a
        # command consumer that /health cannot see.
        stream_id, killed_event = stream_service.register(token, tab)
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
        writer = partial(stream_service.write_frame, self.wfile)
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
                ev = command_queue.event(token)
                ev.clear()
                delivered = 0
                if tab == 'dashboard':
                    delivered += stream_service.drain_queue(
                        target_queue, None, killed_event,
                        command_ttl=CMD_TTL, frame_writer=writer)
                elif tab == 'extension':
                    # Typed commands addressed to the extension itself
                    delivered += stream_service.drain_queue(
                        target_queue, None, killed_event,
                        command_ttl=CMD_TTL, frame_writer=writer)
                    delivered += stream_service.drain_legacy_file(
                        target_legacy, None, command_ttl=CMD_TTL,
                        frame_writer=writer)
                    # Per-tab eval queues for every other tab (tag chromeTab so bg can route)
                    prefix = f'{token}_'
                    for entry in sorted(CMD_DIR.iterdir()):
                        if not entry.is_dir() or not entry.name.startswith(prefix):
                            continue
                        sub = entry.name[len(prefix):]
                        if sub in ('extension', 'dashboard'):
                            continue
                        delivered += stream_service.drain_queue(
                            entry, sub, killed_event, command_ttl=CMD_TTL,
                            frame_writer=writer)
                    # Broadcast queue + legacy per-tab raw-file drops
                    delivered += stream_service.drain_queue(
                        broadcast_queue, None, killed_event,
                        command_ttl=CMD_TTL, frame_writer=writer)
                    delivered += stream_service.drain_legacy_ext(
                        CMD_DIR, token, killed_event, frame_writer=writer,
                        extension_legacy_name=legacy_name, command_ttl=CMD_TTL)
                else:  # specific-tab stream (rare — normal clients use tab=extension)
                    delivered += stream_service.drain_queue(
                        target_queue, None, killed_event,
                        command_ttl=CMD_TTL, frame_writer=writer)
                    if tab:
                        delivered += stream_service.drain_queue(
                            broadcast_queue, None, killed_event,
                            command_ttl=CMD_TTL, frame_writer=writer)
                        delivered += stream_service.drain_legacy_file(
                            target_legacy, None, command_ttl=CMD_TTL,
                            frame_writer=writer)
                # Broadcast legacy raw-file — skip for dashboard so it doesn't steal commands
                if tab != 'dashboard':
                    delivered += stream_service.drain_legacy_file(
                        broadcast_legacy, None, command_ttl=CMD_TTL,
                        frame_writer=writer)

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
            stream_service.unregister(stream_id, killed_event)
        return None

    def do_POST(self):
        clen = self._declared_body_length()
        if clen is None:
            return None
        try:
            return self._dispatch_post(clen)
        finally:
            if clen > _TRIM_THRESHOLD:
                _malloc_trim()

    def _drain_refused_body(self, clen):
        """Absorb a refused body, up to a bound, before answering it.

        Closing a socket that still has unread data sends RST rather than
        FIN, and an RST DISCARDS whatever the peer has not read yet — so the
        413 written a moment later never reaches the client, and the refusal
        arrives as a connection reset instead. That is why an oversized body
        of nine bytes could fail a caller that was handling the refusal
        correctly.

        Bounded on purpose: the cap exists so a huge body is never read into
        this process, and draining without a limit would give that away. Past
        the bound the early close stands, which is the right trade for a
        caller sending far more than the server will take.
        """
        remaining = min(clen, REFUSED_BODY_DRAIN)
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 8192))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            # The drain is a courtesy to the refusal already written: it
            # exists so the close is a FIN rather than an RST. A socket that
            # errors here has nothing left to receive that answer anyway.
            pass

    def _drain_undeclared_body(self):
        """Absorb a bounded undeclared body so the refusal survives the close.

        Same mechanism as _drain_refused_body: closing a socket that still
        has unread data sends RST, and an RST discards the answer the client
        has not read yet. With no declared length there is nothing to count
        down, so the read is bounded by the same byte cap and by a short
        timeout — a sender that declared no length has no claim on more.
        """
        try:
            original = self.connection.gettimeout()
        except OSError:
            return
        try:
            self.connection.settimeout(_UNDECLARED_BODY_DRAIN_SECONDS)
            self.rfile.read(REFUSED_BODY_DRAIN)
        except (OSError, ValueError):
            # Same courtesy as _drain_refused_body, and the same reason it
            # cannot matter: nothing is read out of this body.
            pass
        finally:
            try:
                self.connection.settimeout(original)
            except OSError:
                # The connection is closing either way, so a socket that will
                # not take its timeout back is one nothing else will use.
                pass

    def _declared_body_length(self):
        """Parse and bound the Content-Length header of a body-reading request.

        Every verb that reads a body (POST, PUT, DELETE) gates on this, so the
        refusal rules live in exactly one place. Returns the byte count the
        handler may read, or None once the request has already been answered:
        411 for no declaration at all (defaulting it to zero discarded the
        body the sender did send — on the raw segment route that stored an
        empty .ts and answered success, since those bytes are opaque rather
        than JSON that has to parse), 400 for a value int() cannot parse (an
        uncaught ValueError here used to kill the request thread, dropping
        the connection with no answer), 400 for a negative value
        (rfile.read(-1) reads to EOF, so a negative length is not a small
        body — it is an unbounded one, and testing only
        `clen > MAX_BODY_SIZE` let it straight through), and 413 for one over
        MAX_BODY_SIZE.

        Every request this bridge answers carries a body, so nothing it
        serves loses a legitimate call to the 411.
        """
        declared = self.headers.get('Content-Length')
        if declared is None:
            self._drain_undeclared_body()
            return self._json(411, {'error': 'Content-Length required'})
        try:
            clen = int(declared)
        except ValueError:
            self._json(400, {'error': 'invalid Content-Length'})
            return None
        if clen < 0:
            self._json(400, {'error': 'invalid Content-Length'})
            return None
        if clen > MAX_BODY_SIZE:
            self._drain_refused_body(clen)
            # Anything past the drain bound is NOT read: those bytes die with
            # the socket. That holds only while protocol_version stays at the
            # HTTP/1.0 default, which keeps close_connection true on every
            # request — raise it and the drain must become total, or a
            # leftover body would be parsed as the next kept-alive request.
            self._json(413, {'error': 'request body too large'})
            return None
        return clen

    def _read_body(self, clen):
        """Read one declared body, or None once a deadline answered it.

        Every verb that reads a body goes through here, so the deadline is
        stated once: a peer that declares a length and then stops sending is
        answered 408 and its worker released, rather than parked on the read
        until the peer decides to close.
        """
        try:
            return self.rfile.read(clen)
        except TimeoutError:
            self._json(408, {'error': 'request body timed out'})
            return None

    def _load_json_object(self, clen):
        """Read one JSON body, answering 400 unless it is an object."""
        raw = self._read_body(clen)
        if raw is None:
            return None
        if _json_nests_deeper_than(raw, MAX_JSON_DEPTH):
            self._json(400, {'error': 'JSON body too deeply nested'})
            return None
        try:
            body = json.loads(raw, object_pairs_hook=_JSONObject)
        except RecursionError:
            # Unreachable through nesting now that the bound above decides it,
            # and kept for the case it never covered: an interpreter whose
            # recursion limit is lower than MAX_JSON_DEPTH, where json.loads
            # still runs out of stack on a body this bridge considers shallow.
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
            # The 400 is written; its body was never read, and an unread
            # body turns the close into an RST that discards the answer.
            return self._drain_refused_body(clen)
        if (parsed.path == '/segment'
                and ('octet-stream' in content_type
                     or 'application/json' not in content_type)):
            # Everything this route authorizes on rides in the query string,
            # so the answer never depends on a byte of the body. Deciding
            # first is what keeps a refusal cheap: reading the body and then
            # answering 403 charged the process for a request it was always
            # going to reject.
            admitted = self._segment_admission(parsed)
            if admitted is None:
                # The refusal is written; the body is drained rather than
                # read, because closing on unread bytes sends RST and the
                # answer would be discarded with them.
                return self._drain_refused_body(clen)
            raw = self._read_body(clen)
            if raw is None:
                return None
            return self._handle_segment(raw, *admitted)
        authenticated = self._authenticate_before_body(clen)
        if authenticated is None:
            return self._drain_refused_body(clen)
        body = self._load_json_object(clen)
        if body is None:
            return None
        token = self._body_token(body, authenticated)
        if token is None:
            return None

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
                command_queue.notify_dashboard(
                    CMD_DIR, token,
                    {'type': 'tab-updated', 'tabId': tab_id,
                     'url': url, 'title': title})
            # This route is update-only, so a tab the registry has never seen
            # is a no-op — and answering it {'ok': True} told the caller its
            # state had been refreshed when nothing had. `updated` is what
            # separates the two, so a client whose tab has fallen out of the
            # registry can notice and re-sync instead of reporting stale
            # entries forever.
            return self._json(200, {'ok': True, 'updated': updated})

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
            command_queue.notify_dashboard(
                CMD_DIR, token, {'type': 'tabs-synced', 'count': count})
            return self._json(200, {'ok': True, 'count': count})

        elif self.path == '/unregister':
            tab_id = body.get('tabId', '')
            if not tab_id:
                return self._json(400, {'error': 'missing tabId'})
            with _tab_lock:
                tabs = _tab_registry.get(token, {})
                removed = tabs.pop(str(tab_id), None)
            command_queue.notify_dashboard(
                CMD_DIR, token,
                {'type': 'tab-unregistered', 'tabId': str(tab_id)})
            return self._json(200, {'ok': True, 'removed': removed is not None})

        elif self.path == '/poll':
            # Both of these raise ValueError on a name that cannot be a safe
            # component or a path that leaves the queue root, and this route
            # had no guard for the first of them either.
            try:
                _, legacy_name = command_queue.command_target_names(token)
                cmd_file = path_safety.under(CMD_DIR, legacy_name)
            except ValueError:
                return self._json(400, {'error': 'invalid path component'})
            data = {}
            with command_queue.command_fs_lock:
                if cmd_file.exists():
                    try:
                        candidate = json.loads(cmd_file.read_text(encoding='utf-8'))
                        if isinstance(candidate, dict):
                            data = candidate
                            cmd_file.unlink()
                    except (OSError, json.JSONDecodeError,
                            RecursionError, ValueError):
                        # A legacy drop that cannot be read is not a command.
                        # The empty answer below is the same one an absent
                        # file gives, and the file is left to the TTL sweep.
                        pass
            return self._json(200, data)

        elif self.path == '/upload':
            return self._handle_upload(body)

        elif self.path == '/segment-job':
            return self._handle_segment_job(body)

        elif self.path == '/result':
            tab_id = body.get('tabId', '')
            if tab_id and path_safety.unsafe_component(tab_id):
                return self._json(400, {'error': 'invalid path component'})
            try:
                token_result_slot = path_safety.under(
                    RES_DIR, path_safety.derived_component(f'{token}.json'))
                tab_result_slot = (
                    path_safety.under(
                        RES_DIR,
                        path_safety.derived_component(
                            f'{token}_{tab_id}.json'))
                    if tab_id else None)
            except ValueError:
                return self._json(400, {'error': 'invalid path component'})
            print(
                f'[RESULT] tab={tab_id[:8] if tab_id else "none"} '
                f'id={log_safe(body.get("id", ""))}', flush=True)
            # Full server-observed roundtrip: _did's leading ms is the enqueue
            # instant (same clock as now), so no skew. Skip if _did is absent/malformed.
            # _did remains internal on the extension wire. Surface its value as
            # deliveryId so waiters can correlate a result with this invocation.
            did = body.pop('_did', '')
            # Only a string delivery id is one: anything else is not a key the
            # dedup record can hold, and pushing it in there would raise.
            if not isinstance(did, str):
                did = ''
            try:
                delivery_dir, delivery_file = (
                    result_store.delivery_result_paths(
                        token, tab_id, did) if did else (None, None))
            except ValueError:
                return self._json(400, {'error': 'invalid path component'})
            body.pop('deliveryId', None)
            body['resultGeneration'] = uuid.uuid4().hex
            if did:
                body['deliveryId'] = did
            if '_' in did:
                try:
                    body['roundtrip_ms'] = int(time.time() * 1000) - int(did.split('_')[0])
                except ValueError:
                    # A delivery id is not required to carry a millisecond
                    # prefix. When it does not, the reading is absent from the
                    # result rather than the result being refused.
                    pass
            try:
                serialized = json.dumps(
                    body, ensure_ascii=False).encode('utf-8')
            except (TypeError, ValueError, RecursionError):
                return self._json(400, {'error': 'result is not encodable'})
            duplicate = False
            try:
                if delivery_dir is not None:
                    assert delivery_file is not None
                    with result_store.delivery_lock_for(
                            result_store.result_key(token, tab_id)):
                        with result_store.result_lock:
                            duplicate = result_store.delivery_recorded(did)
                        if not duplicate:
                            delivery_dir.mkdir(parents=True, exist_ok=True)
                            entries = result_store.scan_delivery_results(
                                delivery_dir)
                            with result_store.result_lock:
                                duplicate = (
                                    result_store.delivery_recorded(did))
                                if not duplicate:
                                    if tab_result_slot is not None:
                                        result_store.atomic_result_write(
                                            tab_result_slot, serialized)
                                    result_store.atomic_result_write(
                                        token_result_slot, serialized)
                            if not duplicate:
                                result_store.atomic_result_write(
                                    delivery_file, serialized)
                                stamp = result_store.mark_delivery_result(
                                    delivery_file, entries)
                                with result_store.result_lock:
                                    result_store.record_delivery(did)
                                if stamp is not None and entries is not None:
                                    entries = [
                                        (old_stamp, name)
                                        for old_stamp, name in entries
                                        if name != delivery_file.name]
                                    entries.append((stamp, delivery_file.name))
                                    try:
                                        result_store.evict_delivery_results(
                                            delivery_dir, entries)
                                    except OSError:
                                        # The result is stored and its caller
                                        # can read it. Failing to trim
                                        # retention must not drop that answer,
                                        # and the next write to this target
                                        # evicts what this one could not.
                                        pass
                else:
                    with result_store.result_lock:
                        if tab_result_slot is not None:
                            result_store.atomic_result_write(
                                tab_result_slot, serialized)
                        result_store.atomic_result_write(
                            token_result_slot, serialized)
            except OSError:
                return self._json(500, {'error': 'result storage failure'})
            if duplicate:
                # A retry of a delivery already stored. Answering 200 is what
                # stops the extension retrying again; rewriting the slots is
                # what would lose a newer result, so this does the first and
                # not the second, and publishes no second dashboard event.
                return self._json(200, {'ok': True, 'duplicate': True})
            command_queue.notify_dashboard(CMD_DIR, token, {
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
            return None
        authenticated = self._authenticate_before_body(clen)
        if authenticated is None:
            return self._drain_refused_body(clen)
        body = self._load_json_object(clen)
        if body is None:
            return None
        if self._body_token(body, authenticated) is None:
            return None
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
            if path_safety.unsafe_component(val):
                return self._json(400, {'error': 'invalid path component'})
        # A filename names a file inside an id, so without one it matches
        # neither the file branch nor the id branch below and used to reach the
        # branch that removes the token's whole namespace: naming one file
        # deleted every upload the token had, and answered that as success.
        if filename and not upload_id:
            return self._json(400, {'error': 'filename requires id'})
        # under rather than a join: this branch removes trees, so the
        # question that matters is where the path ended up, not how each
        # component looked. A ValueError here is the same refusal the shape
        # check above gives, reached by the other route.
        try:
            if filename and upload_id:
                target = path_safety.under(
                    UPLOAD_DIR, token, upload_id, filename)
                if not target.is_file():
                    return self._json(404, {'error': 'file not found'})
                target.unlink()
                print(f'[DELETE] {token}/{upload_id}/{filename}', flush=True)
            elif upload_id:
                target = path_safety.under(UPLOAD_DIR, token, upload_id)
                if not target.is_dir():
                    return self._json(404, {'error': 'id not found'})
                shutil.rmtree(target)
                print(f'[DELETE] {token}/{upload_id}/', flush=True)
            else:
                target = path_safety.under(UPLOAD_DIR, token)
                if not target.is_dir():
                    return self._json(404, {'error': 'token not found'})
                shutil.rmtree(target)
                print(f'[DELETE] {token}/', flush=True)
        except ValueError:
            return self._json(400, {'error': 'invalid path component'})
        except OSError:
            return self._json(500, {'error': 'upload delete failure'})
        return self._json(200, {'ok': True})

    def do_PUT(self):
        clen = self._declared_body_length()
        if clen is None:
            return None
        parsed = self._request_target()
        if parsed is None:
            # The 400 is written; its body was never read.
            return self._drain_refused_body(clen)
        if parsed.path == '/command':
            return self._handle_put_command(clen)
        self._json(404, {'error': 'not found'})
        # A refused PUT never reads its body, and closing a socket that
        # still holds unread data sends RST — which discards the answer
        # written a moment ago, so the caller sees a connection reset
        # instead of the 404. Same mechanism as the oversize-body drain.
        return self._drain_refused_body(clen)

    def _handle_put_command(self, clen):
        """PUT /command — write a command for delivery via SSE."""
        authenticated = self._authenticate_before_body(clen)
        if authenticated is None:
            return self._drain_refused_body(clen)
        body = self._load_json_object(clen)
        if body is None:
            return None
        token = self._body_token(body, authenticated)
        if token is None:
            return None
        tab = str(body.get('tab', ''))
        cmd_id = body.get('id', '')
        code = body.get('code', '')
        cmd_type = body.get('type', '')
        if tab and path_safety.unsafe_component(tab):
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
            did = command_queue.enqueue(CMD_DIR, token, tab, cmd)
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
        print(
            f'[PUT-CMD] {target} id={log_safe(cmd_id)} did={did}',
            flush=True)
        return self._json(200, {'ok': True, 'target': target, 'did': did})

    def _handle_get_result(self, params):
        """Fetch a slot or delivery result, optionally consuming it."""
        token = self._bridge_token(params)
        tab = params.get('tab', [''])[0]
        delivery = params.get('delivery', [''])[0]
        consume = params.get('consume', [''])[0] == '1'
        expected = params.get('expected', [''])[0]
        if token is None:
            return None
        if tab and path_safety.unsafe_component(tab):
            return self._json(400, {'error': 'invalid path component'})
        delivery_tab = ''
        delivery_dir = None
        try:
            if delivery:
                delivery_dir, res_file, delivery_tab = (
                    result_store.find_delivery_result(
                        token, tab, delivery)
                )
            else:
                # A requested tab selects its own slot; otherwise use the token slot.
                res_file = path_safety.under(
                    RES_DIR,
                    path_safety.derived_component(
                        f'{result_store.result_key(token, tab)}.json'))
        except ValueError:
            return self._json(400, {'error': 'invalid path component'})
        try:
            if delivery:
                assert delivery_dir is not None
                with result_store.delivery_lock_for(
                        result_store.result_key(token, delivery_tab)):
                    with result_store.result_lock:
                        response, _ = result_store.read_result_file(
                            res_file, consume, expected)
                        if consume:
                            consumed = (response.get('consumed') is True
                                        if expected
                                        else 'resultGeneration' in response)
                            if consumed:
                                generation = response.get('resultGeneration', '')
                                slot_names = [f'{token}.json']
                                if delivery_tab:
                                    slot_names.append(
                                        f'{token}_{delivery_tab}.json')
                                for slot_name in slot_names:
                                    slot = path_safety.under(
                                        RES_DIR,
                                        path_safety.derived_component(
                                            slot_name))
                                    result_store.remove_matching_result_file(
                                        slot, generation)
            elif consume:
                for _attempt in range(_COMPAT_CONSUME_RETRY_ATTEMPTS):
                    with result_store.result_lock:
                        preview, _ = result_store.read_result_file(
                            res_file, False, '')
                    preview_delivery = (preview.get('deliveryId', '')
                                        if isinstance(preview, dict) else '')
                    if (not isinstance(preview_delivery, str)
                            or not preview_delivery
                            or path_safety.unsafe_component(preview_delivery)):
                        with result_store.result_lock:
                            current, _current_delivery = (
                                result_store.read_result_file(
                                    res_file, False, '')
                            )
                            current_delivery = (current.get('deliveryId', '')
                                                if isinstance(current, dict)
                                                else '')
                            if current_delivery != preview_delivery:
                                continue
                            response, _result_delivery = (
                                result_store.read_result_file(
                                    res_file, True, expected)
                            )
                        break
                    try:
                        candidate_dir, candidate_file, candidate_tab = (
                            result_store.find_delivery_result(
                                token, tab, preview_delivery))
                    except ValueError:
                        candidate_dir = None
                    if candidate_dir is None:
                        with result_store.result_lock:
                            response, _result_delivery = (
                                result_store.read_result_file(
                                    res_file, True, expected)
                            )
                        break
                    changed = False
                    with result_store.delivery_lock_for(
                            result_store.result_key(token, candidate_tab)):
                        with result_store.result_lock:
                            current, _current_delivery = (
                                result_store.read_result_file(
                                    res_file, False, '')
                            )
                            current_delivery = (current.get('deliveryId', '')
                                                if isinstance(current, dict)
                                                else '')
                            if current_delivery != preview_delivery:
                                changed = True
                            else:
                                response, _result_delivery = (
                                    result_store.read_result_file(
                                        res_file, True, expected))
                                consumed = (
                                    response.get('consumed') is True
                                    if expected
                                    else 'resultGeneration' in response)
                                if consumed:
                                    generation = response.get(
                                        'resultGeneration', '')
                                    result_store.remove_matching_result_file(
                                        candidate_file, generation)
                    if not changed:
                        break
                else:
                    # The slot's delivery id kept changing under us. Consume on
                    # the caller's own terms and leave the mirrored copy for
                    # eviction: cross-copy cleanup is best effort, but the
                    # caller's generation precondition is not.
                    with result_store.result_lock:
                        response, _ = result_store.read_result_file(
                            res_file, True, expected)
            else:
                with result_store.result_lock:
                    response, _ = result_store.read_result_file(
                        res_file, consume, expected)
        except (OSError, json.JSONDecodeError, ValueError):
            return self._json(500, {'error': 'result storage failure'})
        return self._json(200, response)

    def _handle_list_uploads(self, params):
        """GET /upload?token=X[&id=Y][&limit=N&offset=M] — list uploaded files.
        When limit or offset is provided, returns {items, total, limit, offset}.
        Without either, returns a bare array (back-compat)."""
        token = self._bridge_token(params)
        upload_id = params.get('id', [''])[0]
        limit_p = params.get('limit', [None])[0]
        offset_p = params.get('offset', [None])[0]
        if token is None:
            return None
        if upload_id and path_safety.unsafe_component(upload_id):
            return self._json(400, {'error': 'invalid path component'})
        # Before the directory is looked at, so that whether a query is well
        # formed does not depend on whether anything has been uploaded yet:
        # the shortcut below used to answer 200 for a malformed limit on an
        # empty data root and 400 for the same query once the directory
        # existed.
        paged = limit_p is not None or offset_p is not None
        lim, off = 200, 0
        if paged:
            try:
                lim = int(limit_p) if limit_p is not None else 200
                off = int(offset_p) if offset_p is not None else 0
            except ValueError:
                return self._json(400, {'error': 'invalid limit/offset'})
            lim = max(1, min(lim, 1000))
            off = max(0, off)
        try:
            token_dir = path_safety.under(UPLOAD_DIR, token)
        except ValueError:
            return self._json(400, {'error': 'invalid path component'})
        if not token_dir.is_dir():
            if paged:
                return self._json(200, {'items': [], 'total': 0,
                                        'limit': lim, 'offset': off})
            return self._json(200, [])
        # Counting is not describing. Every stored file is counted, because
        # `total` says how many pages there are; only the page's own files are
        # statted, because size and mtime are a syscall each. `limit=1` used
        # to stat every file in the namespace twice to describe one of them.
        window = range(off, off + lim) if paged else None
        results = []
        total = 0
        # The walk is inside the guard because _stored_uploads re-roots the
        # named id under the token directory, and a listing must refuse an
        # escape the same way a delete does rather than dying mid-response.
        try:
            for index, (id_name, entry) in enumerate(
                    _stored_uploads(token_dir, upload_id)):
                total += 1
                if window is not None and index not in window:
                    continue
                info = os.stat(entry.path)
                results.append({
                    'id': id_name,
                    'filename': entry.name,
                    'size': info.st_size,
                    'mtime': int(info.st_mtime),
                    'path': f'{token}/{id_name}/{entry.name}',
                })
        except ValueError:
            return self._json(400, {'error': 'invalid path component'})
        if paged:
            return self._json(200, {'items': results, 'total': total,
                                    'limit': lim, 'offset': off})
        return self._json(200, results)

    def _segment_admission(self, parsed):
        """Settle POST /segment?job=X&seg=N&total=T&sig=S before its body.

        The documented poster is page JavaScript running in a hostile page's
        MAIN world, so it must never hold the bridge token. It carries the
        job-scoped capability minted by POST /segment-job instead. A stolen
        sig authorizes status reads and segment writes only for that job. The
        finalized .ts set stays inside the record's index, count, and byte
        quotas; stale temp writes are removed before the next admission.

        Returns (job, segment index, quota, directory) for a request that may
        proceed,        or None once the refusal has been written. The quota travels with the
        admission rather than being read again under the write lock: a
        record's recorded limits are fixed at mint and never rewritten, so
        re-reading them would cost a second file read per segment and settle
        nothing the first read did not.
        """
        params = self._parse_query(parsed.query)
        if params is None:
            return None
        job = params.get('job', [''])[0]
        seg = params.get('seg', [''])[0]
        total = params.get('total', [''])[0]
        sig = self._segment_capability(params)
        if sig is None:
            return None
        if not job or not seg:
            self._json(400, {'error': 'missing job or seg'})
            return None
        if (seg.isascii() and seg.isdecimal()
                and len(seg) > _SEGMENT_DECIMAL_MAX_DIGITS):
            self._json(400, {'error': 'seg must be a bounded ASCII decimal'})
            return None
        for val in (job, seg, total):
            if path_safety.unsafe_component(val):
                self._json(400, {'error': 'invalid param'})
                return None
        if not seg.isascii() or not seg.isdecimal():
            self._json(400, {'error': 'seg must be a bounded ASCII decimal'})
            return None
        try:
            segment_index = int(seg)
        except (ValueError, OverflowError):
            self._json(400, {'error': 'seg must be a bounded ASCII decimal'})
            return None

        # `total` is untrusted progress metadata supplied by the page on every
        # request. Only the server-minted record controls storage.
        try:
            seg_dir = path_safety.under(SEG_DIR, job)
            with segment_store.seg_lock:
                record = segment_store.record_for_sig(job, sig)
                quota = (segment_store.quota(record)
                         if record is not None else None)
        except ValueError:
            self._json(400, {'error': 'invalid param'})
            return None
        if quota is None:
            self._json(403, {'error': 'bad sig'})
            return None
        if segment_index > quota[0]:
            self._json(400, {'error': 'seg out of range'})
            return None
        # The directory travels with the admission so the namespace is decided
        # once, here, where the refusal is a 400 about the request rather than
        # a storage error raised under the write lock.
        return job, segment_index, quota, seg_dir

    def _handle_segment(self, raw, job, segment_index, quota, seg_dir):
        """Store one admitted segment body under the job's remaining budget.

        The capability, the parameter shapes and the quota were settled by
        _segment_admission. What is left has to be atomic: the file listing,
        the byte sum and the write happen under one hold of
        segment_store.seg_lock, so two
        segments arriving together cannot both spend the same remaining bytes.
        """
        _, max_count, max_bytes = quota
        marks = segment_store.timing_marks()
        with segment_store.seg_lock:
            if marks is not None:
                marks.append(('acquire', time.perf_counter()))
            filename = f'{segment_index:06d}.ts'
            tmp = seg_dir / f'.{filename}.tmp'
            final = seg_dir / filename
            try:
                seg_dir.mkdir(parents=True, exist_ok=True)
                # The totals are read here rather than carried from admission,
                # and this is the difference between them and the quota: a
                # quota is fixed at mint, while these change with every write,
                # so a value read outside this lock could be spent twice.
                try:
                    record = segment_store.load_record(job)
                except segment_store.SegmentRecordError:
                    record = None
                usage = (segment_store.usage(record)
                         if record is not None else None)
                if usage is None:
                    # A job minted before totals were kept, converted once.
                    # This is the only scan left on this path, and no segment
                    # written afterwards pays for it.
                    usage = segment_store.recount(seg_dir)
                    if usage is None:
                        return self._json(
                            500, {'error': 'segment storage failure'})
                stored_count, stored_bytes = usage
                if marks is not None:
                    marks.append(('usage', time.perf_counter()))
                # One stat, for the one file this request may be replacing.
                try:
                    replaced_bytes = final.stat().st_size
                    replacing = True
                except FileNotFoundError:
                    replaced_bytes = 0
                    replacing = False
                if marks is not None:
                    marks.append(('replaced', time.perf_counter()))
                if not replacing and stored_count >= max_count:
                    return self._json(
                        413, {'error': 'segment count limit exceeded'})
                if stored_bytes - replaced_bytes + len(raw) > max_bytes:
                    return self._json(413, {'error': 'job byte limit exceeded'})
                try:
                    tmp.write_bytes(raw)
                    atomic_file.replace_atomically(tmp, final)
                finally:
                    try:
                        tmp.unlink()
                    except FileNotFoundError:
                        # os.replace consumed it, which is the success path.
                        pass
                if marks is not None:
                    marks.append(('write', time.perf_counter()))
                segment_store.write_usage(
                    job,
                    stored_count + (0 if replacing else 1),
                    stored_bytes - replaced_bytes + len(raw))
                if marks is not None:
                    marks.append(('record', time.perf_counter()))
                    segment_store.log_timing(
                        log_safe(job), stored_count, marks)
            except OSError:
                return self._json(500, {'error': 'segment storage failure'})
        print(f'[SEGMENT] {job}/{filename} ({len(raw)} bytes)', flush=True)
        return self._json(200, {'ok': True})

    def _handle_health(self):
        """GET /health — bridge liveness for detecting a silently-dead stream."""
        now = time.time()
        live_streams, stream_tabs = stream_service.snapshot()
        with _tab_lock:
            tokens = len(_tab_registry)
            tabs = sum(len(v) for v in _tab_registry.values())
        last_delivery_at = stream_service.last_delivery_at()
        return self._json(200, {
            'ok': True,
            'uptime_s': round(now - _server_start_ts, 1),
            'active_streams': live_streams,
            'stream_tabs': stream_tabs,
            'registry': {'tokens': tokens, 'tabs': tabs},
            'last_delivery_s_ago': (
                round(now - last_delivery_at, 1)
                if last_delivery_at else None),
            'cmd_ttl_s': CMD_TTL,
            'stream_max_age_s': STREAM_MAX_AGE,
        })

    def _handle_segment_status(self, params):
        """GET /segment-status?job=X&sig=S — list received segments."""
        job = params.get('job', [''])[0]
        sig = self._segment_capability(params)
        if sig is None:
            return None
        if not job or path_safety.unsafe_component(job):
            return self._json(400, {'error': 'bad job'})
        # Both path uses inside one guard: the directory and the record the
        # sig is checked against are separate joins, and either can be the one
        # that leaves the namespace.
        try:
            seg_dir = path_safety.under(SEG_DIR, job)
            authorized = segment_store.sig_ok(job, sig)
        except ValueError:
            return self._json(400, {'error': 'bad job'})
        if not authorized:
            # Unknown job and wrong sig get the same answer: no existence oracle.
            return self._json(403, {'error': 'bad sig'})
        try:
            done = sorted(int(f.stem) for f in seg_dir.iterdir()
                          if f.suffix == '.ts' and f.stem.isascii()
                          and f.stem.isdecimal()) if seg_dir.is_dir() else []
        except OSError:
            return self._json(500, {'error': 'segment storage failure'})
        return self._json(200, {'done': done, 'count': len(done)})

    def _handle_segment_job_lookup(self, params):
        """GET /segment-job?token=X&job=Y — the capability, without minting.

        POST mints a job that does not exist yet, which is what a producer
        wants and the opposite of what a status query wants: asking about a
        name that was never used created it, so a typo left a permanent
        record behind and answered as though the job were real.

        Unlike GET /segment-status this route takes the bridge token, so an
        absent job can be reported as absent — the capability route has to
        conflate "no such job" with "wrong sig" to avoid being an existence
        oracle, and a caller holding the bridge token is owed neither.
        """
        token = self._bridge_token(params)
        job = params.get('job', [''])[0]
        if token is None:
            return None
        if not job or path_safety.unsafe_component(job):
            return self._json(400, {'error': 'bad job'})
        with segment_store.seg_lock:
            try:
                record = segment_store.load_record(job)
            except ValueError:
                return self._json(400, {'error': 'bad job'})
            except segment_store.SegmentRecordError:
                return self._json(500, {'error': 'segment storage failure'})
            if record is None:
                return self._json(404, {'error': 'no such job'})
            if record.get('token') != token:
                return self._json(
                    409, {'error': 'job owned by a different token'})
            sig = record.get('sig', '')
            if not isinstance(sig, str) or not sig or not sig.isascii():
                return self._json(409, {'error': 'job record cannot resume'})
        return self._json(200, {'ok': True, 'sig': sig})

    def _handle_segment_job(self, body):
        """POST /segment-job — mint (or re-fetch) the capability for an HLS job.

        Idempotent for the owning token: the relay is documented as resumable,
        so re-minting returns the same sig and a resume keeps working. A job
        already owned by a different token answers 409. The record lives beside
        the job's directory so both survive together.
        """
        token = body['token']
        job = body.get('job', '')
        if not job or path_safety.unsafe_component(job):
            return self._json(400, {'error': 'bad job'})
        with segment_store.seg_lock:
            try:
                record = segment_store.load_record(job)
                job_dir = path_safety.under(SEG_DIR, job)
                tmp = path_safety.under(SEG_DIR, f'.{job}.json.tmp')
                record_path = segment_store.record_path(job)
            except ValueError:
                return self._json(400, {'error': 'bad job'})
            except segment_store.SegmentRecordError:
                return self._json(
                    500, {'error': 'segment storage failure'})
            if record is not None:
                if record.get('token') != token:
                    return self._json(
                        409, {'error': 'job owned by a different token'})
                sig = record.get('sig', '')
                if not isinstance(sig, str) or not sig or not sig.isascii():
                    return self._json(409, {'error': 'job record cannot resume'})
                quota = segment_store.quota(record)
                if quota is not None:
                    # A resume is the right moment to reconcile: this counts
                    # the directory, refreshes the totals, and sweeps temps a
                    # crashed write left behind. It is O(files), which is why
                    # it lives here and not on the per-segment path -- a job
                    # is minted once per resume, not once per segment.
                    #
                    # It also heals the one drift the write path can leave: a
                    # crash between publishing a segment and recording it.
                    reconciled = segment_store.recount(job_dir)
                    if reconciled is not None and (
                            record.get('stored_count'),
                            record.get('stored_bytes')) != reconciled:
                        segment_store.write_usage(job, *reconciled)
                    return self._json(200, {'ok': True, 'sig': sig})

                quota_fields = (
                    'max_segment_index', 'max_segment_count', 'max_bytes')
                if any(field in record for field in quota_fields):
                    return self._json(409, {'error': 'job record cannot resume'})
                if any(value < 0 for value in (
                        MAX_SEGMENT_INDEX, MAX_SEGMENTS_PER_JOB,
                        MAX_SEGMENT_JOB_SIZE)):
                    return self._json(409, {'error': 'job record cannot resume'})

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
                    # This branch has already counted and measured the job to
                    # decide whether it fits current quotas, so seeding the
                    # totals here costs nothing and spares the first segment
                    # write a recount.
                    'stored_count': len(segment_files),
                    'stored_bytes': stored_bytes,
                }
                try:
                    tmp.write_text(json.dumps(record), encoding='utf-8')
                    atomic_file.replace_atomically(tmp, record_path)
                except OSError:
                    try:
                        tmp.unlink()
                    except OSError:
                        # The record write already failed and the 500 below is
                        # the answer; a leftover temp is overwritten by the
                        # next write to this job's name.
                        pass
                    return self._json(
                        500, {'error': 'segment storage failure'})
                return self._json(200, {'ok': True, 'sig': sig})
            # Counted, not assumed empty: a record can be deleted while its
            # directory survives, and seeding zero there would hand the job a
            # budget it has already spent. This is also where a temp left by a
            # crashed write is swept, which is off the per-segment path.
            seeded = segment_store.recount(job_dir)
            seeded_count, seeded_bytes = seeded if seeded is not None else (0, 0)
            record = segment_store.new_record(
                token, seeded_count, seeded_bytes)
            sig = record['sig']
            made_dir = not job_dir.exists()
            try:
                job_dir.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(record), encoding='utf-8')
                atomic_file.replace_atomically(tmp, record_path)  # publish
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
                    # Best effort: the 409 below is the answer either way, and
                    # the next mint on this name overwrites the temp.
                    pass
                if made_dir:
                    try:
                        job_dir.rmdir()
                    except OSError:
                        # Only this call's own directory is removed, and only
                        # while empty. One that is not empty belongs to
                        # whatever filled it.
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
        # isinstance BEFORE the membership test: `[] in SCREENSHOT_TYPES`
        # raises TypeError rather than answering False, and an exception here
        # killed the request thread, so the caller got a dropped connection
        # instead of the refusal this line already knew how to write.
        if not isinstance(fmt, str) or fmt not in SCREENSHOT_TYPES:
            return self._json(400, {'error': 'unsupported format'})
        if not upload_id:
            return self._json(400, {'error': 'missing id'})
        if not data_b64:
            return self._json(400, {'error': 'missing data'})
        # Sanitize path components
        for val in (token, upload_id, filename):
            if path_safety.unsafe_component(val):
                return self._json(400, {'error': 'invalid path component'})
        import base64
        try:
            raw = base64.b64decode(data_b64, validate=True)
        except Exception:
            return self._json(400, {'error': 'invalid base64'})
        try:
            dest_dir = path_safety.under(UPLOAD_DIR, token, upload_id)
            if filename:
                dest = path_safety.under(dest_dir, filename)
            else:
                ts = int(time.time() * 1000)
                dest = path_safety.under(dest_dir, f'{ts}.{fmt}')
        except ValueError:
            return self._json(400, {'error': 'invalid path component'})
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

    def _serve_named_upload(self, token, named):
        """Serve exactly the file a result named, not whatever is newest.

        `named` is the `path` POST /upload answered with and the result
        carries, token component included. Screenshot ids are reused — `_ss`
        is the default one — so an id identifies a directory rather than a
        capture, and the newest file in it belongs to whichever invocation
        finished last. Every component is checked the way each was checked
        on the way in, and the leading one has to be the caller's own token:
        one token's paths never name another's storage.
        """
        parts = named.split('/')
        if any(path_safety.unsafe_component(part) for part in parts):
            return self._json(400, {'error': 'invalid path component'})
        if parts[0] != token:
            return self._json(404, {'error': 'no screenshot'})
        target = UPLOAD_DIR.joinpath(*parts)
        fmt = target.suffix.lstrip('.').lower()
        if fmt not in SCREENSHOT_TYPES or not target.is_file():
            return self._json(404, {'error': 'no screenshot'})
        return self._serve_file(target, fmt)

    def _handle_get_screenshot(self, params):
        """GET /screenshot?token=X&id=Y — serve latest screenshot for that id. Or token only for latest across all ids. With `path=<upload path>`, serve that exact file instead."""
        token = self._bridge_token(params)
        upload_id = params.get('id', [''])[0]
        named = params.get('path', [''])[0]
        if token is None:
            return None
        if named:
            return self._serve_named_upload(token, named)
        if upload_id and path_safety.unsafe_component(upload_id):
            return self._json(400, {'error': 'invalid path component'})
        try:
            token_dir = path_safety.under(UPLOAD_DIR, token)
        except ValueError:
            return self._json(400, {'error': 'invalid path component'})
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
                if f.suffix.lower().lstrip('.') in SCREENSHOT_TYPES:
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
        if any(path_safety.unsafe_component(part) for part in rel.split('/')):
            return self._json(400, {'error': 'bad path'})
        # Same containment as every other root, through the same helper:
        # this route grew its own resolve-and-contain before there was one,
        # and two spellings of one rule is one more than anybody will keep
        # in step.
        try:
            target = path_safety.under(DASHBOARD_DIR, *rel.split('/'))
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
        # This page holds the bridge token and drives the browser, so being
        # framed by another origin puts those controls under someone else's
        # overlay. frame-ancestors is the one that governs; X-Frame-Options
        # is carried beside it for anything that never learned CSP. Sent on
        # every dashboard response, assets included: a framed script or
        # stylesheet is a smaller prize than the document, but the header
        # costs nothing and the exception would be the thing to get wrong.
        self.send_header(
            'Content-Security-Policy', "frame-ancestors 'none'")
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(data)
        return None

    def _serve_file(self, path, fmt):
        """Serve a binary file, streamed so large files aren't fully buffered in RAM."""
        mime_map = {**SCREENSHOT_TYPES,
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
        target=command_queue.gc_loop, args=(CMD_DIR, CMD_TTL),
        name='command-gc', daemon=True).start()
    httpd = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    bridge_port = httpd.server_address[1]
    try:
        import mcp_server
        mcp_server.start_in_thread(f'http://127.0.0.1:{bridge_port}')
    except Exception as e:
        # ASCII only, and it names the install: without the optional
        # dependencies the bridge otherwise starts normally and /mcp simply
        # is not there, which reads as a client problem rather than a
        # missing extra.
        print('[Daedalus] MCP bootstrap failed, so /mcp is not served - '
              'install its dependencies with: pip install ".[mcp]" - '
              f'{log_safe(e)}', flush=True)
    # ASCII only, deliberately: this line is the bridge's sole readiness
    # signal, and a console whose code page cannot encode a decorative
    # character raises rather than degrading, so the announcement would be
    # lost and every caller waiting on it would time out against a bridge
    # whose port is already open. cp437, still a Windows console default,
    # has no em dash.
    print(f'[Daedalus] Listening on 127.0.0.1:{bridge_port} - '
          f'base={log_safe(BASE)}', flush=True)
    httpd.serve_forever()
