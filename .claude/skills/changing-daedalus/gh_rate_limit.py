#!/usr/bin/env python3
"""Whether one `gh` answer is a rate-limit refusal, and when to resume.

Its own module because it is one subject, and because `gh_client.py` sits
ten lines under the production ceiling: a second spelling of "this is the
report" is a second thing to widen, and this is what the tree asks.

THE RULE, in one sentence: an answer reports exhaustion when it DID NOT
DELIVER what was asked for and carries rate-limit evidence, or when it
delivered and its own `errors[]` entry is the report.

Delivered is two things at once, read once, by `delivered`.

Evidence is a `Retry-After`; or a reset beside a spent
`X-Ratelimit-Remaining`; or a reset on a 403 or 429; or a GraphQL
`errors[]` entry whose `type` or `code` names a rate limit; or the
answer's own text; or what `gh` wrote to stderr. Which of those is read
on a delivered answer is `REFUSAL_STATUSES` and `exhausted` between them.

The evidence is read BEFORE the exit-code refusal, because the exit code
is the last evidence and not the first: a throttled query answers 200
and exits 1, so a reader that consults the code first raises over the
carriers and never reaches what the answer actually said (issue 1338).

WHICH CARRIERS an answer is asked on, and the one fact that decides
it: an `errors[]` entry is read whatever the answer delivered; the
header, the body's own text and the complaint only when it did not. The
difference is WHO OWNS THE WORDS. An entry's `type` and `code` are
labels the server writes and no caller can put its own data into, so
asking a delivered answer about one cannot read its data. The other
three are places a caller's field, or a coincidence, spells the same
two words - and that is not a theory, it is what the widening cost
before this gate existed. Measured on this branch without it: a 200
that SUCCEEDED, carrying complete data, with a `gh` warning on stderr
that merely mentions a limit, was answered `RateLimited(resume_at=None)`
- a flat minute's pause over a call that worked; and the LAST successful
request before the window closes, carrying `X-Ratelimit-Remaining: 0`
beside valid data, was discarded for the reset. `rateLimit` is the name
of a REAL GraphQL extension, which is why a body-text reading fires on
the very body that most certainly IS a refusal, and on bodies that are
not.

REACHABILITY, so a reader can check it rather than believe it. Two
queries exist in this tree and both name ONE top-level selection,
`repository`: `RUNS_QUERY` here, and `HEAD_PULL_REQUESTS_QUERY` in
`gh_head_prs.py`. A rate limit nested under `repository` would null part
of it and leave `data` non-null, so a throttler reporting at a NESTED
field produces `{"data": {"repository": {...}}, "errors": [RATE_LIMIT]}`,
which a delivery-only gate reads as DELIVERED. That false negative is
why `errors[]` is read on a delivered answer at all.

What the tree holds is one capture, from 2026-09-29, and it is the OTHER
shape: `{"errors":[{"type":"RATE_LIMIT", ...}]}` with no `data` member.
Nothing establishes that GitHub's throttler never produces the partial
one - the GraphQL specification permits a partial answer beside a
non-null `errors[]`, and a `gh` double emits it in a single fixture - so
this reader does not rely on its not arriving.

A 403 carrying none of that is a permission refusal and stays a
failure: a pause must never be the answer to a question about authority.
"""


import re
import time
from datetime import datetime


class RateLimited(RuntimeError):
    """A refusal carrying the instant to resume at, when it carries one.

    A sibling of `QueryError`, never a subclass: a pause must be handled
    before the failure path, and an `except QueryError` that caught this
    too would turn a known wait back into the loud failure it is not.
    """

    def __init__(self, message, resume_at=None):
        super().__init__(message)
        self.resume_at = resume_at


def _resume_at(headers, now):
    """The instant a refusal reported, preferring `Retry-After`."""
    retry = headers.get('retry-after')
    if retry and retry.lstrip('-').isdigit():
        return now + int(retry)
    reset = headers.get('x-ratelimit-reset')
    if reset and reset.lstrip('-').isdigit():
        return float(reset)
    return None


# GitHub spells one report several ways - a `type` of `RATE_LIMITED`, a
# `type` of `RATE_LIMIT`, a `code` of `graphql_rate_limit`, a message
# naming it in English - so the match is on the two words rather than on a
# list of the strings, and a spelling nobody has seen yet still reads as
# the report it is. The start of the match is bounded by a character that
# is neither a letter nor a digit, so a word that merely ENDS in these two
# is not one of them.
RATE_LIMIT = re.compile(r'(?:^|[^a-z0-9])rate[^a-z0-9]*limit')


def _names_a_rate_limit(text):
    """Whether a value is the report of a rate limit, in any spelling.

    The one place that answers it, so widening what counts as the
    report is a change to this line and not to four.
    """
    if not text:
        return False
    return RATE_LIMIT.search(str(text).lower()) is not None


