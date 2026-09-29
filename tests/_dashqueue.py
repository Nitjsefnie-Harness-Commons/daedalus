"""The dashboard events a route published under the directory it was handed.

A route that published into a directory other than the one it was given
would still answer 200, so both suites read the queue the directory names
rather than the one the bridge happens to have configured.
"""
import json
from pathlib import Path


def _events(cmd_dir, token):
    """Every dashboard event the routes published under `cmd_dir`."""
    queue = Path(cmd_dir) / f'{token}_dashboard'
    if not queue.is_dir():
        return []
    return [json.loads(path.read_text(encoding='utf-8'))
            for path in sorted(queue.iterdir())]
