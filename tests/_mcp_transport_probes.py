"""Scripted stand-ins for the MCP transport suite's bridge client."""


class ResponseProbe:
    def __init__(self, body, status_error=None):
        self.body = body
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error is not None:
            raise self.status_error

    def json(self):
        return self.body


class ClientProbe:
    def __init__(self, replies=(), post_response=None,
                 delete_response=None):
        self.replies = list(replies)
        self.calls = []
        self.post_response = post_response or ResponseProbe({'ok': True})
        self.delete_response = (
            delete_response or ResponseProbe({'deleted': True}))

    async def get(self, path, **kwargs):
        unmodeled = set(kwargs) - {'params', 'headers', 'timeout'}
        if unmodeled:
            raise AssertionError(
                'ClientProbe.get does not model: ' + ', '.join(
                    sorted(unmodeled)))
        self.calls.append(('get', path, kwargs))
        if not self.replies:
            raise RuntimeError('unexpected result poll')
        reply = self.replies.pop(0)
        # A scripted exception is what the transport raised on that GET.
        if isinstance(reply, BaseException):
            raise reply
        return ResponseProbe(reply)

    async def post(self, path, **kwargs):
        self.calls.append(('post', path, kwargs))
        return self.post_response

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.delete_response


def clock_script(*values):
    """Stands in for the poll clock: values replay in order, last repeats."""
    remaining = list(values)

    def read():
        if len(remaining) > 1:
            return remaining.pop(0)
        return remaining[0]

    return read
