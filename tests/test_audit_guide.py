#!/usr/bin/env python3
"""Executable contract for the interactive repository-audit guide."""
import json
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import _util  # noqa: E402


GUIDE = ROOT / "docs" / "guides" / (
    "2026-09-10-comprehensive-repository-audit-guide.html")


class _ApplicationScript(HTMLParser):
    def __init__(self):
        super().__init__()
        self._inside_application = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        self._inside_application = dict(attrs).get(
            "data-audit-guide-app") is not None

    def handle_endtag(self, tag):
        if tag == "script":
            self._inside_application = False

    def handle_data(self, data):
        if self._inside_application:
            self.parts.append(data)


def _guide_script():
    parser = _ApplicationScript()
    parser.feed(GUIDE.read_text(encoding="utf-8"))
    source = "".join(parser.parts)
    assert source, "audit guide has no executable application script"
    return source


def test_audit_guide_state_contract_runs_in_node(tmp):
    """Removing progress or export logic breaks the guide's resumable workflow."""
    del tmp
    contract = {
        "scope": {"status": "complete", "notes": "snapshot pinned"},
        "settings": {"status": "in_progress", "notes": "webhooks remain"},
        "security": {"status": "not_applicable", "notes": ""},
    }
    node_test = f"""
globalThis.window = globalThis;
{_guide_script()}
const domains = ['scope', 'settings', 'security'];
const state = {json.dumps(contract)};
const summary = AuditGuide.summarize(domains, state);
if (JSON.stringify(summary) !== JSON.stringify({{
  total: 3, complete: 1, active: 1, notApplicable: 1,
  remaining: 1, percent: 50
}})) throw new Error('wrong summary: ' + JSON.stringify(summary));
const exported = JSON.parse(AuditGuide.buildExport(
  domains, state, 'f4ed018', '2026-09-10T12:00:00.000Z'));
if (exported.schemaVersion !== 1 || exported.snapshot !== 'f4ed018')
  throw new Error('wrong export metadata');
if (exported.domains.settings.notes !== 'webhooks remain')
  throw new Error('notes were not exported');
const prompt = AuditGuide.buildPrompt(domains, state, 'f4ed018');
if (!prompt.includes('settings — in progress: webhooks remain'))
  throw new Error('incomplete domain missing from prompt: ' + prompt);
if (prompt.includes('scope — complete'))
  throw new Error('completed domain leaked into continuation prompt');
process.stdout.write('audit-guide-contract-ok');
"""
    result = subprocess.run(
        ["node", "-e", node_test],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "audit-guide-contract-ok"


def test_audit_guide_stays_usable_when_browser_storage_and_copy_fail(tmp):
    """Denied storage and clipboard access must not disable audit exports."""
    del tmp
    node_test = f"""
globalThis.window = globalThis;
const elements = {{}};
function element() {{
  return {{
    textContent: '', value: '', style: {{}}, handlers: {{}},
    addEventListener(type, handler) {{ this.handlers[type] = handler; }},
    select() {{ this.selected = true; }}
  }};
}}
const status = element();
const notes = element();
const card = {{
  dataset: {{ domainId: 'source-and-scope' }},
  querySelector(selector) {{
    if (selector === '.domain-status') return status;
    if (selector === '.domain-notes') return notes;
    throw new Error('unexpected selector: ' + selector);
  }}
}};
globalThis.document = {{
  querySelectorAll() {{ return [card]; }},
  getElementById(id) {{ return elements[id] ||= element(); }},
  execCommand() {{ return false; }}
}};
Object.defineProperty(globalThis, 'navigator', {{
  configurable: true, value: {{}}
}});
globalThis.localStorage = {{
  getItem() {{ return 'null'; }},
  setItem() {{ throw new Error('storage denied'); }},
  removeItem() {{ throw new Error('storage denied'); }}
}};
globalThis.confirm = () => true;
globalThis.setTimeout = () => 1;
{_guide_script()}
status.value = 'complete';
status.handlers.change();
notes.value = 'snapshot pinned';
notes.handlers.input();
elements.auditSnapshot.value = '0123456789abcdef';
elements.exportJson.handlers.click();
const exported = JSON.parse(elements.exportOutput.value);
if (exported.snapshot !== '0123456789abcdef')
  throw new Error('page export omitted snapshot');
if (exported.domains['source-and-scope'].status !== 'complete' ||
    exported.domains['source-and-scope'].notes !== 'snapshot pinned')
  throw new Error('live domain controls were not exported');
if (!elements.persistenceStatus.textContent.includes('not saved'))
  throw new Error('storage failure was not disclosed');
if (elements.toast.textContent !== 'Copy failed; select the output manually')
  throw new Error('clipboard failure reported success: ' + elements.toast.textContent);
process.stdout.write('audit-guide-degraded-browser-ok');
"""
    result = subprocess.run(
        ["node", "-e", node_test],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "audit-guide-degraded-browser-ok"


if __name__ == "__main__":
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix="auditguide_"))
