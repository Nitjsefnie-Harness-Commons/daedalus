#!/usr/bin/env python3
"""The changing-daedalus skill keeps the test-tree type-error ratchet rule."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ratchet_fixture import _normalised  # noqa: E402

ROOT = _util.ROOT
SKILL_SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'SKILL.md'


def _skill_decisions(path=SKILL_SOURCE):
    raw = path.read_text(encoding='utf-8')
    # Isolate the type-error paragraph, so a phrase the other ratchet
    # paragraphs share cannot satisfy this one's decisions for it. The
    # anchor is stable under every mutation below.
    block = next((part for part in raw.split('\n\n')
                  if 'pyrightconfig.tests.json' in part), '')
    paragraph = _normalised(block)
    return {
        'owner': ('.github/ci-thresholds.json' in paragraph
                  and 'type_error_baseline' in paragraph),
        'command': ('python3 scripts/ci/type_error_baseline.py --tighten'
                    in paragraph),
        'growth': 'fix the type error' in paragraph,
        'manual_delete': ('entry naming a file that is gone is removed by '
                          'hand' in paragraph),
        'reads_skill': 'tests/test_type_errors.py' in paragraph,
    }


def test_skill_names_the_state_owner_and_tighten_command(tmp):
    del tmp
    decisions = _skill_decisions()
    assert decisions['owner'], decisions
    assert decisions['command'], decisions
    assert decisions['growth'], decisions
    assert decisions['manual_delete'], decisions
    assert decisions['reads_skill'], decisions


def test_skill_mutations_are_caught_independently(tmp):
    source = SKILL_SOURCE.read_text(encoding='utf-8')
    mutations = (
        ('owner', 'type_error_baseline', 'type_error_table'),
        ('command', 'python3 scripts/ci/type_error_baseline.py --tighten',
         'python3 .github/ci-thresholds.json --tighten'),
        ('growth', 'fix the type error', 'raise the recorded number'),
        ('manual_delete',
         'entry naming a file that is gone\nis removed by hand',
         '--tighten removes every stale entry'),
        ('reads_skill', 'tests/test_type_errors.py',
         'tests/test_type_errors_absent.py'),
    )
    for name, old, new in mutations:
        path = Path(tmp) / f'{name}.md'
        path.write_text(source.replace(old, new), encoding='utf-8')
        assert not _skill_decisions(path)[name], name


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='typeerrors_')


if __name__ == '__main__':
    raise SystemExit(main())
