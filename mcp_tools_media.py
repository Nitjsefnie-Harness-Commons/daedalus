"""Media tools for the Daedalus MCP front end."""

from mcp.server.mcpserver import Image
from daedalus_cli import SEGMENT_SIG_HEADER


def register(mcp, bridge):
    """Define this group's tools against `mcp`, bound to `bridge`.

    Returns {tool name: coroutine function} so the composition point can keep
    the tools callable as its own attributes.
    """

    @mcp.tool()
    async def screenshot(cmd_id: str = '_ss', chrome_tab: int | None = None,
                         format: str = 'png', quality: int | None = None,
                         include_image: bool = False, timeout: float = 15.0):
        """Capture a screenshot via extension.

    Default: returns {path, size} — client can fetch the image separately.
    `include_image=True`: also returns the image bytes inline as an MCP Image
    so the caller can Read it directly without another round-trip.
    """
        fields: dict = {}
        if format:
            fields['format'] = format
        if quality is not None:
            fields['quality'] = quality
        if chrome_tab is not None:
            fields['tabId'] = int(chrome_tab)
        result_blob = await bridge.ext_cmd(
            cmd_id, 'screenshot', timeout=timeout, **fields)
        meta = {'path': result_blob.get('path', ''), 'size': result_blob.get('size', 0)}
        if not include_image:
            return meta
        # By path, not by id: ids are reused, so an id names a directory rather
        # than a capture and its newest file belongs to whichever invocation
        # finished last.
        selector = {'path': meta['path']} if meta['path'] else {'id': cmd_id}
        img_bytes = await bridge.get_raw('/screenshot', **selector)
        return [meta, Image(data=img_bytes, format=format)]

    @mcp.tool()
    async def segment_job(job: str) -> dict:
        """Create (or re-fetch) the HLS segment job `job` and return its job-scoped
    capability as {ok, sig}. The sig is what examples/hls-segment-relay.js
    substitutes for __SIG__; minting is idempotent for the owning token, so a
    resumed run gets the same one back."""
        return await bridge.post('/segment-job', {'job': job})

    @mcp.tool()
    async def segment_status(job: str) -> dict:
        """HLS segment relay status for `job`. Returns {count, done, gaps}.

    Creates nothing: the capability is looked up, not minted. A job that does
    not exist raises rather than being brought into being by the question, and
    one owned by a different token raises too.
    """
        # /segment-status takes the job's minted capability, not the bridge token,
        # and GET /segment-job is the lookup that hands it over without creating
        # the job when the name has never been used.
        client = bridge.http_client()
        found = await client.get(
            '/segment-job', params={'job': job}, headers=bridge.auth())
        if found.status_code == 404:
            raise RuntimeError(f'segment_status: no job named {job!r}')
        if found.status_code == 409:
            raise RuntimeError(f'segment_status: job {job!r} is owned by a different token')
        found.raise_for_status()
        sig = found.json()['sig']
        r = await client.get('/segment-status', params={'job': job},
                             headers={SEGMENT_SIG_HEADER: sig})
        r.raise_for_status()
        data = r.json()
        done = data.get('done', [])
        full = set(range(min(done), max(done) + 1)) if done else set()
        data['gaps'] = sorted(full - set(done))
        return data

    @mcp.tool()
    async def uploads(upload_id: str = '', limit: int | None = None,
                      offset: int | None = None):
        """List uploaded files. When limit/offset given, returns {items,total,limit,offset}.
    Without paging, returns a bare array (back-compat with the server surface).
    """
        params: dict = {}
        if upload_id:
            params['id'] = upload_id
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset
        return await bridge.get('/upload', **params)

    @mcp.tool()
    async def delete_upload(upload_id: str = '', filename: str = '') -> dict:
        """Delete uploads. No args → all for token; id only → all files under that id;
    id+filename → single file. Returns the server response."""
        # A filename alone has no narrower target than the whole token, so it
        # would delete every upload rather than the one file it names.
        if filename and not upload_id:
            return {'error': 'filename requires upload_id'}
        body: dict = {}
        if upload_id:
            body['id'] = upload_id
        if filename:
            body['filename'] = filename
        return await bridge.delete('/upload', body)

    return {
        'screenshot': screenshot,
        'segment_job': segment_job,
        'segment_status': segment_status,
        'uploads': uploads,
        'delete_upload': delete_upload,
    }
