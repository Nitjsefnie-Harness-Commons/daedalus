#!/usr/bin/env python3
"""The changing-daedalus skill keeps the plant helper's save/restore rule.

A path-scoped VCS restore is a statement about the whole path, so it also
sets that path to a committed state, and the uncommitted work it carried is
gone with nothing in the output to say so. The skill's mutation paragraphs
name the helper that avoids it; these pin that each decision stays there.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

SKILL_SOURCE = (_util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
                / 'SKILL.md')


_PARAGRAPH_ANCHORS = ('have not watched fail', 'plant the defect',
                      'Restore the way')


def _flatten(text):
    """Each block on one line, so no needle spans a line break."""
    return '\n\n'.join(' '.join(block.split())
                       for block in text.split('\n\n'))


def _plant_paragraph(text):
    """The three mutation paragraphs, each isolated by a unique anchor."""
    blocks = _flatten(text).split('\n\n')
    found = []
    for anchor in _PARAGRAPH_ANCHORS:
        hits = [part for part in blocks if anchor in part]
        assert len(hits) == 1, (anchor, len(hits))
        found.append(hits[0])
    return ' '.join(' '.join(found).split())


_SKILL_DECISIONS = {
    'helper': 'plant.py save',
    'helper_restore': 'plant.py restore',
    'never_checkout': 'never `git checkout --`',
    'commit_first': 'commit before you plant',
    'whole_tree': 'whole tree',
    'green_signal': 'signal that the restore did nothing',
}


def _skill_decisions(path=SKILL_SOURCE):
    paragraph = _plant_paragraph(path.read_text(encoding='utf-8'))
    return {name: needle in paragraph
            for name, needle in _SKILL_DECISIONS.items()}


def test_skill_names_the_helper_and_the_plant_restore_rule(tmp):
    del tmp
    decisions = _skill_decisions()
    for name, satisfied in decisions.items():
        assert satisfied, (name, sorted(decisions.items()))


def test_skill_mutations_are_caught_independently(tmp):
    original = _flatten(SKILL_SOURCE.read_text(encoding='utf-8'))
    copied = Path(tmp) / 'SKILL.md'
    for name, needle in _SKILL_DECISIONS.items():
        assert needle in _plant_paragraph(original), name
        mutated = original.replace(needle, 'REMOVED BY MUTATION')
        assert mutated != original, (name, 'the mutation changed nothing')
        copied.write_text(mutated, encoding='utf-8')
        assert not _skill_decisions(copied)[name], name


def test_the_isolation_refuses_a_relocated_phrase(tmp):
    blocks = _flatten(SKILL_SOURCE.read_text(encoding='utf-8')).split('\n\n')
    name, needle = 'whole_tree', _SKILL_DECISIONS['whole_tree']
    assert _skill_decisions()[name], 'the control must bite on the real file'
    copied = Path(tmp) / 'SKILL.md'
    at = next(i for i, block in enumerate(blocks) if needle in block)
    own = list(blocks)
    own[at] = own[at].replace(needle, 'REMOVED FROM ITS OWN PARAGRAPH')
    borrowed = [f'A neighbouring paragraph that happens to say {needle}.']
    borrowed.extend(own)
    copied.write_text('\n\n'.join(borrowed), encoding='utf-8')
    assert not _skill_decisions(copied)[name], name


def test_the_isolation_refuses_a_duplicated_anchor(tmp):
    blocks = _flatten(SKILL_SOURCE.read_text(encoding='utf-8')).split('\n\n')
    copied = Path(tmp) / 'SKILL.md'
    copied.write_text('\n\n'.join(
        blocks + [next(b for b in blocks if 'plant the defect' in b)]),
        encoding='utf-8')
    refused = False
    try:
        _skill_decisions(copied)
    except AssertionError:
        refused = True
    assert refused, 'a paragraph carrying an anchor twice was not refused'


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='plantrestore_')


if __name__ == '__main__':
    raise SystemExit(main())
