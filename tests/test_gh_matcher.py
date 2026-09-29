#!/usr/bin/env python3
"""Which strings count as the report of a rate limit, and which do not.

The match is two words with a bounded left anchor, and both halves of
that claim were unpinned: the module says a word merely ENDING in the two
is not one of them, and nothing would have failed if that stopped being
true - `NOT_RATE_LIMITED` matches today, because an underscore is a
boundary to the anchor. It also says the separator between them is
anything that is neither a letter nor a digit, and no row said so.

Every control here drives the real `graphql` and a real `gh` process.
The strings ride the body of an answer that did not deliver, because
that is the only carrier that reads text; a delivered answer is not
refused whatever it carries, and those rows are in
`tests/test_gh_body_shapes.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402

ROOT = _util.ROOT
SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'gh_client.py'

ITEM_QUERY = ('query Watch($after: String) { repository { items('
              'first: 2, after: $after) { pageInfo { hasNextPage '
              'endCursor } nodes { id } } } }')


def _client():
    return _util.load(SOURCE, 'gh_matcher_contract')


def _answer(text):
    """A 403 whose body says exactly `text`, with no other carrier."""
    return {'status': 403, 'headers': {},
            'body': {'message': text},
            'stderr': 'gh: forbidden (HTTP 403)\n'}


def _refused_by(mod, text, tmp):
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': _answer(text)})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited:
            return True
        except mod.QueryError:
            return False
        return False


def test_a_word_that_merely_ends_in_these_two_is_not_a_refusal(tmp):
    """The left anchor, which the module's comment claims and nothing
    held. "separate limits" contains "rate limit" from the fourth letter
    of the first word onward, and a reader looking for the two words
    anywhere would sleep on it: the anchor requires a character that is
    neither a letter nor a digit before them.
    """
    mod = _client()
    assert _refused_by(mod, 'The corporate limits are higher on annual '
                            'plans.', tmp) is False
    assert _refused_by(mod, 'Your separate limits are unaffected.',
                       tmp) is False


def test_the_separator_between_them_is_anything_but_a_word_character(tmp):
    """The separator class, one row per spelling GitHub has used.

    A match on the two words with a space between them would catch every
    English message and none of the identifiers, which is the half of
    the report a GraphQL entry arrives as. Zero separators is the
    `rateLimit` extension's own name, so it is the one that must hold
    for a refusal to be seen at all.
    """
    mod = _client()
    for spelling in ('rateLimit', 'rate-limited', 'rate/limit',
                     'rate.limit', 'rate:limit', 'rate_limit',
                     'RATE_LIMIT', 'RATE LIMITED'):
        assert _refused_by(mod, spelling, tmp) is True, spelling


def test_the_two_words_alone_never_make_a_refusal(tmp):
    """The inversion of the row above, and of every alternative the
    matcher gained: a 403 whose body names none of the report is a
    permission refusal whatever the carrier is.
    """
    mod = _client()
    assert _refused_by(mod, 'Resource not accessible by integration.',
                       tmp) is False
    assert _refused_by(mod, 'The rate of requests is unbounded here.',
                       tmp) is False
    assert _refused_by(mod, 'A limited scope was granted.', tmp) is False


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ghmatcher_')


if __name__ == '__main__':
    raise SystemExit(main())
