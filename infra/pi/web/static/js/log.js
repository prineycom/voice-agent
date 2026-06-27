// Timestamped, newest-first log helper. Prepends to a target element's
// textContent and mirrors to the console under a [harness] tag.
export function createLog(logEl) {
  function log(msg) {
    const t = new Date().toLocaleTimeString();
    logEl.textContent = `${t}  ${msg}\n` + logEl.textContent;
    console.log('[harness]', msg);
  }
  return { log };
}
