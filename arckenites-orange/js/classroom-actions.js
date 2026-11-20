/* In-class actions: raise hand and trainer moderation (Phase 2).
 *
 * Every action is a request to the backend, which decides whether the caller may
 * do it (a student only changes their own hand; only the assigned trainer can
 * mute, remove or lower a hand) and then acts on the class's own media room.
 * This module never talks to the media server and never decides permissions;
 * it only shapes requests and reports errors in plain language.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ArckClassroomActions = factory();
}(typeof self !== 'undefined' ? self : this, function () {

  const FRIENDLY = {
    403: 'You are not allowed to do that.',
    404: 'That person is no longer in this class.',
    409: 'This class is not running.',
    429: 'Too many requests. Please wait a moment.',
    503: 'The classroom is temporarily unavailable. Please try again.',
  };

  function describe(err) {
    return (err && err.detail) || FRIENDLY[err && err.status] || 'That did not work. Please try again.';
  }

  class ClassroomActions {
    constructor(api, runId) {
      this.api = api;
      this.base = `/class-runs/${Number(runId)}`;
    }

    async _post(path, body) {
      try {
        await this.api.request(`${this.base}${path}`, { method: 'POST', body });
        return { ok: true, error: '' };
      } catch (err) {
        return { ok: false, error: describe(err) };
      }
    }

    raiseHand(raised) { return this._post('/hand', { raised: !!raised }); }
    mute(identity) { return this._post('/moderation/mute', { identity }); }
    kick(identity) { return this._post('/moderation/kick', { identity }); }
    lowerHand(identity) { return this._post('/moderation/lower-hand', { identity }); }
    /** Trainer only (the server refuses everyone else): closes the class for everyone and finalizes any recording. */
    endClass() { return this._post('/end', {}); }
  }

  /** Participants whose hand just went up (not the caller's own). Pure. */
  function newHands(prev, next) {
    const before = new Set(((prev && prev.participants) || []).filter((p) => p.hand).map((p) => p.identity));
    return ((next && next.participants) || []).filter((p) => p.hand && !p.isLocal && !before.has(p.identity));
  }

  /** Raised hands in the order they appear (the trainer's queue). */
  function raisedHands(state) {
    return ((state && state.participants) || []).filter((p) => p.hand);
  }

  return { ClassroomActions, newHands, raisedHands, describe };
}));
