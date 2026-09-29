"""Which mechanism answers a tool a suite under `tests/` skips on.

Three do, and exactly one of them has to: the shared installer installs
one, a setup step declares another, and a claim about the runner image
exempts a third. This module holds the three tables, the recognisers
that read what a job actually runs, and the derivation that holds the
tables to the skip set `tests/_lint_tool_roles.py` derives — so "which
mechanism answers this tool" is one file's question and never a
control's.

Split out of `tests/test_ci_lint_tools.py`, which owns the controls, for
the reason the role recognisers moved out of it for the same one: the
mechanisms and the controls that hold them to the tree are read
separately on purpose. Nothing here reads a control name; everything
here answers one question about one tree.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _lint_tool_roles import (  # noqa: E402
    _derive_tool_roles, _tool_roles)
from _suite_jobs import (  # noqa: E402
    WORKFLOW_DIR, _action_name, _job_names, _job_steps)

ROOT = _util.ROOT
# The shared installer's path, and the name it writes what it installed
# under. That name is the whole contract between the script and the test
# reading it back, so the two spell it once each and nowhere else.
#
# The path, NOT the command that runs it. A job's install step is correct
# because it runs the installer, and `suites`, `coverage-matrix` and
# `publish` reach the installer through a root checkout while `timed` checks
# its two trees out into subdirectories and reaches it as
# `head/scripts/ci/install_lint_tools.py`. A constant holding the whole
# invocation pinned that second spelling, so the correct path turned the
# control red with a message arguing for the revert — this branch's own
# lesson arriving for the third time, after the tool set and the door set.
# Matching the path with a substring covers both, because the prefixed form
# ends with the root-relative one.
#
# WHAT THIS CANNOT SEE, both directions, because a reader deciding how far
# to trust a green run needs the bounds rather than the claim:
#
# - It cannot tell a step that RUNS the installer from a comment quoting
#   the path inside a `run:` block. That error runs toward a red control
#   naming a comment, which is a cheap edit.
# - It cannot tell a step that runs the installer from a step that NAMES
#   it and would fail at runtime. A path that does not resolve in the job's
#   own directory layout is not a step that runs the installer, and this
#   substring is green on it. That error runs the OTHER way — toward a
#   green control over a broken job — and it is not hypothetical: this
#   branch shipped exactly that, when `timed` named a root-relative script
#   in a job that checks its trees out into subdirectories, and the
#   control stayed green through two review rounds on the strength of this
#   assertion. N1 fixed the instance; nothing in the tree catches the
#   class, and no spelling of this assertion can, because the information
#   that is missing is whether the file exists from where the step runs.
#   The control that would need it is a workflow control resolving each
#   `run:` path against a checkout layout, which does not exist here.
INSTALLER_PATH = 'scripts/ci/install_lint_tools.py'
INSTALLER_SOURCE = ROOT / INSTALLER_PATH
# Tools the suites skip on that no job installs, named here with a reason
# each rather than derived. The derivation cannot supply this list: it read
# the tree's own `present` set as covering a tool, and `present` is an
# assertion the tree may never reach — the jq assert in
# `tests/_coverage_comment_publication.py` sits behind a stub-environment
# condition that is false on every ordinary run, which is how a suite
# skipping on a binary nothing installs read green. Each entry is a claim
# about the runner image, written down so a human can check it and change
# it, and no derivation can be asked to confirm it.
#
# `actions/checkout` is not a declaration of git: it is a client of it, and
# an action that runs git is not a step that says a machine may lack it.
#
# The reason each entry carries is DOCUMENTATION, not the thing that makes
# the entry legal. An entry is legal because no action in any workflow
# names the tool, which the exempt-tool control derives from the workflows
# themselves; that is what stopped the `node` entry this branch removed
# from coming straight back, since restoring it with its own reason text
# satisfied every other control in this file.
SHIPPED_BY_THE_IMAGE = {
    'git': 'every job here checks out through actions/checkout, which '
           'runs git, and the hosted images ship it',
}
# Tools a suite skips on that no job installs through
# `scripts/ci/install_lint_tools.py`, and the setup action a job has to
# carry to DECLARE it. A third mechanism beside the installer's TOOLS and
# the image claim, because a tool the runtime distributes is provisioned by
# a step rather than by an install list, and a control that asked the
# installer to install it would be asking for a mechanism this repository
# does not use.
#
# The action NAME, never the pinned commit: which action a job names is a
# property of the job, while the commit it is pinned at is a property of the
# workflow, and a table holding the SHA would have to be edited on every
# dependabot bump to keep saying the same thing. The pin every declaration
# has to use is a different control's, on a family of actions.
DECLARED_BY = {
    'node': 'actions/setup-node',
}


def _runs_installer(run):
    """Whether a step's command runs the shared installer, from any tree.

    The path is a suffix of every working spelling of it — a job that
    checks the repository out under `head/` runs
    `python head/scripts/ci/install_lint_tools.py` — so matching the path
    asks the property the control means rather than the command one job
    happened to use.
    """
    return INSTALLER_PATH in run


def _declared_tools():
    """The tool set the shared installer says it installs."""
    return frozenset(_util.load(INSTALLER_SOURCE, 'lint_installer').TOOLS)


def _declared_tool_actions():
    """`tool -> {(action, workflow)}` for every tool a workflow action names.

    The last segment of an action name, split into words, because that is
    where a setup action says what it sets up: `actions/setup-node`
    declares `node`. Read off the workflows rather than off a table, so
    "no action exists for this tool" is a fact about this tree rather than
    a sentence somebody wrote — which is the difference between an
    exemption a control checks and an exemption a control believes.

    Every action in every workflow counts, not only the ones in a
    suite-running job: a tool is exempt precisely when the repository has
    no way to declare it anywhere, and an `eslint` job's `actions/setup-node`
    is a way to declare it that this tree still has.
    """
    declared = {}
    for source in sorted(WORKFLOW_DIR.glob('*.yml')) + sorted(
            WORKFLOW_DIR.glob('*.yaml')):
        workflow = source.read_text(encoding='utf-8')
        for job in _job_names(workflow):
            for step in _job_steps(workflow, job):
                action = _action_name(step)
                if not action:
                    continue
                for word in re.findall(r'[a-z0-9]+',
                                       action.rsplit('/', 1)[-1]):
                    declared.setdefault(word, set()).add((action, source.name))
    return declared


def _mechanisms():
    """The three sets answering a skipped tool, and the one subtraction.

    Every tool a suite skips on reaches exactly one of: the shared
    installer, a setup step, or a claim about the runner image. Naming them
    in one place is what lets each control ask about its own and the
    closure control ask about all three at once, without any of the three
    being spelled twice.
    """
    return (_declared_tools() | set(SHIPPED_BY_THE_IMAGE) | set(DECLARED_BY))


def _mechanism_shares():
    """`(name, tools)` for each mechanism a skipped tool can be answered by."""
    return (('scripts/ci/install_lint_tools.py',
             frozenset(_declared_tools())),
            ('SHIPPED_BY_THE_IMAGE', frozenset(SHIPPED_BY_THE_IMAGE)),
            ('DECLARED_BY', frozenset(DECLARED_BY)))


def _mechanism_share(mechanism, sources=None):
    """The tools a suite may run without that ONE mechanism is answerable for.

    The intersection of the derived skip set with the mechanism's own set,
    and never anything outside that mechanism: a caller indexing the
    mechanism with what comes back must not be able to reach a tool the
    mechanism does not carry, which is the crash the union caused.
    """
    skipped, _present = (_tool_roles() if sources is None
                         else _derive_tool_roles(sources))
    return skipped & set(mechanism)


def _unjournalled(sources=None):
    """The tools a suite may run without, minus every mechanism that answers.

    The property is *a tool the suites can skip on*, so the required set is
    the skip set itself. The earlier narrowing subtracted the tools the tree
    treats as present, on the argument that a tool in both is already
    covered by a control which fails rather than skips — and that argument
    is false of the code as written, because the derivation could not tell
    an assert the tree always reaches from one it never does. Two reviews
    drove a false green through it: a suite skipping on `jq` grew the skip
    set, `jq` was in the present set because of an assert behind a stub
    environment that is off on every ordinary run, and the control stayed
    green with no job installing `jq`. So the subtraction is gone, and
    what genuinely needs no install is named below with a reason per entry.

    This is the RESIDUE — what no mechanism answers at all — and a
    mechanism's own share is a different question, asked by
    `_mechanism_share`. The two used to be one function returning a union,
    which leaked the residue into a caller's share and raised `KeyError`
    on the one input a future tool takes: a suite skipping on a binary no
    mechanism names put that binary into the declaration control's share,
    which then looked it up in a table that does not carry it.

    The `requires=` channel on `_util.runner` is the other machine-readable
    one, and it is not read here: its only value in the tree is the prose
    string `'Chromium and Node'`, so a control that demanded a job install
    that string would be asserting something no job can satisfy and no plant
    could falsify. A tool named in prose is a requirement, not a binary.
    """
    skipped, _present = (_tool_roles() if sources is None
                         else _derive_tool_roles(sources))
    return skipped - _mechanisms()


def _mechanism_residue(sources=None):
    """`(residue, overlap, stale)` for the mechanisms and the skip set.

    A tool the suites skip on is answered by exactly one mechanism, and
    this is the question that holds the three of them to it: answered by
    none, answered by more than one, or held in a share the tree no longer
    derives. `sources` is the seam the derivation already has, so a suite
    that skips on a tool no mechanism answers is asked about without planting
    a file in `tests/`; over a synthetic source only the residue arm means
    anything, because a module that skips on one tool leaves every other
    mechanism's entry looking stale.
    """
    skipped, _present = (_tool_roles() if sources is None
                         else _derive_tool_roles(sources))
    shares = _mechanism_shares()
    answered = {tool for _name, tools in shares for tool in tools}
    residue = sorted(skipped - answered)
    overlap = sorted(tool for tool in skipped
                     if sum(tool in tools for _name, tools in shares) > 1)
    stale = sorted((name, sorted(tools - skipped))
                   for name, tools in shares if tools - skipped)
    return residue, overlap, stale
