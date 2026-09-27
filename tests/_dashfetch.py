"""The refusal door the dashboard suites' own `globalThis.fetch` fakes share.

`tests/_dashshell.py` and `tests/_dashsection_transport.py` plan every
route a scenario declares and refuse the rest. The two suites that mount a
section through `_dashnode.DOM` and model their routes by shape were the
gap: their fakes recorded what they saw and then answered anything they
did not model with a success, so a request the module invented was served
and no scenario failed (#1083). `DOOR` is what those fakes close, and it
is the one thing they share -- each fake keeps the routes it models,
because a shape match is not a plan.

A refusal is a record plus a 599 rather than a throw, because
`uploads.js` wraps its own fetch in a catch at `load`, `download` and
`preview` and `sse.js` swallows a throw into an ordinary stream error: a
thrown refusal reaches the suite as a symptom somewhere else. The record
is the half a test reads either way, so `UNPLANNED` is what every harness
splicing this in reports and `unplanned == []` claims that nothing was
invented rather than naming the routes somebody remembered to enumerate.
"""

__all__ = ['DOOR']


DOOR = r"""
const UNPLANNED = [];

// 599 rather than a throw: see the module docstring. `api.js` reads
// `statusText` only on the objectUrl path and `error` off the body on
// the rest, so both spellings name the refusal to the caller.
function refuse(target) {
  UNPLANNED.push(String(target));
  return {
    ok: false, status: 599, statusText: 'unplanned request',
    headers: { get: () => 'application/json' },
    json: async () => ({ error: 'unplanned request' }),
    text: async () => 'unplanned request',
    blob: async () => ({}),
  };
}
"""
