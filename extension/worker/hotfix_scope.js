/* exported _parseMatch, _scopeRefusal */

// ─── Site scope ───
//
// A fix may carry `match`, a Chrome match pattern. One that does not parse is
// refused where it is stored, never stored then silently never matched: a
// scope the operator believes exists and does not is worse than a visible
// refusal.
const MATCH_SCHEME = /^(\*|http|https|file):\/\//;
const MATCH_LABEL = /^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$/;

const literal = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function _parseMatch(match) {
  if (typeof match !== 'string' || /\s/.test(match)) return null;
  const scheme = MATCH_SCHEME.exec(match);
  if (!scheme) return null;
  const rest = match.slice(scheme[0].length);
  const cut = rest.indexOf('/');
  if (cut === -1) return null;
  const host = rest.slice(0, cut);
  const path = rest.slice(cut);
  if (scheme[1] === 'file') {
    if (host !== '') return null;
  } else if (host === '' || !_matchableHost(host)) {
    return null;
  }
  // A URL parser folds the matched url's host; the pattern's own is folded to
  // match.
  return { scheme: scheme[1], host: host.toLowerCase(), path };
}

function _matchableHost(host) {
  if (host === '*') return true;
  const bare = host.startsWith('*.') ? host.slice(2) : host;
  if (bare === '' || bare.includes('*')) return false;
  return bare.split('.').every((label) => MATCH_LABEL.test(label));
}

// Chrome's host wildcard spans bare host and every subdomain, so
// `*.example.com` is a repeated leading label, not a required one.
function _scopeHost(host) {
  if (host === '') return '';
  if (host === '*') return '[^/]+';
  const wildcard = host.startsWith('*.');
  const labels = (wildcard ? host.slice(2) : host).split('.');
  return (wildcard ? '(?:[^./]+\\.)*' : '')
    + labels.map((label) => literal(label)).join('\\.');
}

// Matched as one string: a parsed comparison would re-decide the pattern's
// grammar, not Chrome's.
function _matchesScope(parsed, identity) {
  const scheme = parsed.scheme === '*' ? 'https?' : parsed.scheme;
  const path = literal(parsed.path).replace(/\\\*/g, '.*');
  return new RegExp('^' + scheme + '://' + _scopeHost(parsed.host) + path
                    + '$').test(identity);
}

// Matched against the page the browser reported, with no fallback: a scoped
// fix whose sender named no page runs nowhere.
function _scopeRefusal(fix, identity) {
  if (fix.match === undefined || fix.match === null) return '';
  // The store refuses a pattern it cannot parse, but its carry-over keeps
  // what the record already holds without re-parsing, and a record is
  // writable from the extension's own pages: an unusable scope reaches the
  // record either way, and reading one as no scope is the widening.
  const parsed = _parseMatch(fix.match);
  if (!parsed) return 'scope ' + fix.match + ' does not parse';
  if (!_matchesScope(parsed, identity)) {
    return 'scoped to ' + fix.match + ' and this page is not it';
  }
  return '';
}
