"""HTTP request transport for the bridge handler.

Request target and query parsing, credential resolution, the body-length
refusal rules, the refusal drains and response writing live here as
`RequestMixin`, the base `server.py`'s Handler derives from. Route modules
stay free of the socket: each returns an answer — a `(status, payload)`
pair, a `FileAnswer` or a `BytesAnswer` — and the handler writes it with
`answer`.

Besides `result_store` and `segment_store`, this is the only bridge module
that reads `daedalus_bridge.config`, because it is transport rather than a
route: a route takes its directories and limits as parameters. That is also
why the answer types are defined in `route_answer` and only re-exported here
— a route importing them from this one would inherit that requirement.
"""
import ctypes, ctypes.util
import hmac, json, shutil
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from daedalus_cli import SEGMENT_SIG_HEADER, ambiguous_request_carrier
from daedalus_cli.transport import token as _configured_token
from daedalus_bridge import path_safety
from daedalus_bridge.route_answer import BytesAnswer, FileAnswer
from daedalus_bridge.config import (
    MAX_BODY_SIZE, MAX_JSON_DEPTH, MAX_UNAUTHENTICATED_BODY,
)
from daedalus_bridge.env_config import REFUSED_BODY_DRAIN
from daedalus_bridge.log_safe import log_safe


# ─── glibc malloc tuning ───
# ThreadingMixIn spawns a thread per request; glibc otherwise creates up to
# 8*nproc memory arenas and never returns their freed memory to the OS, which
# inflates RSS to a high-water-mark (~900MB observed) that never recedes. Cap
# the arenas and actively trim freed heap after large request bodies/files.
TRIM_THRESHOLD = 256 * 1024  # only trim after handling payloads larger than this
_TUNING_NOTE = None
try:
    _LIBC = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
    _LIBC.mallopt(-8, 2)  # M_ARENA_MAX = -8: cap concurrent arenas at 2
except Exception as _e:  # non-glibc / unavailable
    _LIBC = None
    _TUNING_NOTE = f'[Daedalus] malloc tuning unavailable: {log_safe(_e)}'


def malloc_tuning_note():
    """The tuning diagnostic to print at startup, or None where it worked.

    Reported by the entry point rather than at import, so importing this
    module writes nothing to stdout.
    """
    return _TUNING_NOTE


def malloc_trim():
    """Return freed heap back to the OS (glibc). No-op where unavailable."""
    if _LIBC is not None:
        try:
            _LIBC.malloc_trim(0)
        except Exception:
            # Returning freed heap is an optimisation with no fallback to
            # attempt. A libc without the symbol, or one that refuses the
            # call, leaves the heap where it is and the bridge keeps serving.
            pass


def json_nests_deeper_than(raw, limit):
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


UNDECLARED_BODY_DRAIN_SECONDS = 0.25


class JSONObject(dict):
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
        if isinstance(other, JSONObject):
            return (super().__eq__(other)
                    and self.duplicate_carrier == other.duplicate_carrier)
        return super().__eq__(other)


class RequestMixin(BaseHTTPRequestHandler):
    """Everything a Handler does with the socket, off the route bodies.

    It derives from BaseHTTPRequestHandler because every method below reads
    that class's request state and calls its response writers; saying so in
    the class statement is what lets a type checker verify the requirement
    rather than take it on trust. It overrides nothing the framework
    defines, so a Handler deriving from this reaches the same methods in the
    same order as one that mixed it in.
    """

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
            self.connection.settimeout(UNDECLARED_BODY_DRAIN_SECONDS)
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
        if json_nests_deeper_than(raw, MAX_JSON_DEPTH):
            self._json(400, {'error': 'JSON body too deeply nested'})
            return None
        try:
            body = json.loads(raw, object_pairs_hook=JSONObject)
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
        if not isinstance(body, JSONObject):
            self._json(400, {'error': 'JSON body must be an object'})
            return None
        if body.duplicate_carrier is not None:
            self._json(
                400, {'error': f'duplicate {body.duplicate_carrier}'})
            return None
        return body

    def _json(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _cors(self):
        pass  # no CORS headers here by design -- see "Deployment" in README.md

    def answer(self, result):
        """Write a route answer: a status pair, FileAnswer or BytesAnswer.

        A route returns a value and never touches the socket, so None is a
        route that forgot to answer — a programming error, refused here
        rather than served as an empty 200.
        """
        # NamedTuples are tuples: these precede the tuple branch.
        if isinstance(result, FileAnswer):
            return self.send_file(result.path, result.mime)
        if isinstance(result, BytesAnswer):
            self.send_response(200)
            self.send_header('Content-Type', result.mime)
            self.send_header('Content-Length', str(len(result.data)))
            self.send_header('Cache-Control', 'no-cache')
            for name, value in result.headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(result.data)
            return None
        if isinstance(result, tuple):
            return self._json(*result)
        raise TypeError(
            'route answer must be a (status, payload) pair, a FileAnswer '
            f'or a BytesAnswer, not {type(result).__name__}')

    def send_file(self, path, mime):
        """Serve a binary file, streamed so large files aren't fully
        buffered in RAM."""
        size = path.stat().st_size
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        with open(path, 'rb') as fh:
            shutil.copyfileobj(fh, self.wfile, 256 * 1024)
        if size > TRIM_THRESHOLD:
            malloc_trim()
