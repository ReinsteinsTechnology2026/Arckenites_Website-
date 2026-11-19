/* Recording status + trainer approval for a batch classroom (Phase 2, milestone 2).
 *
 * Talks only to the existing class-run endpoints and owns no DOM. The server is
 * the sole authority: it decides who may approve (the assigned trainer only,
 * reported as `can_control`) and starts recording itself after approval. This
 * module never starts, stops, downloads or plays a recording; for everyone
 * except the assigned trainer it only READS status, and it does not even call
 * the prompt/approval endpoints for them.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ArckClassroomRecording = factory();
}(typeof self !== 'undefined' ? self : this, function () {

  const POLL_MS = 8000;
  const ENDED_STATES = ['ENDED', 'CANCELLED', 'MISSED'];

  class ClassroomRecording {
    /**
     * @param api  object with request(path, {method, body}) -> Promise (ArckAPI)
     * @param runId numeric class-run id
     * @param initial the `recording` object from the join response
     * @param timers optional {setInterval, clearInterval} for tests
     */
    constructor(api, runId, initial, timers) {
      this.api = api;
      this.runId = Number(runId);
      this.status = initial || null;
      this.timers = timers || { setInterval: (f, ms) => setInterval(f, ms), clearInterval: (h) => clearInterval(h) };
      this.listeners = new Set();
      this.timer = null;
      this.promptClaimed = false;
      this.busy = false;
      this.error = '';
      this._wasRecording = !!(initial && initial.is_recording);
    }

    subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
    _emit() { const v = this.view(); this.listeners.forEach((fn) => { try { fn(v); } catch (_) { /* isolate listeners */ } }); }

    /** What the UI should show right now. */
    view() {
      const s = this.status;
      if (!s) return { indicator: 'none', canDecide: false, showDialog: false, notice: '', busy: this.busy, error: this.error };
      const trainer = !!s.can_control;
      const pending = s.approval_status === 'PENDING';
      let indicator = 'none';
      if (s.is_recording) indicator = 'recording';                                    // everyone
      else if (trainer && s.approval_status === 'APPROVED' && ['APPROVED', 'STARTING'].includes(s.recording_state)) indicator = 'starting';
      let notice = '';
      // The server explains WHY (nobody publishing media, recorder lost, ...) and only tells the trainer.
      if (trainer && s.notice) notice = String(s.notice);
      else if (trainer && s.recording_state === 'FAILED') notice = 'Recording could not be started. The class continues without a recording.';
      else if (trainer && s.approval_status === 'DECLINED') notice = 'Recording declined. This class will not be recorded.';
      const canRetry = trainer && !!s.can_retry;  // after a failed recording, the trainer may ask again (consent is re-confirmed)
      return {
        indicator,
        canDecide: trainer && pending,           // controls exist only for the assigned trainer, only while undecided
        canRetry,
        noticeCode: trainer ? (s.notice_code || '') : '',
        showDialog: !!this._dialogOpen,
        notice,
        consentText: s.consent_text || '',
        busy: this.busy,
        error: this.error,
      };
    }

    /** Called once when the trainer enters the room. Opens the dialog only if the server says so. */
    async start() {
      const s = this.status;
      if (s && s.can_control && s.approval_status === 'PENDING' && !this.promptClaimed) {
        this.promptClaimed = true;
        try {
          const p = await this.api.request(`/class-runs/${this.runId}/recording/prompt`, { method: 'POST', body: {} });
          if (p && p.show) {
            this.status = Object.assign({}, this.status, { consent_version: p.consent_version, consent_text: p.consent_text });
            this._dialogOpen = true;
          }
        } catch (_) { /* the persistent review button still lets the trainer decide */ }
      }
      this.timer = this.timers.setInterval(() => this.refresh(), POLL_MS);
      this._emit();
    }

    stop() { if (this.timer !== null) { this.timers.clearInterval(this.timer); this.timer = null; } }

    openDialog() { const v = this.view(); if (v.canDecide || v.canRetry) { this._dialogOpen = true; this._emit(); } }
    closeDialog() { this._dialogOpen = false; this._emit(); }   // closing is NOT declining; the decision stays pending

    async refresh() {
      try {
        const next = await this.api.request(`/class-runs/${this.runId}/recording`);
        this._apply(next);
      } catch (_) { /* keep the last known status through a transient failure */ }
    }

    _apply(next) {
      const prev = this.status;
      this.status = next;
      if (next.approval_status !== 'PENDING' && !next.can_retry) this._dialogOpen = false;
      const now = !!next.is_recording;
      const transition = now && !this._wasRecording ? 'started' : (!now && this._wasRecording ? 'stopped' : '');
      this._wasRecording = now;
      this._emit();
      if (transition && this.onTransition) this.onTransition(transition, prev, next);
      if (ENDED_STATES.includes(next.run_state) && !this._endedNotified) {
        this._endedNotified = true;
        this.stop();
        if (this.onClassEnded) this.onClassEnded(next);
      }
    }

    /** Trainer's explicit decision. Returns true on success. */
    async decide(decision) {
      const s = this.status;
      if (!s || !s.can_control) return false;                          // never even attempted for students/admins
      if (decision !== 'approve' && decision !== 'decline') return false;
      this.busy = true; this.error = ''; this._emit();
      try {
        const next = await this.api.request(`/class-runs/${this.runId}/recording/approval`, {
          method: 'POST', body: { decision, consent_version: s.consent_version },
        });
        this.busy = false;
        this._dialogOpen = false;
        this._apply(next);
        return true;
      } catch (err) {
        this.busy = false;
        this.error = (err && err.detail) || 'Could not save your decision.';
        this._emit();
        return false;
      }
    }
  }

  return { ClassroomRecording, POLL_MS, ENDED_STATES };
}));
