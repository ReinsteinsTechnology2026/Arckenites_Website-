/* ============================================================
   ARCKENITES PORTAL — VIDEO CONFERENCE (self-hosted Jitsi)
   Shared by student-dashboard.html and staff-dashboard.html.

   Points at our own self-hosted Jitsi instance (not the public
   meet.jit.si), since the public service now requires signing in to
   create/start a room — self-hosting removes that wall entirely.

   Opens the call in its own browser tab rather than an embedded
   iframe: embedding hit a chain of iframe-specific issues (Jitsi's
   own hardcoded https/wss URLs, then self-signed-certificate trust
   not being acceptable from inside an iframe at all). A direct tab
   navigation sidesteps all of that — the browser shows its normal
   "proceed anyway" option for the self-signed cert right on the tab
   itself the first time, same as visiting any other local HTTPS dev
   server, and never again after that.

   Update _DOMAIN/_PROTOCOL here when this moves to the production
   server (ideally with a real trusted certificate, removing the
   self-signed-cert step entirely).
   ============================================================ */

const ArckVideo = {
  _DOMAIN: 'localhost:8443',
  _PROTOCOL: 'https:',

  _roomUrl(roomName, displayName) {
    const base = `${this._PROTOCOL}//${this._DOMAIN}/${encodeURIComponent(roomName)}`;
    const hash = `#config.prejoinPageEnabled=false&userInfo.displayName=${encodeURIComponent(JSON.stringify(displayName))}`;
    return base + hash;
  },

  _ensureInvitePanel() {
    let backdrop = document.getElementById('videoInviteBackdrop');
    if (backdrop) return backdrop;

    backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.id = 'videoInviteBackdrop';
    backdrop.innerHTML = `
      <div class="modal">
        <div class="modal-header">
          <h3>Meeting Started</h3>
          <button type="button" class="icon-btn" id="videoInviteCloseBtn" aria-label="Close"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="modal-body">
          <p>Your meeting opened in a new tab. Share this invite with anyone you want to join:</p>
          <div class="video-call-invite" style="border:none; padding:0; margin-top:12px;">
            <input type="text" class="form-control" id="videoInviteInput" readonly>
            <button type="button" class="btn btn-accent" id="videoInviteCopyBtn">Copy Invite</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(backdrop);

    const close = () => backdrop.classList.remove('is-open');
    backdrop.querySelector('#videoInviteCloseBtn').addEventListener('click', close);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && backdrop.classList.contains('is-open')) close(); });

    backdrop.querySelector('#videoInviteCopyBtn').addEventListener('click', async () => {
      const input = backdrop.querySelector('#videoInviteInput');
      try {
        await navigator.clipboard.writeText(input.value);
        const btn = backdrop.querySelector('#videoInviteCopyBtn');
        const original = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = original; }, 1500);
      } catch (_) {
        input.select();
      }
    });

    return backdrop;
  },

  /**
   * Opens the call in a new tab.
   * @param {{roomName: string, displayName: string, subject: string, shareable?: boolean}} opts
   */
  openRoom({ roomName, displayName, subject, shareable = false }) {
    const url = this._roomUrl(roomName, displayName);
    window.open(url, '_blank', 'noopener');

    if (shareable) {
      const backdrop = this._ensureInvitePanel();
      backdrop.querySelector('#videoInviteInput').value = url;
      backdrop.classList.add('is-open');
    }
  },
};
