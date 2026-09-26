"""The scenario scaffolding the wave 1 section suites share.

`tests/_dashsection.py` owns the shell and the strict transport; this owns
what the three suites repeat. A section suite's own file is its cases, and
these are the strings they are assembled from.

The transport answers one `/result` plan for a whole scenario, so a
scenario that needs a different result for a different command TYPE
carries the table itself: `answer(type, patch)` declares what each type
answers, the wrapper reads the type off the `/command` body the transport
recorded, and a type the scenario never declared is refused by name. The
patch is applied to the envelope the transport anchored on the command, so
the delivery id and the generation the loop matched on are still the real
ones -- what a scenario declares is the RESULT, not the envelope.
"""
TOKEN = 'tok-abcdefghijklmnop'

SEED = "localStorage.setItem('daedalus-token', '" + TOKEN + "');\n"

# `jsonResponse`, `new El`, `container`, `drive`, `bus`, `settle` and the
# report keys all come from `_dashsection.SHELL`; this adds the two things
# every section case asks for. The toasts are searched in the tree rather
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
// `El` dispatches clicks and nothing else, so a keydown reaches the shipped
// listener the same way the double reaches a click handler.
const pressEnter = (el) => {
  for (const fn of el.listeners.keydown || []) fn({ key: 'Enter' });
};
"""

# `answer` is declared per scenario, so an undeclared type is a scenario
# bug rather than a bridge the suite never built. The refusal is thrown
# after the transport has recorded the request, so the record and the
# failure agree.
ANSWERS = r"""
const ANSWERS = {};
const answer = (type, patch) => { ANSWERS[type] = patch; };
const realFetch = globalThis.fetch;
let answering = null;
globalThis.fetch = async (target, init) => {
  const key = String(target);
  if (key.endsWith('/command')) {
    answering = JSON.parse((init || {}).body).type;
    const sent = await realFetch(target, init);
    if (!(answering in ANSWERS)) {
      throw new Error('no answer planned for ' + answering);
    }
    return sent;
  }
  const got = await realFetch(target, init);
  if (key.indexOf('/result') < 0 || key.indexOf('consume') >= 0) return got;
  if (!(answering in ANSWERS)) {
    throw new Error('no answer planned for ' + answering);
  }
  return jsonResponse(Object.assign({}, await got.json(),
                                  ANSWERS[answering]));
};
"""

MOUNT = "mount(container, bus);\n"
SETTLE = ("await bounded(settle(), 'the mount settled',\n"
          "  _dashnodeStepTimeoutMs);\n")

# The one plan a command needs. The delivery id is a constant because the
# assertions are about the fields the section sent, and the result it
# answers with is the scenario's own table (`answer`) applied to this
# envelope -- the default here is only what a poll carries before the
# wrapper replaces it.
PLAN = ("drive.route('/command', { did: 'delivery-1' });\n"
        "drive.route('/result?tab=extension', { result: null });\n")


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


def commands(report):
    """The recorded command bodies, in the order the section sent them."""
    return [r['body'] for r in legs(report)['command']]


def types(report):
    """The type of each recorded command, in order."""
    return [body['type'] for body in commands(report)]
