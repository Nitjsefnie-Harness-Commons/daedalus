#!/usr/bin/env python3
"""Temporary CodeQL suppression probe. Deleted before the branch lands."""
import sys


def probe_suppressed(secret):
    # codeql[py/clear-text-logging-sensitive-data]
    print(f'[SUPPRESSED-PROBE] {secret}', file=sys.stdout, flush=True)


def probe_control(secret):
    print(f'[CONTROL-PROBE] {secret}', file=sys.stdout, flush=True)


if __name__ == '__main__':
    probe_suppressed('AKIAIOSFODNN7EXAMPLE-secret')
    probe_control('AKIAIOSFODNN7EXAMPLE-secret')
