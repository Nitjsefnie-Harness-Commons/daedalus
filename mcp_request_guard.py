"""Pre-body authentication, size refusal, and bounded refusal draining."""
import hmac
from contextvars import ContextVar

from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from daedalus_cli import ambiguous_request_carrier
from daedalus_cli.transport import token as _configured_token
from env_config import REFUSED_BODY_DRAIN


__all__ = ('drain_refused_body', 'early_refusal', 'request_token')


request_token: ContextVar[str] = ContextVar(
    'daedalus_token', default='')


def early_refusal(request, max_body_size):
    """Return a header-decided refusal before any body is requested."""
    duplicate = ambiguous_request_carrier(
        name for name, _value in request.scope.get('headers', ()))
    if duplicate == 'token':
        return JSONResponse(
            {'error': 'duplicate Authorization header'}, status_code=400)
    if duplicate == 'mcp-session-id':
        return JSONResponse(
            {'error': 'duplicate Mcp-Session-Id header'}, status_code=400)
    if duplicate == 'host':
        return JSONResponse(
            {'error': 'duplicate Host header'}, status_code=400)
    if duplicate == 'origin':
        return JSONResponse(
            {'error': 'duplicate Origin header'}, status_code=400)

    # Credentials are decided BEFORE the body is touched. Parsing it first
    # made an unauthenticated caller able to have an arbitrarily large
    # request materialized on its way to a 401, and handed it body-level
    # diagnostics about a request it was never allowed to make.
    authorizations = request.headers.getlist('authorization')
    auth = authorizations[0] if authorizations else ''
    if not auth.lower().startswith('bearer '):
        return JSONResponse(
            {'error': 'missing Bearer token'}, status_code=401)
    tok = auth[7:].strip()
    if not tok or '/' in tok or '.' in tok:
        return JSONResponse({'error': 'bad token'}, status_code=401)
    try:
        authorized = _configured_token()
    except SystemExit:
        authorized = ''
    if (not isinstance(authorized, str) or not authorized
            or not hmac.compare_digest(
                tok.encode('utf-8', 'surrogatepass'),
                authorized.encode('utf-8', 'surrogatepass'))):
        return JSONResponse({'error': 'unauthorized'}, status_code=401)
    request_token.set(tok)

    if request.method == 'POST':
        declared = request.headers.get('content-length')
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                return JSONResponse(
                    {'error': 'invalid Content-Length'}, status_code=400)
            if length < 0:
                return JSONResponse(
                    {'error': 'invalid Content-Length'}, status_code=400)
            if length > max_body_size:
                return JSONResponse(
                    {'error': 'request body too large'}, status_code=413)
    return None


async def drain_refused_body(request):
    """Stream-discard at most the shared refusal-drain bound."""
    remaining = REFUSED_BODY_DRAIN
    try:
        async for chunk in request.stream():
            remaining -= len(chunk)
            if remaining <= 0:
                break
    except ClientDisconnect:
        pass
