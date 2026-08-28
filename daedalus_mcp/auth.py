"""MCP authentication middleware and request-carrier validation."""
import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from daedalus_cli import ambiguous_request_carrier
from daedalus_mcp import request_guard


class BearerAuth(BaseHTTPMiddleware):
    def __init__(self, app, max_body_size):
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request, call_next):
        refusal = request_guard.early_refusal(
            request, self.max_body_size)
        if refusal is not None:
            await request_guard.drain_refused_body(request)
            return refusal

        if request.method == 'POST':
            raw = await request.body()
            # A body sent without a declared length is bounded here rather than
            # by the check above; it has still been read, which is why the
            # declared case is refused before reading at all.
            if len(raw) > self.max_body_size:
                return JSONResponse(
                    {'error': 'request body too large'}, status_code=413)
            duplicate = ambiguous_json_carrier(raw)
            if duplicate is not None:
                return JSONResponse(
                    {'error': f'duplicate {duplicate}'}, status_code=400)

        return await call_next(request)


class CarrierJSONObject(dict):
    """A parsed JSON object that keeps the pairs it was built from.

    A duplicate key collapses into one dict entry, so the pair list is the
    only record that it was sent twice — which is exactly what the repeated
    `job` argument check reads. Equality includes it for that reason:
    inherited dict equality would report two bodies as the same request when
    only one of them repeated a key. Comparison against a plain dict is
    unchanged.

    `__ne__` is overridden alongside `__eq__` rather than left to the
    default: `dict` defines its own `__ne__`, so simply overriding `__eq__`
    here does not make `!=` consistent with it — without this, `!=` would
    silently fall back to plain dict inequality and miss the very duplicate
    key `==` exists to catch. `__eq__` can itself return `NotImplemented`
    (comparison against a non-dict falls through to `dict.__eq__`, which
    returns it rather than `False`), and `NotImplemented` is truthy, so
    `__ne__` has to check for it explicitly rather than just negate --
    negating it directly would silently turn every such comparison false.
    """

    def __init__(self, pairs):
        super().__init__(pairs)
        self.pairs = pairs

    def __eq__(self, other):
        if isinstance(other, CarrierJSONObject):
            return super().__eq__(other) and self.pairs == other.pairs
        return super().__eq__(other)

    def __ne__(self, other):
        equal = self.__eq__(other)
        if equal is NotImplemented:
            return equal
        return not equal


def job_carrier_names(request_body):
    names = []
    if not isinstance(request_body, CarrierJSONObject):
        return names
    for key, params in request_body.pairs:
        if (key != 'params'
                or not isinstance(params, CarrierJSONObject)
                or params.get('name') not in ('segment_job', 'segment_status')):
            continue
        for param_key, arguments in params.pairs:
            if (param_key == 'arguments'
                    and isinstance(arguments, CarrierJSONObject)):
                names.extend(key for key, _value in arguments.pairs
                             if key == 'job')
    return names


def ambiguous_json_carrier(raw):
    try:
        body = json.loads(raw, object_pairs_hook=CarrierJSONObject)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    requests = body if isinstance(body, list) else (body,)
    for request_body in requests:
        duplicate = ambiguous_request_carrier(
            job_carrier_names(request_body))
        if duplicate is not None:
            return duplicate
    return None
