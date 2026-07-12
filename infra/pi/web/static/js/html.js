// Shared HTML-escaping helper for the small string-templated renderers
// (ops.js, voices.js). One implementation so a future escaping fix (e.g. also
// escaping single quotes for attribute contexts) can't leave a copy behind.
export function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
  ));
}
