/* Video stage shared by the batch classroom and LiveKit general meetings:
 * participant tiles, the screen-share tile, and remote audio playback.
 * It renders whatever ClassroomMedia reports and makes no permission decisions.
 * Names are inserted as escaped text, never as raw HTML.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ArckClassroomStage = factory();
}(typeof self !== 'undefined' ? self : this, function () {

  const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  class ClassroomStage {
    /**
     * @param opts { doc, stageEl, media (ClassroomMedia), source ({Camera, Microphone, ScreenShare}), audioParent }
     */
    constructor(opts) {
      this.doc = opts.doc;
      this.stage = opts.stageEl;
      this.media = opts.media;
      this.S = opts.source;
      this.audioParent = opts.audioParent || opts.doc.body;
      this.tiles = new Map();   // key -> { el, avatar, plate, video, attached }
      this.audios = new Map();  // identity -> { el, track }
    }

    _makeTile(key, isScreen, isLocal) {
      const el = this.doc.createElement('div');
      el.className = `cls-tile${isScreen ? ' is-screen' : ''}${isLocal ? ' is-local' : ''}`;
      const avatar = this.doc.createElement('div'); avatar.className = 'cls-avatar';
      const plate = this.doc.createElement('span'); plate.className = 'cls-nameplate';
      el.appendChild(avatar); el.appendChild(plate);
      const tile = { el, avatar, plate, video: null, attached: null, isScreen };
      this.tiles.set(key, tile);
      this.stage.appendChild(el);
      return tile;
    }

    _syncVideo(tile, track) {
      if (tile.attached === track) return;
      if (tile.video) {
        try { tile.attached && tile.attached.detach(tile.video); } catch (_) { /* already gone */ }
        tile.video.remove(); tile.video = null;
      }
      tile.attached = track;
      tile.avatar.style.display = track ? 'none' : '';
      if (!track) return;
      const v = this.doc.createElement('video');
      v.autoplay = true; v.playsInline = true; v.muted = true;      // video elements never carry audio
      if (!tile.isScreen) v.className = 'cls-cam';
      tile.el.insertBefore(v, tile.el.firstChild);
      track.attach(v);
      tile.video = v;
    }

    _syncAudio(p) {
      if (p.isLocal) return;                                       // never play your own microphone back
      const track = p.micOn ? this.media.trackFor(p.identity, this.S.Microphone) : null;
      const cur = this.audios.get(p.identity);
      if (cur && cur.track !== track) { this._dropAudio(p.identity); }
      if (track && !this.audios.has(p.identity)) {
        const el = this.doc.createElement('audio'); el.autoplay = true;
        this.audioParent.appendChild(el);
        track.attach(el);
        this.audios.set(p.identity, { el, track });
      }
    }

    _dropAudio(identity) {
      const a = this.audios.get(identity);
      if (!a) return;
      try { a.track.detach(a.el); } catch (_) { /* already gone */ }
      a.el.remove(); this.audios.delete(identity);
    }

    render(s) {
      const wanted = new Set();
      s.participants.forEach((p) => {
        const camKey = `${p.identity}:cam`;
        wanted.add(camKey);
        const cam = this.tiles.get(camKey) || this._makeTile(camKey, false, p.isLocal);
        cam.el.classList.toggle('is-speaking', p.isSpeaking);
        cam.avatar.textContent = (p.name || '?').charAt(0).toUpperCase();
        cam.plate.innerHTML =
          `${escapeHtml(p.name)}${p.isLocal ? ' (You)' : ''}` +
          `${p.role === 'trainer' ? '<span class="cls-role">Trainer</span>' : ''}` +
          `${p.hand ? ' <i class="fa-solid fa-hand cls-hand" title="Hand raised"></i>' : ''}` +
          `${p.micOn ? '' : '<i class="fa-solid fa-microphone-slash" title="Muted"></i>'}`;
        this._syncVideo(cam, p.cameraOn ? this.media.trackFor(p.identity, this.S.Camera) : null);

        if (p.screenOn) {
          const scrKey = `${p.identity}:screen`;
          wanted.add(scrKey);
          const scr = this.tiles.get(scrKey) || this._makeTile(scrKey, true, p.isLocal);
          scr.plate.textContent = `${p.name}${p.isLocal ? ' (You)' : ''} - screen`;
          this._syncVideo(scr, this.media.trackFor(p.identity, this.S.ScreenShare));
        }
        this._syncAudio(p);
      });

      for (const [key, tile] of this.tiles) {
        if (wanted.has(key)) continue;
        this._syncVideo(tile, null); tile.el.remove(); this.tiles.delete(key);
      }
      const present = new Set(s.participants.map((p) => p.identity));
      for (const id of Array.from(this.audios.keys())) if (!present.has(id)) this._dropAudio(id);

      this.stage.classList.toggle('has-screen', !!s.screenSharer);
      for (const [key, tile] of this.tiles) if (key.endsWith(':screen')) this.stage.insertBefore(tile.el, this.stage.firstChild);
    }

    destroy() {
      for (const tile of this.tiles.values()) { this._syncVideo(tile, null); tile.el.remove(); }
      this.tiles.clear();
      for (const id of Array.from(this.audios.keys())) this._dropAudio(id);
    }
  }

  return { ClassroomStage, escapeHtml };
}));
