#!/usr/bin/env python3
"""How a segment route decides whether the caller may write to a job.

The job capability, not the bridge token, is what authorizes `POST /segment`
and `GET /segment-status`, and it may travel in the
`X-Daedalus-Segment-Sig` header or in the query. These tests pin which
carriers are accepted, that a duplicate or disagreeing pair is refused before
either value is selected, and that a request the capability does not cover is
answered before its body is read.
"""
import http.client
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _segments import TOK, mint_job, post_segment, seg_job  # noqa: E402


def test_segment_post_and_status_require_capability(tmp):
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        seg_dir = Path(docroot) / 'segments' / job

        def post_seg(query):
            return _util.request(base + '/segment?' + query, 'POST', body=b'\x47',
                                 headers={'Content-Type': 'application/octet-stream'})

        # No sig and wrong sig: 403, and no file written.
        status, _ = post_seg(f'job={job}&seg=1&total=2')
        assert status == 403, status
        status, _ = post_seg(f'job={job}&seg=1&total=2&sig=wrong')
        assert status == 403, status
        # A sig minted for another job opens nothing here.
        _, other = mint_job(base, TOK, seg_job())
        status, _ = post_seg(f'job={job}&seg=1&total=2&sig={other["sig"]}')
        assert status == 403, status
        # The bridge token is not a capability either.
        status, _ = post_seg(f'job={job}&seg=1&total=2&sig={TOK}')
        assert status == 403, status
        assert list(seg_dir.rglob('*')) == [], list(seg_dir.iterdir())

        # Status answers the same way.
        status, _ = _util.get_json(base + f'/segment-status?job={job}')
        assert status == 403, status
        status, _ = _util.get_json(base + f'/segment-status?job={job}&sig=wrong')
        assert status == 403, status
        # An unknown job is indistinguishable from a wrong sig (no oracle).
        status, _ = _util.get_json(base + f'/segment-status?job={seg_job()}&sig={sig}')
        assert status == 403, status


def test_a_bad_segment_capability_is_refused_before_the_body_arrives(tmp):
    """The sig is in the query string, so the body never has to be read.

    A 24 MiB post with a bad sig was buffered in full and then answered 403,
    moving the process high-water mark by the size of a body the bridge was
    always going to reject. Nothing about the answer depends on those bytes.

    The proof is the answer arriving while most of the declared body has not
    been sent: only the drain bound is written, and the refusal comes back
    without the remaining megabytes. Before the fix this request produced no
    answer at all until the socket deadline expired.
    """
    env = {'DAEDALUS_REQUEST_TIMEOUT': '5'}
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        declared = 8 * 1024 * 1024
        port = int(base.rsplit(':', 1)[1])
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
        try:
            conn.putrequest(
                'POST', f'/segment?job={job}&seg=0&total=1&sig=notthesig')
            conn.putheader('Content-Type', 'application/octet-stream')
            conn.putheader('Content-Length', str(declared))
            conn.endheaders()
            # Exactly the bound the refusal drains, so the server reaches its
            # close without waiting on bytes this test deliberately withholds.
            conn.send(b'\x47' * 65536)
            response = conn.getresponse()
            status, payload = response.status, response.read()
        finally:
            conn.close()
        assert status == 403, (status, payload)
        assert json.loads(payload) == {'error': 'bad sig'}, payload
        seg_dir = Path(docroot) / 'segments' / job
        stored = sorted(seg_dir.glob('*.ts')) if seg_dir.is_dir() else []
        assert not stored, stored
        assert minted['sig'] != 'notthesig'


def test_a_segment_body_without_a_declared_length_is_refused(tmp):
    """An undeclared body is not an empty one.

    A missing Content-Length was read as zero, and this is the one route
    whose body is opaque bytes rather than JSON that has to parse: the
    sender's segment was discarded, an empty .ts was written in its place,
    and the answer was success.
    """
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        port = int(base.rsplit(':', 1)[1])
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
        try:
            conn.putrequest(
                'POST', f'/segment?job={job}&seg=0&total=1&sig={sig}')
            conn.putheader('Content-Type', 'application/octet-stream')
            conn.endheaders()
            conn.send(b'\x47' * 188)
            response = conn.getresponse()
            status, payload = response.status, response.read()
        finally:
            conn.close()
        assert status == 411, (status, payload)
        seg_dir = Path(docroot) / 'segments' / job
        stored = sorted(seg_dir.glob('*.ts')) if seg_dir.is_dir() else []
        assert not stored, stored


def test_segment_authority_carriers_reject_every_duplicate_shape(tmp):
    """Segment authority never selects a job or sig from repeated carriers."""
    with _util.bridge(tmp) as (base, docroot):
        job = 'duplicate-scope'
        other_job = 'duplicate-other-scope'
        status, minted = mint_job(base, TOK, job)
        assert status == 200, (status, minted)
        status, _other = mint_job(base, TOK, other_job)
        assert status == 200, status
        sig = minted['sig']
        duplicate_values = (
            ('sig', sig, 'wrong'),
            ('sig', 'wrong', sig),
            ('sig', sig, sig),
            ('sig', '', sig),
            ('sig', sig, ''),
            ('job', job, other_job),
            ('job', other_job, job),
            ('job', job, job),
            ('job', '', job),
            ('job', job, ''),
        )
        replies = []
        for index, (carrier, first, second) in enumerate(duplicate_values):
            if carrier == 'sig':
                query = (f'job={job}&seg={index}&total=20&'
                         f'sig={first}&sig={second}')
            else:
                query = (f'job={first}&job={second}&seg={index}&total=20&'
                         f'sig={sig}')
            status, raw = _util.request(
                base + '/segment?' + query, 'POST', body=b'G',
                headers={'Content-Type': 'application/octet-stream'})
            body = json.loads(raw)
            replies.append(('POST /segment', carrier, first, second,
                            status, body.get('error')))

            if carrier == 'sig':
                query = f'job={job}&sig={first}&sig={second}'
            else:
                query = f'job={first}&job={second}&sig={sig}'
            status, body = _util.get_json(base + '/segment-status?' + query)
            replies.append(('GET /segment-status', carrier, first, second,
                            status, body.get('error')))

        assert all(status == 400 and error == f'duplicate {carrier}'
                   for _route, carrier, _first, _second, status, error
                   in replies), replies
        segment_files = list(
            (Path(docroot) / 'segments' / job).glob('*.ts'))
        segment_files += list(
            (Path(docroot) / 'segments' / other_job).glob('*.ts'))
        assert segment_files == [], segment_files


