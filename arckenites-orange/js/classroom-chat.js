/* Classroom chat (Phase 2, milestone 3).
 *
 * Reuses the existing batch group chat: history/sending go through the
 * role-scoped REST endpoints (`/students/me/batches/{id}/chat/messages` or
 * `/staff/me/batches/{id}/chat/messages`), and live delivery comes from the
 * existing per-user chat WebSocket (`batch_message` events). The server decides
 * who may read or post (the batch's trainer and its enrolled students); this
 * module owns no DOM and never trusts a socket frame for another batch.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ArckClassroomChat = factory();
}(typeof self !== 'undefined' ? self : this, function () {

  const MAX_CHARS = 2000;
  const BACKOFF = [1000, 2000, 5000, 10000, 30000];

  class ClassroomChat {
    /**
     * @param api ArckAPI-like { request(path, opts) }
     * @param opts { batchId, role: 'student'|'staff', openSocket(onMessage,onOpen,onClose) -> {close()}, timers }
     */
    constructor(api, opts) {
      if (opts.role !== 'student' && opts.role !== 'staff') throw new Error('Chat is only available to the trainer and enrolled students.');
      this.api = api;
      this.batchId = Number(opts.batchId);
      this.base = `/${opts.role === 'student' ? 'students' : 'staff'}/me/batches/${this.batchId}/chat/messages`;
      this.openSocket = opts.openSocket;
      this.timers = opts.timers || { setTimeout: (f, ms) => setTimeout(f, ms), clearTimeout: (h) => clearTimeout(h) };
      this.messages = [];
      this.ids = new Set();
      this.status = 'connecting';       // connecting | connected | reconnecting | offline
      this.error = '';
      this.sending = false;
      this.loaded = false;
      this.listeners = new Set();
      this.attempt = 0;
      this.socket = null;
      this.retry = null;
      this.closed = false;
    }

    subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
    _emit() { const s = this.state(); this.listeners.forEach((fn) => { try { fn(s); } catch (_) { /* isolate */ } }); }
    state() { return { messages: this.messages.slice(), status: this.status, error: this.error, sending: this.sending, loaded: this.loaded }; }

    _add(m) {
      if (!m || m.batch_id !== this.batchId || this.ids.has(m.id)) return false;   // other batch / duplicate (WS echo of own POST)
      this.ids.add(m.id);
      this.messages.push(m);
      this.messages.sort((a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : a.id - b.id));
      return true;
    }

    async loadHistory() {
      try {
        const rows = await this.api.request(this.base);
        rows.forEach((m) => this._add(m));
        this.loaded = true;
        this.error = '';
      } catch (err) {
        this.error = (err && err.detail) || 'Could not load earlier messages.';
      }
      this._emit();
    }

    async start() {
      await this.loadHistory();
      this._connect();
    }

    _connect() {
      if (this.closed) return;
      this.status = this.attempt === 0 ? 'connecting' : 'reconnecting';
      this._emit();
      this.socket = this.openSocket(
        (frame) => this._onFrame(frame),
        () => { this.attempt = 0; this.status = 'connected'; this._emit(); if (!this.loaded || this._gap) { this._gap = false; this.loadHistory(); } },
        () => this._onClose(),
      );
    }

    _onFrame(frame) {
      if (!frame || frame.type !== 'batch_message' || Number(frame.batch_id) !== this.batchId) return;
      if (this._add(frame.message)) { this._emit(); if (this.onIncoming) this.onIncoming(frame.message); }
    }

    _onClose() {
      if (this.closed) return;
      this._gap = true;                              // we may have missed messages while disconnected
      const delay = BACKOFF[Math.min(this.attempt, BACKOFF.length - 1)];
      this.attempt += 1;
      this.status = this.attempt > BACKOFF.length ? 'offline' : 'reconnecting';
      this._emit();
      this.retry = this.timers.setTimeout(() => this._connect(), delay);
    }

    /** Try again immediately (the "Reconnect" button). */
    reconnectNow() {
      if (this.closed) return;
      if (this.retry !== null) this.timers.clearTimeout(this.retry);
      this.attempt = 0;
      try { this.socket && this.socket.close(); } catch (_) { /* already closed */ }
      this._connect();
    }

    stop() {
      this.closed = true;
      if (this.retry !== null) this.timers.clearTimeout(this.retry);
      try { this.socket && this.socket.close(); } catch (_) { /* already closed */ }
    }

    /** Returns { ok, error }. The caller keeps the typed text when ok is false. */
    async send(text) {
      const body = String(text || '').trim();
      if (!body) return { ok: false, error: '' };
      if (body.length > MAX_CHARS) return { ok: false, error: `Messages can be at most ${MAX_CHARS} characters.` };
      if (this.sending) return { ok: false, error: '' };
      this.sending = true; this.error = ''; this._emit();
      try {
        const msg = await this.api.request(this.base, { method: 'POST', body: { message: body } });
        this._add(msg);
        this.sending = false; this._emit();
        return { ok: true, error: '' };
      } catch (err) {
        this.sending = false;
        this.error = (err && err.detail) || 'Your message was not sent. Check your connection and try again.';
        this._emit();
        return { ok: false, error: this.error };
      }
    }
  }

  return { ClassroomChat, MAX_CHARS, BACKOFF };
}));
