"""The bound a fixed-unit-of-work Node child is started with, and the
named failure one that breaks raises.

A Node child whose real cost is a fixed unit of work gets a HANG DETECTOR.
A site that launches through `tests/_noderun.py` is bounded by that
module's `CHILD_DEADLINE_S`, and that is the default. A site that does not
-- a real-browser capability probe, a GM storage harness over the shipped
scripts -- keeps its bound at its own call site, composed from the slowest
measured sample in its own table and the one multiple below, because a bare
wall-clock literal at a call site is a figure no reader can re-derive. The
samples are taken with the machine BUSY, not idle, because a bound is two
margins and only the second is what a bare number measures.

What that buys is a RATIO, and one number would hide the site that matters.
Divide the literal the composed bounds replaced by the slowest sample in
its own table -- `10` everywhere but `GM_CHILD`, which was `90`:
`NODE_PROBE` 3.397, `MINIMAL_SPAWN` 0.629, `GM_CHILD` 4.996,
`CONTROL_CHILD` 4.049, `REPO_PROBE` 2.450, `WORKER_PROBE` 3.885,
`WORKER_CHECK` 2.781, `CDP_HARNESS` 5.238 -- a band of 0.63x to 5.24x, ONE
site UNDER the child it guarded.

`NodeBoundExceeded` is the other half of what a broken bound costs: a bare
`subprocess.TimeoutExpired` names a figure nobody can re-derive and carries
the child's output as BYTES even when the launch asked for text.
"""

SITE_HANG_MULTIPLE = 5


class NodeBoundExceeded(AssertionError):
    """A child did not finish inside the bound its own site composed.

    An `AssertionError`, so it reads as the test failure it is, and named
    because a bare `subprocess.TimeoutExpired` is the other half of the
    defect: it names a figure nobody can re-derive, and it carries the
    child's output as BYTES even when the launch asked for text. It carries
    the child, the deadline and that output, because a child which stopped
    answering is exactly the case where its output is all there is.
    `context` names the site when the child is a DIAGNOSTIC — an interpreter,
    not the `node` this class is named for.
    """

    def __init__(self, command, deadline_s, stdout, stderr, context=""):
        self.command = command
        self.deadline_s = deadline_s
        self.stdout = stdout
        self.stderr = stderr
        # Each argv element is bounded too: a source handed to `node -e` is
        # arbitrarily long, and the child line is the one a reader reads
        # first.
        child = ' '.join(str(part)[:60] for part in command or ())
        super().__init__(
            f'a harness child did not finish within {deadline_s}s and was '
            f'killed; this bound is a hang detector, not a health margin, '
            f'so nothing correct reaches it{context}.\n'
            f'  child: {child}\n'
            f'  deadline: {deadline_s}s\n'
            f'  stdout: {stdout[:2000]!r}\n'
            f'  stderr: {stderr[:2000]!r}')


def _as_text(stream):
    """A `TimeoutExpired` stream as text, whatever the launch asked for.

    BYTES or TEXT depending on the launch, and the two do not agree across
    platforms: a `windows-latest` leg hands this a `str` where a Linux leg
    hands `bytes` for the same launch. Undecodable bytes are replaced.
    """
    if stream is None:
        return ''
    return (stream if isinstance(stream, str)
            else stream.decode('utf-8', 'replace'))


def node_bound_expiry(why, deadline_s, context=""):
    """A site's `TimeoutExpired` as this module's named failure.

    The figure beside `why` is the composed deadline that actually fired, so
    the report names the number a maintainer re-derives. `context` is for a
    site whose child is a DIAGNOSTIC: the E2BIG probe names the command.
    """
    return NodeBoundExceeded(
        getattr(why, 'cmd', None), deadline_s,
        _as_text(getattr(why, 'stdout', None)),
        _as_text(getattr(why, 'stderr', None)), context)