def test_segment_job_rejects_duplicate_job_before_minting(tmp):
    """Repeated body job keys never select an ownership or capability scope."""
    duplicates = (
        ('body-job-a', 'body-job-b'),
        ('body-job-c', 'body-job-c'),
        ('', 'body-job-d'),
        ('body-job-e', ''),
    )
    with _util.bridge(tmp) as (base, docroot):
        replies = []
        for first, second in duplicates:
            raw_body = (b'{"token":' + json.dumps(TOK).encode()
                        + b',"job":' + json.dumps(first).encode()
                        + b',"job":' + json.dumps(second).encode() + b'}')
            status, raw = _util.request(
                base + '/segment-job', 'POST', body=raw_body,
                headers={'Content-Type': 'application/json'})
            body = json.loads(raw)
            replies.append((first, second, status, body))

        assert all(status == 400 and body == {'error': 'duplicate job'}
                   for _first, _second, status, body in replies), replies
        assert list((Path(docroot) / 'segments').iterdir()) == []


def test_segment_routes_accept_a_capability_header(tmp):
    """A job capability is reusable for its job, so it stays out of the target.

    `sig` authorizes every write and status read for its job until the job is
    gone. Written into a request target it is retained by the same logs and
    tooling a bridge token would be, with no expiry to bound the exposure.
    """
    job = seg_job()
    with _util.bridge(tmp) as (base, docroot):
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        status, raw = _util.request(
            base + f'/segment?job={job}&seg=1&total=1', 'POST', body=b'bytes',
            headers={'Content-Type': 'application/octet-stream',
                     'X-Daedalus-Segment-Sig': sig})
        assert status == 200, (status, raw)
        stored = Path(docroot) / 'segments' / job / '000001.ts'
        assert stored.is_file(), sorted(
            (Path(docroot) / 'segments' / job).iterdir())
        status, payload = _util.get_json(
            base + f'/segment-status?job={job}',
            headers={'X-Daedalus-Segment-Sig': sig})
        assert status == 200 and payload == {'done': [1], 'count': 1}, (
            status, payload)


def test_a_wrong_segment_capability_header_is_refused(tmp):
    """The header is checked, not trusted, on both segment routes."""
    job = seg_job()
    with _util.bridge(tmp) as (base, _docroot):
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        status, raw = _util.request(
            base + f'/segment?job={job}&seg=1&total=1', 'POST', body=b'bytes',
            headers={'Content-Type': 'application/octet-stream',
                     'X-Daedalus-Segment-Sig': 'notthesig'})
        assert status == 403, (status, raw)
        status, payload = _util.get_json(
            base + f'/segment-status?job={job}',
            headers={'X-Daedalus-Segment-Sig': 'notthesig'})
        assert status == 403 and payload == {'error': 'bad sig'}, (
            status, payload)


def test_a_segment_header_and_query_sig_must_agree(tmp):
    """Two different capabilities in one request is an ambiguous carrier."""
    job = seg_job()
    with _util.bridge(tmp) as (base, _docroot):
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        status, payload = _util.get_json(
            base + f'/segment-status?job={job}&sig=other',
            headers={'X-Daedalus-Segment-Sig': sig})
        assert status == 400 and payload == {'error': 'conflicting sig'}, (
            status, payload)
        # Repeating the same one is not a disagreement.
        status, payload = _util.get_json(
            base + f'/segment-status?job={job}&sig={sig}',
            headers={'X-Daedalus-Segment-Sig': sig})
        assert status == 200 and payload == {'done': [], 'count': 0}, (
            status, payload)


def test_a_duplicate_segment_capability_header_is_refused(tmp):
    """Two capability headers are refused before either is selected."""
    job = seg_job()
    with _util.bridge(tmp) as (base, _docroot):
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        conn, response = _util.header_stream(
            base, f'/segment-status?job={job}',
            (('X-Daedalus-Segment-Sig', sig),
             ('X-Daedalus-Segment-Sig', sig)))
        try:
            status, payload = response.status, json.loads(response.read())
        finally:
            response.close()
            conn.close()
        assert status == 400 and payload == {
            'error': 'duplicate segment capability header'}, (status, payload)


def test_a_query_sig_still_authorizes_a_segment_route(tmp):
    """The older carrier keeps working; every deployed relay script uses it."""
    job = seg_job()
    with _util.bridge(tmp) as (base, _docroot):
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        status, raw = post_segment(base, job, sig, 1)
        assert status == 200, (status, raw)
        status, payload = _util.get_json(
            base + f'/segment-status?job={job}&sig={sig}')
        assert status == 200 and payload == {'done': [1], 'count': 1}, (
            status, payload)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='segcapability_')


if __name__ == '__main__':
    raise SystemExit(main())
