#!/usr/bin/env python3
"""Daedalus debug server — SSE command bridge + tab registry."""
import sys
import threading, time
from http.server import HTTPServer
from socketserver import TCPServer, ThreadingMixIn

from daedalus_cli.output import configure_stdio
from daedalus_bridge import command_queue, parent_watch
from daedalus_bridge import data_root_lock
from daedalus_bridge.log_safe import log_safe
from daedalus_bridge import mcp_bootstrap
from daedalus_bridge import result_routes
from daedalus_bridge import segment_jobs
from daedalus_bridge import segment_routes
from daedalus_bridge import static_routes
from daedalus_bridge import stream_route
from daedalus_bridge import stream_service
from daedalus_bridge import tab_registry
from daedalus_bridge import upload_routes
from daedalus_bridge.config import (
    BASE, CMD_DIR, CMD_TTL, DASHBOARD_DIR, MAX_DELIVERY_RESULTS,
    MAX_REQUEST_WORKERS, MAX_SEGMENT_INDEX, MAX_SEGMENT_JOB_SIZE,
    MAX_SEGMENTS_PER_JOB, PORT, REQUEST_TIMEOUT,
    RES_DIR, SEG_DIR, STREAM_KEEPALIVE, STREAM_MAX_AGE,
    UPLOAD_DIR,
)
from daedalus_bridge import http_transport
from daedalus_bridge.http_transport import RequestMixin
from daedalus_bridge import path_safety

# The bridge logs ids and page-supplied text it did not choose, to a console
# whose encoding it did not choose either: under a C locale a result id
# carrying an accent killed the handler thread mid-request and the client saw
# the connection close. This used to happen by accident — importing the CLI
# ran it as an import side effect — so it is called here, where a reader can
# see the bridge depends on it.
configure_stdio()

# ─── Health / observability ───


_server_start_ts = time.time()


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


