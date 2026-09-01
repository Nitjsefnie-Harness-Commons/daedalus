#!/usr/bin/env python3
"""Daedalus debug server — SSE command bridge + tab registry."""
import json, threading, time
from http.server import HTTPServer
from socketserver import TCPServer, ThreadingMixIn

from daedalus_cli.output import configure_stdio
from daedalus_bridge import atomic_file
from daedalus_bridge import command_queue, parent_watch
from daedalus_bridge.log_safe import log_safe
from daedalus_bridge import mcp_bootstrap
from daedalus_bridge import result_routes
from daedalus_bridge import segment_store
from daedalus_bridge import static_routes
from daedalus_bridge import stream_route
from daedalus_bridge import stream_service
from daedalus_bridge import tab_registry
from daedalus_bridge import upload_routes
from daedalus_bridge.config import (
    BASE, CMD_DIR, CMD_TTL, DASHBOARD_DIR,
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


_SEGMENT_DECIMAL_MAX_DIGITS = 20

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
            return self._handle_segment_job_lookup(params)

        if parsed.path == '/segment-status':
            return self._handle_segment_status(params)

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
            return self._handle_segment_job(body)

        elif self.path == '/result':
            return self.answer(result_routes.accept_result(
                RES_DIR, CMD_DIR, token, body))

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
                         if record is not None
                         and not segment_store.needs_recount(job) else None)
                if usage is None:
                    # A job minted before totals were kept, or one whose
                    # last write_usage never confirmed landing. Either way
                    # the record can't be trusted, and this is the only
                    # scan on this path, so nothing after it pays for it
                    # again.
                    usage = segment_store.recount(seg_dir)
                    if usage is None:
                        return self._json(
                            500, {'error': 'segment storage failure'})
                    # Persisted now, whether or not this request's own
                    # write goes on to be accepted: a rejected write never
                    # reaches the write_usage call below, so without this
                    # every later rejection on this job would pay for the
                    # same full scan again and never clear the mark.
                    segment_store.write_usage(job, *usage)
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
                # Marked before the segment is published, not after: once
                # the .ts file lands, this job's true storage can already
                # disagree with its record, and a crash between here and
                # the write_usage call below must not be the one window
                # where that disagreement leaves no trace at all. Refusing
                # the write outright when even this cannot be established
                # is the alternative #203 asks for to a mark that fails
                # silently and lets the write through unaccounted.
                if not segment_store.mark_dirty(job):
                    return self._json(
                        500, {'error': 'segment storage failure'})
                try:
                    atomic_file.write_bytes_retrying(tmp, raw)
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
                        segment_store.mark_dirty(job)
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
                    atomic_file.write_text_retrying(tmp, json.dumps(record))
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
                atomic_file.write_text_retrying(tmp, json.dumps(record))
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

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002 — match base signature
        del format, args  # silence per-request access logging


if __name__ == '__main__':
    parent_watch.start()
    for directory in (CMD_DIR, RES_DIR, UPLOAD_DIR, SEG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
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
