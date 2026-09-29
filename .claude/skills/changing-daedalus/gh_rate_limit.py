#!/usr/bin/env python3
"""Whether one `gh` answer is a rate-limit refusal, and when to resume.

Its own module because it is one subject, and because `gh_client.py` had
ten lines of headroom under the production ceiling when this was written:
a second spelling of "this is the report" is a second thing to widen, and
the reader is what everything else in the tree asks.

The rule, in one sentence: an answer reports exhaustion when it carries
rate-limit EVIDENCE, whatever the status and whatever `gh`'s exit code
says. Evidence is a `Retry-After`; or an `X-Ratelimit-Remaining: 0`
beside the reset it counts down to; or a GraphQL `errors[]` entry whose
`type` or `code` names a rate limit; or text `gh` wrote to stderr. The
evidence is read BEFORE the exit-code refusal, because the exit code is
the last evidence and not the first: a throttled query answers 200 and
exits 1, so a reader that consults the code first raises over the
carriers and never reaches what the answer actually said (issue 1338).

A 403 carrying none of that is a permission refusal and stays a failure.
That is the property the rule is shaped around - a pause must never be
the answer to a question about authority.
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

    The one place that answers it. Every carrier below asks this question
    and none of them asks it again by its own rules, so widening what
    counts as the report is a change to this line and not to four.
    """
    if not text:
        return False
    return RATE_LIMIT.search(str(text).lower()) is not None


def _header_evidence(status, headers, now):
    """(exhausted, when to resume) from the response headers alone.

    A `Retry-After` is a report on its own, and a reset is a report
    wherever it appears: beside a spent `X-Ratelimit-Remaining`, which is
    the pair the live refusal carries on a 200 GitHub exits 1 over (issue
    1338), or on a 403 or 429, where the status has already said the
    request was refused and the reset says when to try again.

    A reset on its own is neither, and neither is a spent counter with
    no moment behind it: both say the limit is gone without saying when
    it returns, and a wait needs a moment to wake at. Nor is the status
    on its own ever the evidence - that is what keeps a permission
    refusal a failure instead of a sleep.
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
    extension. Either field names the report - GitHub has answered the
    same refusal with a `RATE_LIMITED` type and with a `RATE_LIMIT` type
    beside a `graphql_rate_limit` code (issue 1338) - and an `errors[]`
    that is not a list of objects is stepped over rather than believed,
    because a body is data and the reader may not assume its shape.
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


def exhausted(status, headers, body, complained='', payload=None):
    """(whether an answer reports exhaustion, the instant to resume at).

    The one reader, and it reads every carrier the evidence travels on.
    Each is asked only the one question, and the instant is taken from
    whichever carrier reported one, so a header that counts down to the
    reset is not lost to a message that does not.

    `gh`'s exit code is deliberately not a carrier here. It is the last
    evidence and not the first, because a throttled query answers 200 and
    exits 1: a reader that consults the code before the carriers raises
    over them and never reaches what the answer actually said (issue
    1338). A 403 with none of this is not here either, and stays a
    permission refusal rather than a wait.
    """
    now = time.time()
    found = [_header_evidence(status, headers, now),
             _graphql_refusal(payload),
             (_names_a_rate_limit(body), None),
             (_names_a_rate_limit(complained), None)]
    if not any(refused for refused, _ in found):
        return False, None
    return True, next((at for _, at in found if at is not None), None)


def bare_complaint(complained):
    """The refusal an empty stdout can still be, or None when it is none.

    `gh` refusing before the transport produced a response leaves no
    status and no header block to read, so the complaint is the only
    carrier there is. It is also the one carrier with no instant in it,
    so the pause it leads to falls back to the caller's plain minute
    rather than a moment nobody reported.
    """
    if _names_a_rate_limit(complained):
        return RateLimited(complained.strip()[:200], None)
    return None


def refusal_text(status, body, complained):
    """The one line a refusal carries: the status, and what it said.

    The body is the answer, and the complaint is what `gh` made of it, so
    an answer with no body is rendered from the complaint rather than
    from a status code and nothing else.
    """
    return f'HTTP {status}: {(body.strip() or complained.strip())[:200]}'
