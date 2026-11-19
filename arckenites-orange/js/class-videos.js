/* ============================================================
   ARCKENITES PORTAL — CLASS VIDEOS (shared by the student, trainer
   and admin pages)

   Two kinds of entry share one list:
     - LINK       a posted web link (the original behaviour)
     - RECORDING  an Arckenites class recording. It has no URL: it plays in
                  an in-page player through an authorized stream, so there is
                  never a storage link to copy or share.

   The player offers no download button and no share link. That is an access
   control, not a guarantee: nothing in a browser can stop someone recording
   their own screen, and the page does not claim otherwise.
   ============================================================ */

const ArckClassVideos = (() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // Only web links may become an href: a stored "javascript:" link must never run.
  const safeHref = (url) => (typeof url === 'string' && /^https?:\/\//i.test(url.trim())) ? url.trim() : null;

  const formatDuration = (seconds) => {
    if (seconds == null) return '—';
    const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
    return h ? `${h}h ${String(m).padStart(2, '0')}m` : (m ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`);
  };

  const isRecording = (v) => v && v.source === 'RECORDING' && !!v.recording_id;

  const linkCellHtml = (v, label = 'Open') => {
    const href = safeHref(v.video_url);
    return href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : '—';
  };

  const watchButtonHtml = (v) =>
    `<button type="button" class="btn btn-accent" style="padding:4px 14px;" data-watch-recording="${esc(v.recording_id)}" data-watch-title="${esc(v.title)}"><i class="fa-solid fa-play"></i> Watch</button>`;

  /* ---------- the in-page player ---------- */
  let modal = null, refreshTimer = null;
  const REFRESH_EVERY_MS = 20 * 60 * 1000;   // well inside the server's 30 minute token life

  const ensureModal = () => {
    if (modal) return modal;
    modal = document.createElement('div');
    modal.className = 'modal-backdrop';
    modal.innerHTML = `
      <div class="modal" style="max-width:920px; width:min(920px, 96vw);">
        <div class="modal-header">
          <h3 id="classVideoTitle"></h3>
          <button type="button" class="icon-btn" id="classVideoClose" aria-label="Close"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="modal-body">
          <video id="classVideoPlayer" controls playsinline preload="metadata" disablePictureInPicture
                 controlsList="nodownload noremoteplayback" style="width:100%; max-height:70vh; background:#000; border-radius:8px;"></video>
          <p id="classVideoMessage" style="display:none; margin-top:12px;"></p>
        </div>
      </div>`;
    document.body.appendChild(modal);
    const video = modal.querySelector('#classVideoPlayer');
    video.addEventListener('contextmenu', (e) => e.preventDefault());
    modal.querySelector('#classVideoClose').addEventListener('click', close);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && modal.classList.contains('is-open')) close(); });
    video.addEventListener('error', () => {
      if (modal.classList.contains('is-open')) showMessage('This recording could not be played. It may be temporarily unavailable, or your access may have changed.');
    });
    return modal;
  };

  const showMessage = (text) => {
    const el = modal.querySelector('#classVideoMessage');
    el.textContent = text || '';
    el.style.display = text ? 'block' : 'none';
  };

  const streamUrl = (path) => new URL(path, new URL(API_BASE, window.location.href).origin).toString();

  const fetchStreamUrl = async (recordingId) => {
    const res = await ArckAPI.request(`/recordings/${encodeURIComponent(recordingId)}/playback-token`, { method: 'POST', body: {} });
    return streamUrl(res.stream_url);
  };

  const close = () => {
    if (!modal) return;
    clearInterval(refreshTimer);
    const video = modal.querySelector('#classVideoPlayer');
    video.pause();
    video.removeAttribute('src');
    video.load();                      // drop the stream (and its token) from the page
    modal.classList.remove('is-open');
  };

  const watch = async (recordingId, title) => {
    ensureModal();
    const video = modal.querySelector('#classVideoPlayer');
    modal.querySelector('#classVideoTitle').textContent = title || 'Class recording';
    showMessage('');
    modal.classList.add('is-open');
    try {
      video.src = await fetchStreamUrl(recordingId);
    } catch (err) {
      showMessage(err.status === 404 ? "This recording isn't available to you." : 'Could not start playback. Please try again.');
      return;
    }
    video.play().catch(() => { /* the user can press play */ });
    clearInterval(refreshTimer);
    refreshTimer = setInterval(async () => {
      // Swap in a fresh token without losing the viewer's place.
      try {
        const next = await fetchStreamUrl(recordingId);
        const at = video.currentTime, wasPlaying = !video.paused;
        video.src = next;
        video.addEventListener('loadedmetadata', () => { video.currentTime = at; if (wasPlaying) video.play().catch(() => {}); }, { once: true });
      } catch (_) { /* the next tick tries again; an expired token shows the message above */ }
    }, REFRESH_EVERY_MS);
  };

  // One delegated handler per page: any element carrying data-watch-recording.
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-watch-recording]');
    if (btn) watch(btn.dataset.watchRecording, btn.dataset.watchTitle);
  });

  return { esc, safeHref, formatDuration, isRecording, linkCellHtml, watchButtonHtml, watch };
})();