class Handler(RequestMixin):
    # socketserver applies this to the connection, so it bounds the request
    # line, the headers and the body alike. It is per socket operation: a
    # transfer that keeps making progress renews it and is never cut short.
    timeout = REQUEST_TIMEOUT

    def do_GET(self):
        parsed = self._request_target()
        if parsed is None:
            return None
        params = self._parse_query(parsed.query)
        if params is None:
            return None

        if parsed.path == '/result':
            token = self._bridge_token(params)
            if token is None:
                return None
            return self.answer(
                result_routes.fetch_result(RES_DIR, token, params))

        if parsed.path == '/screenshot':
            token = self._bridge_token(params)
            if token is None:
                return None
            named = params.get('path', [''])[0]
            if named:
                return self.answer(
                    upload_routes.named_upload(UPLOAD_DIR, token, named))
            return self.answer(
                upload_routes.latest_screenshot(UPLOAD_DIR, token, params))

        if parsed.path == '/upload':
            token = self._bridge_token(params)
            if token is None:
                return None
            return self.answer(
                upload_routes.list_uploads(UPLOAD_DIR, token, params))

        if parsed.path == '/tabs':
            token = self._bridge_token(params)
            if token is None:
                return None
            return self.answer(tab_registry.list_tabs(token))

        if parsed.path == '/segment-job':
            token = self._bridge_token(params)
            if token is None:
                return None
            return self.answer(
                segment_routes.lookup_job(SEG_DIR, token, params))

        if parsed.path == '/segment-status':
            sig = self._segment_capability(params)
            if sig is None:
                return None
            return self.answer(
                segment_routes.segment_status(SEG_DIR, params, sig))

        if parsed.path == '/health':
            return self._handle_health()

        if parsed.path == '/dashboard' or parsed.path.startswith('/dashboard/'):
            return self.answer(static_routes.dashboard_asset(
                DASHBOARD_DIR, parsed.path))

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
        targets = stream_route.resolve_targets(CMD_DIR, token, tab)
        if targets is None:
            return self._json(400, {'error': 'invalid path component'})
        # Kill old stream for the same tab, register this one. Every stream is
        # registered, tabless ones included: one that is not is a worker and a
        # command consumer that /health cannot see.
        stream_id, killed_event = stream_service.register(token, tab)
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
        try:
            stream_route.serve_stream(
                self.wfile, cmd_dir=CMD_DIR, token=token, tab=tab,
                targets=targets, killed_event=killed_event,
                command_ttl=CMD_TTL, keepalive=STREAM_KEEPALIVE,
                max_age=STREAM_MAX_AGE,
                client_label=self.client_address[0])
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
            if clen > http_transport.TRIM_THRESHOLD:
                http_transport.malloc_trim()

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
            params = self._parse_query(parsed.query)
            if params is None:
                # A refusal is written; the body is drained rather than
                # read, because closing on unread bytes sends RST and the
                # answer would be discarded with them.
                return self._drain_refused_body(clen)
            sig = self._segment_capability(params)
            if sig is None:
                return self._drain_refused_body(clen)
            admitted = segment_routes.admit_segment(SEG_DIR, params, sig)
            if not isinstance(admitted, segment_routes.Admission):
                self.answer(admitted)
                return self._drain_refused_body(clen)
            raw = self._read_body(clen)
            if raw is None:
                return None
            return self.answer(segment_routes.store_segment(raw, admitted))
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
            return self.answer(tab_registry.refresh(CMD_DIR, token, body))

        elif self.path == '/sync-tabs':
            return self.answer(tab_registry.replace(CMD_DIR, token, body))

        elif self.path == '/unregister':
            return self.answer(tab_registry.remove(CMD_DIR, token, body))

        elif self.path == '/poll':
            return self.answer(
                stream_service.poll_legacy(CMD_DIR, token))

        elif self.path == '/upload':
            return self.answer(
                upload_routes.store_upload(UPLOAD_DIR, body))

        elif self.path == '/segment-job':
            return self.answer(segment_jobs.mint_job(
                SEG_DIR, token, body,
                segment_jobs.JobQuotas(
                    MAX_SEGMENT_INDEX, MAX_SEGMENTS_PER_JOB,
                    MAX_SEGMENT_JOB_SIZE)))

        elif self.path == '/result':
            return self.answer(result_routes.accept_result(
                RES_DIR, CMD_DIR, token, body, MAX_DELIVERY_RESULTS))

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
            return self.answer(
                upload_routes.delete_upload(UPLOAD_DIR, body))
        return self._json(404, {'error': 'not found'})

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

    def _handle_health(self):
        """GET /health — bridge liveness for detecting a silently-dead stream."""
        now = time.time()
        live_streams, stream_tabs = stream_service.snapshot()
        tokens, tabs = tab_registry.counts()
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
            'mcp': mcp_bootstrap.state(),
        })

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002 — match base signature
        del format, args  # silence per-request access logging


if __name__ == '__main__':
    _tuning_note = http_transport.malloc_tuning_note()
    if _tuning_note:
        print(_tuning_note, flush=True)
    parent_watch.start()
    for directory in (CMD_DIR, RES_DIR, UPLOAD_DIR, SEG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    try:
        bridge_lock = data_root_lock.acquire(BASE)
    except data_root_lock.DataRootInUse:
        print(f'[Daedalus] data root already in use: {log_safe(BASE)}',
              flush=True)
        sys.exit(1)
    threading.Thread(
        target=command_queue.gc_loop, args=(CMD_DIR, CMD_TTL),
        name='command-gc', daemon=True).start()
    httpd = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    bridge_port = httpd.server_address[1]
    mcp_bootstrap.start(bridge_port)
    # ASCII only, deliberately: this line is the bridge's sole readiness
    # signal, and a console whose code page cannot encode a decorative
    # character raises rather than degrading, so the announcement would be
    # lost and every caller waiting on it would time out against a bridge
    # whose port is already open. cp437, still a Windows console default,
    # has no em dash.
    print(f'[Daedalus] Listening on 127.0.0.1:{bridge_port} - '
          f'base={log_safe(BASE)}', flush=True)
    httpd.serve_forever()
