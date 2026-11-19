/* Arckenites batch classroom page (Phase 2): join, media controls, participant
 * tiles and list (milestone 1); recording indicator and trainer approval
 * (milestone 2). Chat, notes, hand-raise and moderation arrive in later
 * milestones. General meetings stay on meeting-room.html. */
const clsEscape = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const batchId = params.get('batch');
  const runId = params.get('run');
  // The LiveKit classroom may be switched off (or its server unavailable): a link to this page
  // must then land in the existing meeting room instead of an error.
  if (batchId && !runId && (typeof ArckClassroom === 'undefined' || !(await ArckClassroom.isClassroomEnabled(ArckAPI)))) {
    window.location.replace(`meeting-room.html?batch=${encodeURIComponent(batchId)}`);
    return;
  }
  const body = $('clsPrejoinBody');

  if (!/^\d+$/.test(batchId || runId || '')) {
    body.innerHTML = '<h2>Invalid classroom link</h2><p>This classroom link is missing or malformed.</p>';
    return;
  }

  let user;
  try { user = await ArckAuth.getCurrentUser(); } catch (_) { return; } // auth.js already redirected to login

  const DASHBOARD = { admin: 'admin-dashboard.html', staff: 'staff-dashboard.html', student: 'student-dashboard.html' };
  const home = DASHBOARD[user.role] || 'index.html';
  [['clsBackLink', true], ['clsEndedLink', false]].forEach(([id, hidden]) => {
    const el = $(id); if (!el) return; el.href = home; if (hidden) el.style.display = '';
  });

  const lk = window.LivekitClient;
  if (!lk) {
    body.innerHTML = '<h2>Can\'t load the classroom</h2><p>The video library could not be loaded. Check your connection and refresh.</p>';
    return;
  }

  const media = new ArckClassroomMedia.ClassroomMedia(lk);
  const showError = (message) => {
    const el = $('clsPrejoinError');
    if (el) { el.textContent = message; el.style.display = 'block'; }
    else body.innerHTML = `<h2>Can't join this classroom</h2><p>${clsEscape(message)}</p>`;
  };

  body.innerHTML = `
    <h2>Ready to join?</h2>
    <p>You're signed in as ${clsEscape(user.full_name)}.</p>
    <button type="button" class="btn btn-accent" id="clsJoinBtn" style="margin-top:16px; width:100%;"><i class="fa-solid fa-video"></i> Join Classroom</button>
    <div class="meet-prejoin-error" id="clsPrejoinError" style="display:none;"></div>`;
  $('clsJoinBtn').addEventListener('click', join);

  async function join() {
    const btn = $('clsJoinBtn');
    btn.disabled = true;
    try {
      const path = batchId ? `/class-runs/batch/${encodeURIComponent(batchId)}/join` : `/class-runs/${encodeURIComponent(runId)}/join`;
      const info = await ArckAPI.request(path, { method: 'POST', body: {} });
      await media.connect(info.livekit_url, info.token);
      $('clsPrejoin').style.display = 'none';
      $('clsRoom').style.display = 'flex';
      $('clsTitle').textContent = info.display_name ? `Classroom - ${info.display_name}` : 'Classroom';
      media.subscribe(render);
      render(media.state());
      startRecordingUi(info);
      startActionsUi(info);
      startChatUi(info);
      startNotesUi(info);
      // Join with media off; people turn on what they want (and the browser asks permission on their click).
    } catch (err) {
      showError((err && err.detail) || (err && err.message) || 'Something went wrong.');
      if (batchId && err && [404, 502, 503].includes(err.status)) {
        // Classroom or media server unavailable: offer the existing meeting room (DOM-built; no HTML from the URL).
        const el = $('clsPrejoinError');
        const link = document.createElement('a');
        link.href = `meeting-room.html?batch=${encodeURIComponent(batchId)}`;
        link.textContent = 'Join the classic meeting room instead';
        link.style.display = 'block';
        link.style.marginTop = '8px';
        el.appendChild(link);
      }
      btn.disabled = false;
    }
  }

  /* ---------- toolbar ---------- */
  const note = (msg) => {
    const t = $('clsToast'); if (!msg) return;
    t.textContent = msg; t.classList.add('is-visible');
    clearTimeout(note.timer); note.timer = setTimeout(() => t.classList.remove('is-visible'), 5000);
  };
  const run = async (action) => { await action(); const s = media.state(); if (s.error) note(s.error); };
  $('clsToolMic').addEventListener('click', () => run(() => media.toggleMicrophone()));
  $('clsToolCamera').addEventListener('click', () => run(() => media.toggleCamera()));
  $('clsToolShare').addEventListener('click', () => run(() => media.toggleScreenShare()));
  // One exit path for Leave, End class and "the trainer ended the class".
  function leaveClassroom(message) {
    media.disconnect();
    if (recorder) recorder.stop();
    stageView.destroy();
    if (chat) chat.stop();
    if (notes) { notes.save(); notes.stop(); }
    $('clsRoom').style.display = 'none';
    if (message) $('clsEndedText').textContent = message;
    $('clsEnded').style.display = 'flex';
  }
  $('clsToolLeave').addEventListener('click', () => {
    if (!window.confirm('Leave this classroom?')) return;
    leaveClassroom();
  });
  const side = $('clsSidepanel');
  let activePanel = 'participants';
  const openPanel = (name) => {
    activePanel = name;
    side.classList.add('is-open');
    side.querySelectorAll('.meet-sidepanel-tab').forEach((t) => t.classList.toggle('is-active', t.dataset.panel === name));
    side.querySelectorAll('[data-panel-body]').forEach((p) => { p.style.display = p.dataset.panelBody === name ? 'flex' : 'none'; });
    if (name === 'chat') { unreadChat = 0; renderChatBadge(); const m = $('clsChatMessages'); m.scrollTop = m.scrollHeight; }
  };
  side.querySelectorAll('.meet-sidepanel-tab').forEach((t) => t.addEventListener('click', () => openPanel(t.dataset.panel)));
  $('clsToolChat').addEventListener('click', () => openPanel('chat'));
  $('clsToolParticipants').addEventListener('click', () => openPanel('participants'));
  $('clsSidepanelClose').addEventListener('click', () => side.classList.remove('is-open'));
  if (window.innerWidth > 860) side.classList.add('is-open');
  // Browsers may block remote audio until a user gesture.
  document.addEventListener('click', () => { try { media.room.startAudio(); } catch (_) { /* nothing to start */ } });

  const setBtn = (id, on, iconOn, iconOff) => {
    const b = $(id);
    b.classList.toggle('is-active', on); b.classList.toggle('is-off', !on);
    b.setAttribute('aria-pressed', String(on));
    b.firstElementChild.className = `fa-solid ${on ? iconOn : iconOff}`;
  };

  /* ---------- recording: indicator for everyone, approval only for the assigned trainer ---------- */
  let recorder = null;
  let lastNotice = '';
  function renderRecording(v) {
    const badge = $('clsRecordingBadge');
    badge.style.display = v.indicator === 'none' ? 'none' : 'inline-flex';
    $('clsRecordingLabel').textContent = v.noticeCode === 'waiting_for_media' ? 'Waiting for camera or mic…'
      : (v.indicator === 'starting' ? 'Recording starting…' : 'Recording');
    const decide = v.canDecide || v.canRetry;
    $('clsReviewRecording').style.display = decide ? '' : 'none';
    $('clsReviewRecording').lastChild.textContent = v.canRetry ? ' Retry recording' : ' Recording decision';
    $('clsConsent').style.display = decide && v.showDialog ? 'flex' : 'none';
    $('clsDeclineBtn').style.display = v.canRetry ? 'none' : '';   // approval already stands; retrying cannot "decline"
    $('clsApproveBtn').lastChild.textContent = v.canRetry ? 'Retry recording' : 'Approve & start recording';
    $('clsConsentText').textContent = v.consentText;
    const err = $('clsConsentError');
    err.textContent = v.error; err.style.display = v.error ? 'block' : 'none';
    $('clsApproveBtn').disabled = v.busy; $('clsDeclineBtn').disabled = v.busy;
    if (v.notice && v.notice !== lastNotice) note(v.notice);   // say each message once, not on every poll
    lastNotice = v.notice || '';
  }
  function startRecordingUi(info) {
    recorder = new ArckClassroomRecording.ClassroomRecording(ArckAPI, info.run_id, info.recording);
    recorder.onClassEnded = () => leaveClassroom(isTrainer ? 'This class has ended.' : 'Your trainer ended this class.');
    recorder.onTransition = (what) => note(what === 'started' ? 'This class is now being recorded.' : 'Recording has stopped.');
    recorder.subscribe(renderRecording);
    $('clsApproveBtn').addEventListener('click', () => recorder.decide('approve'));
    $('clsDeclineBtn').addEventListener('click', () => recorder.decide('decline'));
    $('clsConsentClose').addEventListener('click', () => recorder.closeDialog());
    $('clsReviewRecording').addEventListener('click', () => recorder.openDialog());
    recorder.start();
  }

  /* ---------- chat: the batch group chat (trainer + enrolled students) ---------- */
  let chat = null;
  let unreadChat = 0;
  const renderChatBadge = () => {
    const b = $('clsChatBadge');
    b.textContent = unreadChat > 9 ? '9+' : String(unreadChat);
    b.style.display = unreadChat ? '' : 'none';
  };
  function openChatSocket(onMessage, onOpen, onClose) {
    const base = new URL(API_BASE, window.location.href);
    const proto = base.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${base.host}${base.pathname}/chat/ws?token=${encodeURIComponent(ArckAPI.getToken())}`);
    ws.onopen = onOpen;
    ws.onmessage = (evt) => { try { onMessage(JSON.parse(evt.data)); } catch (_) { /* ignore malformed frames */ } };
    ws.onclose = onClose;
    ws.onerror = () => { try { ws.close(); } catch (_) { /* already closed */ } };
    return ws;
  }
  function renderChat(s) {
    const box = $('clsChatMessages');
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.innerHTML = s.messages.map((m) => `
      <div class="meet-chat-msg${m.sender_id === user.id ? ' is-mine' : ''}">
        <strong>${clsEscape(m.sender_name)}<time>${clsEscape(m.created_at ? new Date(m.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '')}</time></strong>${clsEscape(m.message)}
      </div>`).join('') || '<div class="meet-chat-msg is-system">No messages yet.</div>';
    if (atBottom) box.scrollTop = box.scrollHeight;
    const conn = $('clsChatConn');
    const text = { connecting: 'Connecting to chat…', reconnecting: 'Chat disconnected. Reconnecting…', offline: 'Chat is offline.' }[s.status] || '';
    conn.style.display = text ? '' : 'none';
    conn.innerHTML = text ? `${clsEscape(text)}${s.status === 'offline' ? ' <button type="button" class="btn btn-primary-outline" id="clsChatReconnect">Reconnect</button>' : ''}` : '';
    const reconnect = $('clsChatReconnect');
    if (reconnect) reconnect.addEventListener('click', () => chat.reconnectNow());
    const err = $('clsChatError');
    err.textContent = s.error; err.style.display = s.error ? 'block' : 'none';
    $('clsChatSend').disabled = s.sending;
  }
  function startChatUi(info) {
    if (!['student', 'staff'].includes(user.role)) return;      // admins have no seat in a class
    chat = new ArckClassroomChat.ClassroomChat(ArckAPI, { batchId: info.batch_id, role: user.role, openSocket: openChatSocket });
    chat.onIncoming = (m) => { if (m.sender_id !== user.id && activePanel !== 'chat') { unreadChat += 1; renderChatBadge(); } };
    chat.subscribe(renderChat);
    $('clsChatForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const input = $('clsChatInput');
      const res = await chat.send(input.value);
      if (res.ok) input.value = '';                              // keep the text if sending failed
    });
    chat.start();
  }

  /* ---------- private notes (students only, autosaved) ---------- */
  let notes = null;
  const NOTES_LABEL = { loading: 'Loading…', 'load-failed': 'Not loaded', saved: 'Saved', dirty: 'Unsaved changes', saving: 'Saving…', error: 'Not saved' };
  function renderNotes(s) {
    const area = $('clsNotesContent');
    area.disabled = !s.canEdit;
    if (area.value !== s.content && document.activeElement !== area) area.value = s.content;
    $('clsNotesStatus').textContent = NOTES_LABEL[s.status] || '';
    const err = $('clsNotesError');
    err.textContent = s.error; err.style.display = s.error ? 'block' : 'none';
    $('clsNotesRetry').style.display = s.status === 'error' || s.status === 'load-failed' ? '' : 'none';
  }
  function startNotesUi(info) {
    if (info.is_trainer || user.role !== 'student') return;     // notes belong to students; nobody else gets a tab
    $('clsNotesTab').style.display = '';
    notes = new ArckClassroomNotes.ClassroomNotes(ArckAPI, info.run_id);
    notes.subscribe(renderNotes);
    $('clsNotesContent').addEventListener('input', (e) => notes.edit(e.target.value));
    $('clsNotesRetry').addEventListener('click', () => notes.retryNow());
    document.addEventListener('visibilitychange', () => { if (document.hidden) notes.save(); });
    window.addEventListener('beforeunload', (e) => { if (notes.hasUnsaved()) { notes.save(); e.preventDefault(); e.returnValue = ''; } });
    notes.load();
  }

  /* ---------- raise hand + trainer moderation (all authorized by the server) ---------- */
  let actions = null;
  let isTrainer = false;
  let handUp = false;
  let lastState = null;
  const act = async (promise) => { const r = await promise; if (!r.ok) note(r.error); return r.ok; };
  function startActionsUi(info) {
    isTrainer = !!info.is_trainer;
    actions = new ArckClassroomActions.ClassroomActions(ArckAPI, info.run_id);
    if (!isTrainer) $('clsToolHand').style.display = '';
    if (isTrainer) {
      // Only the assigned trainer ever sees this button; the server independently refuses everyone else.
      $('clsToolEnd').style.display = '';
      $('clsToolEnd').addEventListener('click', async () => {
        if (!window.confirm('End this class for everyone now? All participants will be disconnected and any recording will stop and be saved. This cannot be undone.')) return;
        $('clsToolEnd').disabled = true;
        const ok = await act(actions.endClass());
        if (ok) leaveClassroom('You ended this class for everyone.');
        else $('clsToolEnd').disabled = false;
      });
    }
    $('clsToolHand').addEventListener('click', async () => {
      const want = !handUp;
      if (await act(actions.raiseHand(want))) { handUp = want; $('clsToolHand').classList.toggle('is-active', handUp); $('clsToolHand').classList.toggle('is-off', !handUp); $('clsToolHand').setAttribute('aria-pressed', String(handUp)); }
    });
    $('clsParticipantList').addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-mod]');
      if (!btn || !isTrainer) return;
      const identity = btn.dataset.id;
      const kind = btn.dataset.mod;
      if (kind === 'kick' && !window.confirm('Remove this student from the class?')) return;
      btn.disabled = true;
      const call = { mute: actions.mute, kick: actions.kick, lower: actions.lowerHand }[kind];
      if (call) await act(call.call(actions, identity));
      btn.disabled = false;
    });
  }

  /* ---------- tiles (shared stage) ---------- */
  const stageView = new ArckClassroomStage.ClassroomStage({
    doc: document, stageEl: $('clsStage'), media, source: lk.Track.Source,
  });

  function render(s) {
    stageView.render(s);
    if (isTrainer) {
      // Notify the trainer when a student raises their hand.
      ArckClassroomActions.newHands(lastState, s).forEach((p) => note(`${p.name} raised their hand`));
    }
    lastState = s;
    setBtn('clsToolMic', s.micOn, 'fa-microphone', 'fa-microphone-slash');
    setBtn('clsToolCamera', s.cameraOn, 'fa-video', 'fa-video-slash');
    setBtn('clsToolShare', s.screenOn, 'fa-display', 'fa-display');
    $('clsStatus').innerHTML = !s.connected
      ? '<i class="fa-solid fa-circle" style="color:#ff5a5a; font-size:.55rem;"></i> Disconnected'
      : s.reconnecting
        ? '<i class="fa-solid fa-circle-notch fa-spin"></i> Reconnecting&hellip;'
        : '<i class="fa-solid fa-circle" style="color:#4caf50; font-size:.55rem;"></i> Connected';
    $('clsCount').textContent = `(${s.participants.length})`;
    $('clsParticipantList').innerHTML = s.participants.map((p) => `
      <li class="meet-participant-row${p.hand ? ' has-hand' : ''}">
        <span class="meet-participant-avatar">${clsEscape((p.name || '?').charAt(0).toUpperCase())}</span>
        <span class="meet-participant-name">${clsEscape(p.name)}${p.isLocal ? ' (You)' : ''}${p.role === 'trainer' ? ' <i class="fa-solid fa-crown" style="color:var(--primary); font-size:.7rem;" title="Trainer"></i>' : ''}${p.hand ? ' <i class="fa-solid fa-hand cls-hand" title="Hand raised"></i>' : ''}</span>
        <span class="meet-participant-badges">
          <i class="fa-solid ${p.micOn ? 'fa-microphone' : 'fa-microphone-slash'}"></i>
          <i class="fa-solid ${p.cameraOn ? 'fa-video' : 'fa-video-slash'}"></i>
          ${p.screenOn ? '<i class="fa-solid fa-display" title="Sharing screen"></i>' : ''}
        </span>
        ${isTrainer && !p.isLocal && p.role !== 'trainer' ? `
        <span class="cls-mod">
          ${p.hand ? `<button type="button" class="icon-btn" data-mod="lower" data-id="${clsEscape(p.identity)}" title="Lower hand"><i class="fa-solid fa-hand-point-down"></i></button>` : ''}
          <button type="button" class="icon-btn" data-mod="mute" data-id="${clsEscape(p.identity)}" title="Mute"><i class="fa-solid fa-microphone-slash"></i></button>
          <button type="button" class="icon-btn" data-mod="kick" data-id="${clsEscape(p.identity)}" title="Remove from class"><i class="fa-solid fa-user-slash"></i></button>
        </span>` : ''}
      </li>`).join('');
  }
});
