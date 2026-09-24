"""The hotfix record's byte-bound scenario, split from the shared file.

`_boundary_scenarios.py` carries one JavaScript fragment per boundary, and
this one is long enough to have taken that module past the size ceiling.
The fragment is appended to the same harness; it is dispatched by the
`hotfix-quota` row in the shared table.
"""

HOTFIX_SCENARIOS = r"""
// The hotfix record's byte bound. Chrome measures QUOTA_BYTES as the JSON
// stringification of every value plus every key's length, and the record is
// ONE key, so its charge is the record's JSON bytes plus the key's length.
// This scenario's own measure is computed from its OWN construction and never
// calls the production measure, so a boundary placed here is placed
// independently: dropping either term in production moves the admission and
// the test disagrees.
const HOTFIX_TEST_KEY = 'daedalus-hotfixes';
const HOTFIX_TEST_TS = 1700000000000;
const enc = new TextEncoder();

function hotfixRecordBytes(version, fixes) {
  return enc.encode(JSON.stringify({ version, fixes })).length;
}

function hotfixRecordCharge(version, fixes) {
  return hotfixRecordBytes(version, fixes) + enc.encode(HOTFIX_TEST_KEY)
    .length;
}

// A fix entry as the store composes it, with the code run at `length`
// characters, is what `compose` builds; the projected charge below is the
// measure a boundary is placed on.
async function runHotfixQuota() {
  const plan = JSON.parse(commandText);
  const version = plan.version;
  const store = storageStore;
  const posted = [];
  function command(fields) {
    context.hotfixCommand = fields;
    return vm.runInContext('dispatchCommand(hotfixCommand)', context);
  }
  // What the operator receives: the payload the worker POSTed for the
  // command, plus what the record holds afterwards.
  function summary() {
    const payload = resultPayloads[resultPayloads.length - 1];
    const fixes = (store[HOTFIX_TEST_KEY] || { fixes: [] }).fixes;
    return {
      id: payload.id,
      error: payload.error,
      result: payload.result === undefined ? null : payload.result,
      fixes: fixes.map((fix) => ({
        id: fix.id, codeLength: fix.code.length, permanent: fix.permanent,
      })),
      charge: hotfixRecordCharge(version, fixes),
    };
  }
  function compose(fixes, spec) {
    const kept = fixes.filter((fix) => fix.id !== spec.id);
    kept.push({ id: spec.id, code: 'x'.repeat(spec.codeLength),
      ts: HOTFIX_TEST_TS, permanent: spec.permanent === true });
    return kept;
  }
  async function step(spec) {
    // A step with a `seed` starts a fresh store; a step without one
    // continues on whatever the previous step left, so a sequence can show
    // that a refused store released the lock (a clear and a further store
    // then both run).
    if (spec.seed) {
      delete store[HOTFIX_TEST_KEY];
      if (spec.seed.length > 0) {
        store[HOTFIX_TEST_KEY] = {
          version,
          fixes: spec.seed.map((fix) => ({ id: fix.id,
            code: 'x'.repeat(fix.codeLength), ts: HOTFIX_TEST_TS,
            permanent: fix.permanent === true })),
        };
      }
    }
    const seeded = (store[HOTFIX_TEST_KEY] || { fixes: [] }).fixes;
    if (spec.clear) {
      await command({ id: spec.id, type: 'clear-hotfix',
        fixId: spec.id, _did: 'did-clear-' + spec.id });
      posted.push(summary());
      return;
    }
    const incoming = { id: spec.id, codeLength: spec.codeLength,
      permanent: spec.permanent === true };
    const projected = hotfixRecordCharge(
      version, compose(seeded, incoming));
    await command({ id: spec.id, type: 'store-hotfix',
      fixId: spec.id, code: 'x'.repeat(spec.codeLength),
      permanent: spec.permanent, _did: 'did-store-' + spec.id });
    posted.push(summary());
    posted[posted.length - 1].projected = projected;
  }
  for (const spec of plan.steps) await step(spec);
  return posted;
}
"""
