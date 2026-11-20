/* Chooses between the LiveKit classroom and the existing Jitsi meeting room.
 *
 * The server decides (GET /classroom/availability): the LiveKit classroom is used only when it is
 * switched on, its routes are registered and the media server is configured. EVERYTHING else -
 * flag off, 401/403/404/5xx, a network error, a timeout, an odd response - keeps people on the
 * existing meeting room, so a failed check can never strand a trainer or student. Only a
 * successful `true` answer is cached (briefly); a failure is never cached.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ArckClassroom = factory();
}(typeof self !== 'undefined' ? self : this, function () {

  const CLASSROOM_PAGE = 'classroom.html';
  const JITSI_PAGE = 'meeting-room.html';
  const CACHE_KEY = 'ak_classroom_availability';
  const CACHE_MS = 60000;
  const TIMEOUT_MS = 4000;

  function readCache(storage, now) {
    try {
      const c = JSON.parse(storage.getItem(CACHE_KEY) || 'null');
      if (c && typeof c.v === 'boolean' && now - c.t < CACHE_MS) return c.v;
    } catch (_) { /* unavailable or corrupt: ask the server */ }
    return null;
  }
  function writeCache(storage, value, now) {
    try { storage.setItem(CACHE_KEY, JSON.stringify({ v: value, t: now })); } catch (_) { /* optional */ }
  }

  /** True only when the server says the classroom is available. Never throws. */
  async function isClassroomEnabled(api, opts = {}) {
    const storage = opts.storage !== undefined ? opts.storage : (typeof sessionStorage !== 'undefined' ? sessionStorage : null);
    const now = (opts.now || Date.now)();
    if (storage) { const cached = readCache(storage, now); if (cached !== null) return cached; }
    let timer;
    try {
      const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), opts.timeoutMs || TIMEOUT_MS); });
      const res = await Promise.race([api.request('/classroom/availability'), timeout]);
      const enabled = !!res && res.classroom_enabled === true;
      if (storage) writeCache(storage, enabled, now);
      return enabled;
    } catch (_) {
      return false;      // any failure = existing behaviour
    } finally {
      clearTimeout(timer);
    }
  }

  function classUrl(batchId, enabled) {
    return `${enabled ? CLASSROOM_PAGE : JITSI_PAGE}?batch=${encodeURIComponent(String(batchId))}`;
  }
  function jitsiUrl(batchId) { return classUrl(batchId, false); }

  async function joinUrl(api, batchId, opts) {
    return classUrl(batchId, await isClassroomEnabled(api, opts));
  }

  /** Sends the user to the right room for this batch. */
  async function goToClass(api, batchId, navigate, opts) {
    const go = navigate || ((url) => { window.location.href = url; });
    go(await joinUrl(api, batchId, opts));
  }

  /** Admins do not take a seat in a LiveKit class (trainer and enrolled students only). While the
   *  classroom is off they keep their existing Jitsi "Join" control; once it is on, it is replaced. */
  async function hideAdminJoinWhenClassroomOn(api, scope, opts) {
    if (!(await isClassroomEnabled(api, opts))) return false;
    const doc = scope || (typeof document !== 'undefined' ? document : null);
    if (!doc) return false;
    doc.querySelectorAll('[data-admin-class-join]').forEach((el) => {
      if (el.tagName === 'BUTTON') { el.style.display = 'none'; return; }
      const note = (el.ownerDocument || document).createElement('span');
      note.className = 'muted';
      note.title = 'Admins do not join class sessions';
      note.textContent = 'Trainer & students';
      el.replaceWith(note);
    });
    return true;
  }

  return { isClassroomEnabled, joinUrl, goToClass, classUrl, jitsiUrl, hideAdminJoinWhenClassroomOn, CACHE_KEY, CACHE_MS, TIMEOUT_MS, CLASSROOM_PAGE, JITSI_PAGE };
}));