# The statuses an API answers a rate limit with, and so the only ones
# whose bodies the text carrier reads. Every other status is a failure
# of some other kind, and a body naming a limit under one of them says
# so by accident - the words are as likely to be a bug report's. The
# base read 403 and 429 alone, and this is the whole of what widens
# that: 200, because a throttled GraphQL query is answered 200.
REFUSAL_STATUSES = frozenset({200, 403, 429})


def _header_evidence(status, headers, now):
    """(exhausted, when to resume) from the response headers alone.

    A `Retry-After` is a report on its own. A reset is one wherever it
    appears: beside a spent counter, which is the pair the live refusal
    carries on a 200 GitHub exits 1 over (issue 1338), or on a 403 or
    429, where the status has already said the request was refused and
    the reset says when to try again.

    Neither a reset on its own nor a spent counter with no moment
    behind it is evidence: both say the limit is gone without saying
    when it returns, and a wait needs a moment to wake at.
    """
    if headers.get('retry-after'):
        return True, _resume_at(headers, now)
    resume = _resume_at(headers, now)
    if resume is None:
        return False, None
    spent = str(headers.get('x-ratelimit-remaining', '')).strip() == '0'
    return (True, resume) if spent or status in (403, 429) \
        else (False, None)


def _graphql_refusal(payload):
    """Whether a 200 body reports exhaustion in `errors[]`, and when to resume.

    How GraphQL reports a throttled query: the transport succeeded, so the
    evidence is the entry's `type` or its `code` and its `rateLimit`
    extension. An `errors[]` that is not a list of objects is stepped
    over rather than believed: a body is data, and the reader may not
    assume its shape.
    """
    errors = payload.get('errors') if isinstance(payload, dict) else None
    for error in errors or []:
        if not isinstance(error, dict):
            continue
        if not any(_names_a_rate_limit(error.get(field))
                   for field in ('type', 'code')):
            continue
        extensions = error.get('extensions') or {}
        rate = extensions.get('rateLimit') or {}
        now = time.time()
        reset = rate.get('resetAt') or extensions.get('resetAt')
        if reset:
            try:
                iso = str(reset).replace('Z', '+00:00')
                stamp = datetime.fromisoformat(iso)
                return True, stamp.timestamp()
            except ValueError:
                pass
        retry = rate.get('retryAfter') or extensions.get('retryAfter')
        if isinstance(retry, (int, float)):
            return True, now + retry
        return True, None
    return False, None


def delivered(code, payload):
    """Whether the answer delivered what the query asked for.

    Read once, by the one caller, and the whole co-condition of the rule:
    `gh` said it succeeded, and what it wrote is a JSON object carrying
    a non-null `data`. Anything else - a nonzero exit, a body that did
    not parse, a body with no data beside its errors - is an answer that
    did not deliver.

    The rule's third term - stdout was non-empty - is absent on purpose:
    `gh_client.graphql` has already returned or raised on an empty stdout
    before this is called, so a clause here could not be false, and a
    second place to change the rule is a place to change it wrongly.
    """
    return code == 0 and isinstance(payload, dict) \
        and payload.get('data') is not None


def exhausted(status, headers, body, complained='', payload=None, ok=False):
    """(whether an answer reports exhaustion, the instant to resume at).

    The one reader, and it reads every carrier the evidence travels on.
    Each is asked only the one question, and the instant is taken from
    whichever carrier reported one, so a header that counts down to the
    reset is not lost to a message that does not.

    `ok` is the answer's delivery, from `delivered`, and it decides
    WHICH carriers are asked, not whether the answer is read. The reason
    it draws the line there is the module docstring's.
    """
    now = time.time()
    entry = _graphql_refusal(payload)
    if ok:
        return entry
    found = [_header_evidence(status, headers, now),
             entry,
             (_names_a_rate_limit(body) if status in REFUSAL_STATUSES
              else False, None),
             (_names_a_rate_limit(complained), None)]
    if not any(refused for refused, _ in found):
        return False, None
    return True, next((at for _, at in found if at is not None), None)


def bare_complaint(complained):
    """The refusal an empty stdout can still be, or None when it is none.

    `gh` refusing before the transport produced a response leaves no
    status and no header block to read, so the complaint is the only
    carrier there is - and the only one that can never name an instant,
    so the pause it leads to falls back to the caller's plain minute.
    """
    if _names_a_rate_limit(complained):
        return RateLimited(complained.strip()[:200], None)
    return None


def refusal_text(status, body, complained):
    """The one line a refusal carries: the status, and what it said."""
    return f'HTTP {status}: {(body.strip() or complained.strip())[:200]}'
