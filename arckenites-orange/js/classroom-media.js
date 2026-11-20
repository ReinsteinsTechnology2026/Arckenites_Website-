/* Arckenites classroom media controller (Phase 2, milestone 1).
 *
 * Wraps a LiveKit Room: microphone, camera, screen share and the participant
 * list. It owns no DOM and receives the LiveKit SDK namespace (`lk`) from the
 * caller, so it can be unit-tested with a fake room and never needs the network.
 * Permissions are NOT decided here: the server-issued token is the only
 * authority (students can publish media but not data; only the trainer is a
 * room admin). This module just reflects what the room reports.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ArckClassroomMedia = factory();
}(typeof self !== 'undefined' ? self : this, function () {

  function parseRole(metadata) {
    try {
      const role = JSON.parse(metadata || '{}').role;
      return role === 'trainer' ? 'trainer' : 'student';
    } catch (_) {
      return 'student';
    }
  }

  function friendlyMediaError(err, what) {
    const name = err && err.name;
    if (name === 'NotAllowedError' || name === 'SecurityError' || (err && /permission/i.test(err.message || ''))) {
      return `Your browser blocked ${what}. Allow access in the address bar and try again.`;
    }
    if (name === 'NotFoundError' || name === 'OverconstrainedError') return `No ${what} was found on this device.`;
    if (name === 'AbortError' && what === 'screen sharing') return '';   // user closed the picker: not an error
    if (name === 'NotReadableError') return `Your ${what} is being used by another application.`;
    return `Could not start ${what}.`;
  }

  class ClassroomMedia {
    /** @param lk the LiveKit client namespace (window.LivekitClient) */
    constructor(lk, options = {}) {
      this.lk = lk;
      this.room = options.room || new lk.Room({ adaptiveStream: true, dynacast: true });
      this.listeners = new Set();
      this.lastError = '';
      this.connected = false;
      this._wired = false;
    }

    subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
    _emit() { const s = this.state(); this.listeners.forEach((fn) => { try { fn(s); } catch (_) { /* a bad listener must not break the call */ } }); }

    async connect(url, token) {
      this._wire();
      await this.room.connect(url, token);
      this.connected = true;
      this._emit();
    }

    disconnect() {
      try { this.room.disconnect(); } catch (_) { /* already gone */ }
      this.connected = false;
      this._emit();
    }

    _wire() {
      if (this._wired) return;
      this._wired = true;
      const E = this.lk.RoomEvent;
      [E.ParticipantConnected, E.ParticipantDisconnected, E.TrackMuted, E.TrackUnmuted,
        E.TrackPublished, E.TrackUnpublished, E.TrackSubscribed, E.TrackUnsubscribed,
        E.LocalTrackPublished, E.LocalTrackUnpublished, E.ActiveSpeakersChanged,
        E.ParticipantMetadataChanged, E.ParticipantAttributesChanged, E.Reconnecting, E.Reconnected,
      ].filter(Boolean).forEach((evt) => this.room.on(evt, () => this._emit()));
      this.room.on(E.Disconnected, () => { this.connected = false; this._emit(); });
    }

    /* ---------- local controls ---------- */
    async _toggle(kind, label, enable) {
      this.lastError = '';
      const lp = this.room.localParticipant;
      try {
        await lp[kind](enable);
      } catch (err) {
        this.lastError = friendlyMediaError(err, label);
      }
      this._emit();
      return this.lastError === '';
    }
    setMicrophone(on) { return this._toggle('setMicrophoneEnabled', 'the microphone', on); }
    setCamera(on) { return this._toggle('setCameraEnabled', 'the camera', on); }
    setScreenShare(on) { return this._toggle('setScreenShareEnabled', 'screen sharing', on); }

    toggleMicrophone() { return this.setMicrophone(!this.room.localParticipant.isMicrophoneEnabled); }
    toggleCamera() { return this.setCamera(!this.room.localParticipant.isCameraEnabled); }
    toggleScreenShare() { return this.setScreenShare(!this.room.localParticipant.isScreenShareEnabled); }

    /* ---------- read side ---------- */
    _describe(p, isLocal) {
      const S = this.lk.Track.Source;
      const pub = (src) => (p.getTrackPublication ? p.getTrackPublication(src) : undefined);
      const on = (src) => { const t = pub(src); return !!(t && t.track && !t.isMuted); };
      return {
        identity: p.identity,
        name: p.name || p.identity,
        isLocal,
        role: parseRole(p.metadata),
        isSpeaking: !!p.isSpeaking,
        hand: !!(p.attributes && p.attributes.hand === '1'),   // set by the server on the student's behalf
        micOn: on(S.Microphone),
        cameraOn: on(S.Camera),
        screenOn: on(S.ScreenShare),
      };
    }

    state() {
      const lp = this.room.localParticipant;
      const remotes = Array.from((this.room.remoteParticipants || new Map()).values());
      const participants = [this._describe(lp, true), ...remotes.map((p) => this._describe(p, false))];
      return {
        connected: this.connected,
        reconnecting: this.room.state === 'reconnecting',
        micOn: !!lp.isMicrophoneEnabled,
        cameraOn: !!lp.isCameraEnabled,
        screenOn: !!lp.isScreenShareEnabled,
        error: this.lastError,
        participants,
        screenSharer: (participants.find((p) => p.screenOn) || {}).identity || null,
      };
    }

    /** The live MediaStreamTrack wrapper for a participant/source, or null. */
    trackFor(identity, source) {
      const lp = this.room.localParticipant;
      const p = identity === lp.identity ? lp : (this.room.remoteParticipants || new Map()).get(identity);
      const pub = p && p.getTrackPublication ? p.getTrackPublication(source) : null;
      return pub && pub.track && !pub.isMuted ? pub.track : null;
    }
  }

  return { ClassroomMedia, parseRole, friendlyMediaError };
}));
