/* A student's private class notes with autosave (Phase 2, milestone 3).
 *
 * The server identifies the owner from the session; this module never sends or
 * accepts a user id. Safety rules enforced here:
 *  - editing stays disabled until the saved notes have loaded, so a failed load
 *    can never lead to the server copy being overwritten with a blank page;
 *  - only one save is in flight at a time and the newest text always wins;
 *  - a failed save keeps the text, says so, and retries with backoff.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ArckClassroomNotes = factory();
}(typeof self !== 'undefined' ? self : this, function () {

  const DEBOUNCE_MS = 1200;
  const RETRY_MS = [3000, 6000, 15000, 30000];
  const MAX_CHARS = 20000;

  class ClassroomNotes {
    constructor(api, runId, timers) {
      this.api = api;
      this.path = `/class-runs/${Number(runId)}/notes/me`;
      this.timers = timers || { setTimeout: (f, ms) => setTimeout(f, ms), clearTimeout: (h) => clearTimeout(h) };
      this.content = '';
      this.loaded = false;
      this.status = 'loading';          // loading | load-failed | saved | dirty | saving | error
      this.error = '';
      this.dirty = false;
      this.inFlight = false;
      this.timer = null;
      this.failures = 0;
      this.listeners = new Set();
    }

    subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
    _emit() { const s = this.state(); this.listeners.forEach((fn) => { try { fn(s); } catch (_) { /* isolate */ } }); }
    state() { return { content: this.content, status: this.status, error: this.error, canEdit: this.loaded, tooLong: this.content.length > MAX_CHARS }; }
    hasUnsaved() { return this.dirty || this.inFlight; }

    async load() {
      this.status = 'loading'; this.error = ''; this._emit();
      try {
        const note = await this.api.request(this.path);
        if (!this.dirty) this.content = note.content || '';
        this.loaded = true;
        this.status = this.dirty ? 'dirty' : 'saved';
      } catch (err) {
        this.status = 'load-failed';
        this.error = (err && err.detail) || 'Your notes could not be loaded. Editing is paused so nothing is overwritten.';
      }
      this._emit();
    }

    edit(text) {
      if (!this.loaded) return false;
      this.content = String(text);
      this.dirty = true;
      if (this.content.length > MAX_CHARS) { this.status = 'error'; this.error = `Notes can be at most ${MAX_CHARS} characters. Shorten them to keep saving.`; this._emit(); return true; }
      this.status = 'dirty'; this.error = '';
      this._schedule(DEBOUNCE_MS);
      this._emit();
      return true;
    }

    _schedule(ms) {
      if (this.timer !== null) this.timers.clearTimeout(this.timer);
      this.timer = this.timers.setTimeout(() => { this.timer = null; this.save(); }, ms);
    }

    /** Save now (also used when leaving the page). Resolves true when nothing is left unsaved. */
    async save() {
      if (this.timer !== null) { this.timers.clearTimeout(this.timer); this.timer = null; }
      if (this.inFlight || !this.dirty || !this.loaded || this.content.length > MAX_CHARS) return !this.dirty;
      this.inFlight = true; this.status = 'saving'; this._emit();
      const sent = this.content;
      try {
        await this.api.request(this.path, { method: 'POST', body: { content: sent } });
        this.inFlight = false; this.failures = 0;
        if (this.content === sent) { this.dirty = false; this.status = 'saved'; this.error = ''; this._emit(); return true; }
        this.status = 'dirty'; this._emit();            // typed more while saving: go again
        return this.save();
      } catch (err) {
        this.inFlight = false;
        this.status = 'error';
        this.error = (err && err.detail) || 'Could not save your notes. They are kept here and will retry.';
        this._schedule(RETRY_MS[Math.min(this.failures, RETRY_MS.length - 1)]);
        this.failures += 1;
        this._emit();
        return false;
      }
    }

    retryNow() { return this.loaded ? this.save() : this.load(); }
    stop() { if (this.timer !== null) { this.timers.clearTimeout(this.timer); this.timer = null; } }
  }

  return { ClassroomNotes, DEBOUNCE_MS, RETRY_MS, MAX_CHARS };
}));
