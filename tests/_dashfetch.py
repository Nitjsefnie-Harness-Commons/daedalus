"""The refusal door the dashboard suites' own `globalThis.fetch` fakes share.

`tests/_dashshell.py` and `tests/_dashsection_transport.py` plan every
route a scenario declares and refuse the rest. The two suites that mount a
section through `_dashnode.DOM` and model their routes by shape were the
gap: their fakes recorded what they saw and then answered anything they
did not model with a success, so a request the module invented was served
and no scenario failed (#1083). `DOOR` is what those fakes close, and it
is the one thing they share -- each fake keeps the routes it models,
because a shape match is not a plan.

A refusal is a record plus a 599 rather than a throw, and the reason is
narrower than it first looks: a 599 is a RESPONSE, so the module's own
error path runs and the child reaches its report. That is what puts the
record in front of the assertion. A throw would do the same at the three
sites in `uploads.js` that catch their own fetch -- both spellings reach
the module as a failure there -- so the choice is made for the sites that
do NOT catch, and the status is what keeps the child alive to write
`UNPLANNED` at all.

The 599 is re-spelled here rather than imported, so `_dashshell.py`'s
`UNPLANNED_STATUS` can change without this one following.
`UNPLANNED` is what every harness splicing this in reports, so
`unplanned == []` claims that nothing was invented rather than naming the
routes somebody remembered to enumerate.
"""

__all__ = ['DOOR']


DOOR = r"""
const UNPLANNED = [];

// `api.js` reads `statusText` only on the objectUrl path and `error` off
// the body on the rest, so both spellings name the refusal to the caller.
// The header bag is keyed: a module reaching for a name this transport
// does not model fails by name rather than reading `application/json`
// off it, which is the response-side half of "fail on what you do not
// model" and the half `tests/_dashshell.py` follows.
function refuse(target) {
  UNPLANNED.push(String(target));
  return {
    ok: false, status: 599, statusText: 'unplanned request',
    headers: { get: (name) => {
      if (String(name).toLowerCase() !== 'content-type') {
        throw new Error('response header not modelled: ' + String(name));
      }
      return 'application/json';
    } },
    json: async () => ({ error: 'unplanned request' }),
    text: async () => 'unplanned request',
    blob: async () => ({}),
  };
}
"""
