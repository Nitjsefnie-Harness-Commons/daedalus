"""Discover and load the extension service worker's classic scripts."""
import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_PATH = ROOT / 'extension' / 'background.js'


def imported_worker_paths(background_path=BACKGROUND_PATH):
    background_path = Path(background_path)
    source = background_path.read_text(encoding='utf-8')
    calls = re.findall(
        r'^importScripts\((.*?)\);\s*$', source,
        flags=re.MULTILINE | re.DOTALL)
    assert len(calls) == 1, (
        f'expected one importScripts call in {background_path}, found '
        f'{len(calls)}')
    try:
        names = ast.literal_eval('[' + calls[0] + ']')
    except (SyntaxError, ValueError) as error:
        raise AssertionError(
            f'importScripts arguments are not string literals: {error}') \
            from error
    assert all(isinstance(name, str) for name in names), (
        'importScripts arguments must all be string literals')
    return tuple(background_path.parent / name for name in names)


def worker_source_paths(background_path=BACKGROUND_PATH):
    background_path = Path(background_path)
    return (background_path, *imported_worker_paths(background_path))


def import_scripts_stub(context_name):
    return r"""
__CONTEXT__.importScripts = (...sourceNames) => {
  for (const sourceName of sourceNames) {
    const sourcePath = require('path').resolve(
      require('path').dirname(backgroundPath), sourceName);
    vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), __CONTEXT__);
  }
};
""".replace('__CONTEXT__', context_name)
