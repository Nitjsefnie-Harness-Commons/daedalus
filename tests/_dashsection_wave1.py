"""The scenario scaffolding the wave 1 section suites share.

`tests/_dashsection.py` owns the shell and the strict transport; this owns
what the three suites repeat. A section suite's own file is its cases, and
these are the strings they are assembled from.
"""
import json
import re

TOKEN = 'tok-abcdefghijklmnop'

SEED = "localStorage.setItem('daedalus-token', '" + TOKEN + "');\n"

# `jsonResponse`, `new El`, `container`, `drive`, `bus`, `settle` and the
# report keys all come from `_dashsection.SHELL`; what follows adds the
# readers the section suites ask for, and the toasts among them. The
# toasts are searched in the tree rather
# than resolved by id because `_dashnode.DOM`'s `getElementById` answers
# null for every id, so `toast()` mints a host per call and appends to
# `document.body`.
PRELUDE = r"""
const container = new El('div');
const toasts = () => document.body.all().filter(
  (el) => el.className.indexOf('chip ') === 0)
  .map((el) => ({ type: el.className.slice(5), text: el.textContent }));
const button = (text, root) => (root || container).all().find(
  (el) => el.tag === 'button' && el.textContent === text) || null;
const rowTexts = (root) => root.all().filter((el) => el.tag === 'tr')
  .map((tr) => tr.children.map((td) => td.textContent));
const headers = (root) => {
  const head = root.all().find((el) => el.tag === 'thead');
  return head ? head.children[0].children.map((th) => th.textContent) : [];
};
const hasClass = (el, name) => el.className.indexOf(name) >= 0;
// `El` dispatches clicks and nothing else, so a keydown is driven by
// calling the listener the double stored. The boundary that leaves: the
// listener is handed `{ key }` and no `Event`, so one reading
// `e.currentTarget` would pass here and fail in a browser.
const pressEnter = (el) => {
  for (const fn of el.listeners.keydown || []) fn({ key: 'Enter' });
};
"""

MOUNT = "mount(container, bus);\n"
SETTLE = ("await bounded(settle(), 'the mount settled',\n"
          "  _dashnodeStepTimeoutMs);\n")

# The one plan a command needs. The delivery id is a constant because the
# assertions are about the fields the section sent.
COMMAND = "drive.route('/command', { did: 'delivery-1' });\n"


_ANSWER = re.compile(r"answer\('([^']*)', (.*)\);$")


def results(*statements):
    """Plan the `/result` leg from a scenario's `answer(...)` lines.

    A scenario writes what each command TYPE answers with in the same
    shape it writes its routes, and this is the one place that reads
    those lines into the transport's `byType` plan. A line that is not an
    `answer('type', {...});` is a scenario bug, and it is refused here --
    at plan time, in Python, where it is a test failure rather than a
    throw a section catches.

    The refusal for a command type nobody NAMED is the transport's, not
    this file's: it records the request and throws, exactly as it does for
    a target no scenario planned.
    """
    pairs = []
    for line in ''.join(statements).splitlines():
        text = line.strip()
        if not text:
            continue
        found = _ANSWER.fullmatch(text)
        if not found:
            raise ValueError('not an answer line: ' + text)
        pairs.append(found.groups())
    table = ', '.join(json.dumps(name) + ': ' + patch for name, patch in pairs)
    return ("drive.route('/result?tab=extension',\n"
            "  { byType: { " + table + ' } });\n')


def open_section(name):
    """Import one declared section and reach the point the call starts.

    `load` is the shell's own importer, so the two import checkpoints come
    from it rather than from the scenario.
    """
    return ("const { mount } = await bounded(load('" + name + "'),\n"
            "  'section import', _dashnodeStepTimeoutMs);\n"
            "phase('dashboard call started');\n")


def legs(report):
    """The command, the poll and the consume legs a report recorded.

    Split by target rather than by position, so a give-up tail of twenty
    polls is never counted as commands and the consume leg is never
    counted as a poll.
    """
    rows = report['requests']
    return {
        'command': [r for r in rows if r['target'].endswith('/command')],
        'poll': [r for r in rows if r['target'].startswith('/result')
                 and 'consume' not in r['target']],
        'consume': [r for r in rows if 'consume' in r['target']],
    }


# `api.js` sleeps `POLL_CADENCE_MS` between result attempts and its loop
# tests `hostNow() + SPENT < deadline` at `api.js:143`, so the number of
# legs a give-up spends is the budget divided by the cadence MINUS whatever
# real time the child itself burned getting there. A run fast enough to
# stay under one cadence of host time gets the whole number; a slow or
# loaded one gives up a poll or two early, and an exact count then reddens
# a suite for the runner rather than for the code.
#
# The budget itself is pinned exactly and host-independently, by the
# give-up message: `api.js:164` formats it from the `timeout` the loop was
# handed, and nothing a loaded machine does reaches that string. So the leg
# count is the CORROBORATION and the band is its honest shape.
POLL_CADENCE_MS = 250

# How far a count may fall short of the whole number. The four budgets a
# dashboard section can send are 5000, 15000, 20000 and 30000 -- twenty,
# sixty, eighty and a hundred and twenty legs -- and the nearest pair is
# twenty legs apart, so nine is the widest band that keeps every pair
# disjoint. It is not a comfort margin: it is the most the numbers allow.
#
# Its ceiling is measured, not assumed. A runner that burns `h` ms of host
# time per result leg costs `legs * h` over a give-up, so the binding case
# is the largest budget and the arithmetic bound is `9 * 250 / 120` =
# 18.75 ms per leg. Planting a busy-wait of that size in the transport's
# fetch puts the real edge a little above the arithmetic: three samples
# each at 16, 18 and 20 ms per leg are green in both suites, and the first
# red is one sample in three at 22. Past the ceiling the CORROBORATING
# count reds and the case's actual claim -- the message, which `api.js:164`
# formats from the budget the loop was handed -- is untouched. Assert the
# message first.
POLL_SLACK = 9


def poll_band(report, budget, key='polls'):
    """The band `api.js` spends `budget` worth of result legs in.

    `budget` is the number the case's own timeout message already asserts
    exactly, so this band is derived from the same fact rather than from
    whatever count a fast run happened to produce.
    """
    whole = budget // POLL_CADENCE_MS
    return whole - POLL_SLACK <= report[key] <= whole


def commands(report):
    """The recorded command bodies, in the order the section sent them."""
    return [r['body'] for r in legs(report)['command']]


def types(report):
    """The type of each recorded command, in order."""
    return [body['type'] for body in commands(report)]
