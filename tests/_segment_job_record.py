"""The job record the bridge wrote beside a job's segment directory.

The lock-side controls and the namespace controls read the same file to
settle different things — who owns a name, and what the write path
recorded — so both read it the one way the file is spelled.
"""
import json
from pathlib import Path


def _job_record(docroot, job):
    """The record the bridge wrote for `job` under a bridge-owned root."""
    return json.loads(
        (Path(docroot) / 'segments' / f'{job}.json').read_text(
            encoding='utf-8'))
