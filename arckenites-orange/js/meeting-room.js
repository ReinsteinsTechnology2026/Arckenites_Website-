const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatTime = (iso) => iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';

const PARTICIPANT_STATUS_LABEL = { INVITED: 'Invited', WAITING: 'Waiting', ADMITTED: 'Admitted', JOINED: 'In meeting', LEFT: 'Left', REMOVED: 'Removed', REJECTED: 'Rejected' };

document.addEventListener('DOMContentLoaded', async () => {

  const token = new URLSearchParams(location.search).get('token');
  const prejoinBody = document.getElementById('meetPrejoinBody');

  if (!token) {
    prejoinBody.innerHTML = '<h2>Invalid meeting link</h2><p>This meeting link is missing or malformed.</p>';
    return;
  }

  let user;
  try {
    user = await ArckAuth.getCurrentUser();
  } catch (_) {
    return; // onUnauthorized already redirected to login.html
  }

  let meetingId = null;
  let isModerator = false;
  let jitsiApi = null;
  let jitsiDomain = null;
  let notesEnabled = true;
  let chatEnabled = true;
  let screenShareEnabled = true;
  let micAllowed = true;
  let cameraAllowed = true;
  let ws = null;
  let waitingPollTimer = null;
  let lastPassword = null;
  let notesSaveTimer = null;
  let actionItems = [];

  /* ---------- Join flow ---------- */
  const renderJoinButton = (extraHtml = '') => {
    prejoinBody.innerHTML = `
      <h2>Ready to join?</h2>
      <p>You're signed in as ${escapeHtml(user.full_name)}.</p>
      ${extraHtml}
      <button type="button" class="btn btn-accent" id="meetJoinBtn" style="margin-top:16px; width:100%;">
        <i class="fa-solid fa-video"></i> Join Meeting
      </button>
      <div class="meet-prejoin-error" id="meetPrejoinError" style="display:none;"></div>
    `;
    document.getElementById('meetJoinBtn').addEventListener('click', () => attemptJoin(lastPassword));
  };

  const renderPasswordPrompt = (message) => {
    prejoinBody.innerHTML = `
      <h2>Password required</h2>
      <p>${escapeHtml(message || 'This meeting is password-protected.')}</p>
      <input type="password" class="form-control" id="meetPasswordInput" placeholder="Meeting password" style="margin-top:14px;">
      <button type="button" class="btn btn-accent" id="meetPasswordSubmit" style="margin-top:12px; width:100%;">Join Meeting</button>
      <div class="meet-prejoin-error" id="meetPrejoinError" style="display:none;"></div>
    `;
    const submit = () => attemptJoin(document.getElementById('meetPasswordInput').value);
    document.getElementById('meetPasswordSubmit').addEventListener('click', submit);
    document.getElementById('meetPasswordInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
  };

  const renderWaiting = () => {
    prejoinBody.innerHTML = `
      <h2><i class="fa-solid fa-hourglass-half"></i> Waiting for the host</h2>
      <p>You've asked to join — the host needs to admit you before you can enter.</p>
    `;
  };

  const renderError = (message) => {
    const errEl = document.getElementById('meetPrejoinError');
    if (errEl) { errEl.textContent = message; errEl.style.display = 'block'; }
    else { prejoinBody.innerHTML = `<h2>Can't join this meeting</h2><p>${escapeHtml(message)}</p>`; }
  };

  const attemptJoin = async (password) => {
    lastPassword = password || null;
    const btn = document.getElementById('meetJoinBtn') || document.getElementById('meetPasswordSubmit');
    if (btn) btn.disabled = true;
    try {
      const res = await ArckAPI.request(`/meetings/${encodeURIComponent(token)}/join`, {
        method: 'POST', body: { password: lastPassword },
      });
      if (res.status === 'WAITING') {
        clearTimeout(waitingPollTimer);
        renderWaiting();
        waitingPollTimer = setTimeout(() => attemptJoin(lastPassword), 5000);
        return;
      }
      meetingId = res.meeting_id;
      isModerator = res.is_moderator;
      notesEnabled = res.notes_enabled;
      chatEnabled = res.chat_enabled;
      screenShareEnabled = res.screen_share_enabled;
      micAllowed = res.mic_enabled;
      cameraAllowed = res.camera_enabled;
      jitsiDomain = res.jitsi_domain;
      await enterRoom(res);
    } catch (err) {
      if (err.status === 401) {
        renderPasswordPrompt(err.detail);
      } else if (err.status === 202) {
        // shouldn't happen anymore (fixed server-side), kept as a defensive fallback
        renderWaiting();
      } else {
        renderError(err.detail || 'Something went wrong.');
        if (btn) btn.disabled = false;
      }
    }
  };

  renderJoinButton();

  /* ---------- Enter the live room ---------- */
  const loadJitsiScript = (domain) => new Promise((resolve, reject) => {
    if (window.JitsiMeetExternalAPI) { resolve(); return; }
    const script = document.createElement('script');
    script.src = `https://${domain}/external_api.js`;
    script.onload = resolve;
    script.onerror = () => reject(new Error('Could not load the meeting server. It may be offline.'));
    document.head.appendChild(script);
  });

  async function enterRoom(joinInfo) {
    try {
      await loadJitsiScript(jitsiDomain);
    } catch (err) {
      renderError(err.message);
      return;
    }

    document.getElementById('meetPrejoin').style.display = 'none';
    document.getElementById('meetRoom').style.display = 'flex';
    document.getElementById('meetTitle').textContent = joinInfo.title;
    document.getElementById('meetToolNotes').style.display = notesEnabled ? '' : 'none';
    document.getElementById('meetToolChat').style.display = chatEnabled ? '' : 'none';
    if (!isModerator) document.getElementById('meetToolRecord').style.display = 'none';

    jitsiApi = new window.JitsiMeetExternalAPI(jitsiDomain, {
      roomName: joinInfo.room_name,
      parentNode: document.getElementById('meetVideoArea'),
      width: '100%',
      height: '100%',
      userInfo: { displayName: joinInfo.display_name },
      configOverwrite: {
        prejoinPageEnabled: false,
        disableDeepLinking: true,
        startWithAudioMuted: !micAllowed,
        startWithVideoMuted: !cameraAllowed,
        toolbarButtons: [], // we drive everything through our own toolbar below
      },
      interfaceConfigOverwrite: {
        TOOLBAR_BUTTONS: [],
        SHOW_JITSI_WATERMARK: false,
        SHOW_WATERMARK_FOR_GUESTS: false,
        MOBILE_APP_PROMO: false,
      },
    });

    const connStatus = document.getElementById('meetConnectionStatus');
    jitsiApi.addEventListener('videoConferenceJoined', () => {
      connStatus.innerHTML = '<i class="fa-solid fa-circle" style="color:#4caf50; font-size:.55rem;"></i> Connected';
    });
    jitsiApi.addEventListener('videoConferenceLeft', () => leaveAndShowEnded());
    jitsiApi.addEventListener('readyToClose', () => leaveAndShowEnded());
    jitsiApi.addEventListener('audioMuteStatusChanged', ({ muted }) => setToolActive('meetToolMic', !muted));
    jitsiApi.addEventListener('videoMuteStatusChanged', ({ muted }) => setToolActive('meetToolCamera', !muted));
    jitsiApi.addEventListener('screenSharingStatusChanged', ({ on }) => setToolActive('meetToolShare', on));
    jitsiApi.addEventListener('participantJoined', () => refreshParticipants());
    jitsiApi.addEventListener('participantLeft', () => refreshParticipants());

    setToolActive('meetToolMic', micAllowed);
    setToolActive('meetToolCamera', cameraAllowed);

    connectChatSocket();
    await loadChatHistory();
    await refreshParticipants();
    if (notesEnabled) await loadNotes();
    if (isModerator) pollWaitingRoom();
  }

  function setToolActive(id, active) {
    document.getElementById(id).classList.toggle('is-active', active);
    document.getElementById(id).classList.toggle('is-off', !active);
  }

  /* ---------- Toolbar ---------- */
  document.getElementById('meetToolMic').addEventListener('click', () => {
    if (!isModerator && !micAllowed) { window.alert('The host has disabled microphones for this meeting.'); return; }
    jitsiApi?.executeCommand('toggleAudio');
  });
  document.getElementById('meetToolCamera').addEventListener('click', () => {
    if (!isModerator && !cameraAllowed) { window.alert('The host has disabled cameras for this meeting.'); return; }
    jitsiApi?.executeCommand('toggleVideo');
  });
  document.getElementById('meetToolShare').addEventListener('click', () => {
    if (!isModerator && !screenShareEnabled) { window.alert('The host has disabled screen sharing for this meeting.'); return; }
    jitsiApi?.executeCommand('toggleShareScreen');
  });
  document.getElementById('meetToolLeave').addEventListener('click', () => {
    if (window.confirm('Leave this meeting?')) jitsiApi?.executeCommand('hangup');
  });
  document.getElementById('meetToolRecord').addEventListener('click', toggleRecording);

  const sidepanel = document.getElementById('meetSidepanel');
  const openPanel = (name) => {
    sidepanel.classList.add('is-open');
    sidepanel.dataset.activeTab = name;
    document.querySelectorAll('.meet-sidepanel-tab').forEach((t) => t.classList.toggle('is-active', t.dataset.panel === name));
    document.querySelectorAll('[data-panel-body]').forEach((p) => { p.style.display = p.dataset.panelBody === name ? 'flex' : 'none'; });
  };
  document.getElementById('meetToolParticipants').addEventListener('click', () => openPanel('participants'));
  document.getElementById('meetToolChat').addEventListener('click', () => openPanel('chat'));
  document.getElementById('meetToolNotes').addEventListener('click', () => openPanel('notes'));
  document.querySelectorAll('.meet-sidepanel-tab').forEach((tab) => tab.addEventListener('click', () => openPanel(tab.dataset.panel)));
  document.getElementById('meetSidepanelClose').addEventListener('click', () => sidepanel.classList.remove('is-open'));
  openPanel('participants');
  if (window.innerWidth > 860) sidepanel.classList.add('is-open');

  /* ---------- Participants ---------- */
  const participantListEl = document.getElementById('meetParticipantList');
  async function refreshParticipants() {
    try {
      const [participants, waiting] = await Promise.all([
        ArckAPI.request(`/meetings/${meetingId}/participants`),
        isModerator ? ArckAPI.request(`/meetings/${meetingId}/waiting`) : Promise.resolve([]),
      ]);
      const waitingHtml = waiting.map((p) => `
        <li class="meet-participant-row" style="background:rgba(255,90,31,.08);">
          <span class="meet-participant-avatar">${escapeHtml((p.full_name || '?').charAt(0))}</span>
          <span class="meet-participant-name">${escapeHtml(p.full_name)} <em style="color:var(--muted-2); font-style:normal; font-size:.72rem;">waiting</em></span>
          <div style="display:flex; gap:6px;">
            <button type="button" class="icon-btn" style="width:26px;height:26px;" data-admit="${p.id}" title="Admit"><i class="fa-solid fa-check" style="color:#4caf50;"></i></button>
            <button type="button" class="icon-btn" style="width:26px;height:26px;" data-reject="${p.id}" title="Reject"><i class="fa-solid fa-xmark" style="color:#ff5a5a;"></i></button>
          </div>
        </li>
      `).join('');
      const rowsHtml = participants.map((p) => `
        <li class="meet-participant-row">
          <span class="meet-participant-avatar">${escapeHtml((p.full_name || '?').charAt(0))}</span>
          <span class="meet-participant-name">${escapeHtml(p.full_name)} ${p.role === 'HOST' ? '<i class="fa-solid fa-crown" style="color:var(--primary); font-size:.7rem;" title="Host"></i>' : ''}</span>
          <span class="meet-participant-badges">${PARTICIPANT_STATUS_LABEL[p.status] || p.status}</span>
          ${isModerator && p.role !== 'HOST' && p.status === 'JOINED' ? `<button type="button" class="icon-btn" style="width:24px;height:24px;" data-remove="${p.id}" title="Remove"><i class="fa-solid fa-user-slash"></i></button>` : ''}
        </li>
      `).join('');
      participantListEl.innerHTML = waitingHtml + rowsHtml || '<li class="admin-panel-empty">No participants yet.</li>';
    } catch (_) { /* keep last-known list on a transient failure */ }
  }
  participantListEl.addEventListener('click', async (e) => {
    const admitBtn = e.target.closest('[data-admit]');
    const rejectBtn = e.target.closest('[data-reject]');
    const removeBtn = e.target.closest('[data-remove]');
    try {
      if (admitBtn) await ArckAPI.request(`/meetings/${meetingId}/participants/${admitBtn.dataset.admit}/admit`, { method: 'POST' });
      else if (rejectBtn) await ArckAPI.request(`/meetings/${meetingId}/participants/${rejectBtn.dataset.reject}/reject`, { method: 'POST' });
      else if (removeBtn) { if (!window.confirm('Remove this participant?')) return; await ArckAPI.request(`/meetings/${meetingId}/participants/${removeBtn.dataset.remove}/remove`, { method: 'POST' }); }
      else return;
      await refreshParticipants();
    } catch (err) { window.alert(err.detail || 'Action failed.'); }
  });

  function pollWaitingRoom() {
    setInterval(refreshParticipants, 6000);
  }

  /* ---------- Chat ---------- */
  const chatMessagesEl = document.getElementById('meetChatMessages');
  const chatForm = document.getElementById('meetChatForm');
  const chatInput = document.getElementById('meetChatInput');
  document.getElementById('meetChatForm').style.display = chatEnabled ? 'flex' : 'none';

  const renderChatMessage = (m) => {
    const div = document.createElement('div');
    div.className = `meet-chat-msg${m.is_system ? ' is-system' : ''}`;
    div.innerHTML = m.is_system
      ? escapeHtml(m.message)
      : `<strong>${escapeHtml(m.sender_name)}<time>${formatTime(m.created_at)}</time></strong>${escapeHtml(m.message)}`;
    chatMessagesEl.appendChild(div);
    chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
  };

  async function loadChatHistory() {
    try {
      const messages = await ArckAPI.request(`/meetings/${meetingId}/messages`);
      chatMessagesEl.innerHTML = '';
      messages.forEach(renderChatMessage);
    } catch (_) { /* chat history is best-effort */ }
  }

  chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = '';
    try {
      const msg = await ArckAPI.request(`/meetings/${meetingId}/messages`, { method: 'POST', body: { message: text } });
      renderChatMessage(msg);
    } catch (err) { window.alert(err.detail || 'Could not send message.'); }
  });

  function connectChatSocket() {
    const base = new URL(API_BASE, window.location.href);
    const proto = base.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${base.host}${base.pathname}/chat/ws?token=${encodeURIComponent(ArckAPI.getToken())}`;
    ws = new WebSocket(url);
    ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        if (data.type === 'meeting_message' && data.meeting_id === meetingId) renderChatMessage(data.message);
      } catch (_) { /* ignore malformed frames */ }
    };
    ws.onclose = () => { setTimeout(() => { if (meetingId) connectChatSocket(); }, 3000); };
  }

  /* ---------- Notes ---------- */
  const notesContentEl = document.getElementById('meetNotesContent');
  const notesStatusEl = document.getElementById('meetNotesSaveStatus');
  const actionItemsList = document.getElementById('meetActionItemsList');

  async function loadNotes() {
    try {
      const note = await ArckAPI.request(`/meetings/${meetingId}/notes`);
      notesContentEl.value = note.content;
      actionItems = note.action_items || [];
      renderActionItems();
    } catch (_) { /* notes are best-effort on load */ }
  }

  const renderActionItems = () => {
    actionItemsList.innerHTML = actionItems.map((item, i) => `
      <li class="meet-action-item ${item.done ? 'is-done' : ''}">
        <input type="checkbox" data-item-done="${i}" ${item.done ? 'checked' : ''}>
        <input type="text" data-item-text="${i}" value="${escapeHtml(item.text)}">
        <button type="button" class="icon-btn" data-item-remove="${i}"><i class="fa-solid fa-xmark"></i></button>
      </li>
    `).join('');
  };

  document.getElementById('meetAddActionItem').addEventListener('click', () => {
    actionItems.push({ text: '', done: false });
    renderActionItems();
    scheduleNotesSave();
  });
  actionItemsList.addEventListener('input', (e) => {
    const textInput = e.target.closest('[data-item-text]');
    if (textInput) { actionItems[Number(textInput.dataset.itemText)].text = textInput.value; scheduleNotesSave(); }
  });
  actionItemsList.addEventListener('change', (e) => {
    const checkbox = e.target.closest('[data-item-done]');
    if (checkbox) { actionItems[Number(checkbox.dataset.itemDone)].done = checkbox.checked; renderActionItems(); scheduleNotesSave(); }
  });
  actionItemsList.addEventListener('click', (e) => {
    const removeBtn = e.target.closest('[data-item-remove]');
    if (removeBtn) { actionItems.splice(Number(removeBtn.dataset.itemRemove), 1); renderActionItems(); scheduleNotesSave(); }
  });

  const scheduleNotesSave = () => {
    notesStatusEl.textContent = 'Saving…';
    clearTimeout(notesSaveTimer);
    notesSaveTimer = setTimeout(saveNotes, 1200);
  };
  notesContentEl.addEventListener('input', scheduleNotesSave);

  async function saveNotes() {
    try {
      await ArckAPI.request(`/meetings/${meetingId}/notes`, {
        method: 'PATCH', body: { content: notesContentEl.value, action_items: actionItems },
      });
      notesStatusEl.textContent = 'Saved';
    } catch (_) {
      notesStatusEl.textContent = 'Could not save';
    }
  }

  /* ---------- Recording ---------- */
  let activeRecordingId = null;
  async function toggleRecording() {
    const btn = document.getElementById('meetToolRecord');
    btn.disabled = true;
    try {
      if (activeRecordingId) {
        await ArckAPI.request(`/meetings/${meetingId}/recordings/${activeRecordingId}/stop`, { method: 'POST' });
        activeRecordingId = null;
        btn.classList.remove('is-recording');
        document.getElementById('meetRecordingBadge').style.display = 'none';
        window.alert('Recording stopped. Note: actual video capture requires the Jibri recording service to be deployed on the server — see the deployment docs. This session\'s recording metadata was saved, but no video file was produced yet.');
      } else {
        const rec = await ArckAPI.request(`/meetings/${meetingId}/recordings/start`, { method: 'POST', body: {} });
        activeRecordingId = rec.id;
        btn.classList.add('is-recording');
        document.getElementById('meetRecordingBadge').style.display = 'inline-flex';
      }
    } catch (err) {
      window.alert(err.detail || 'Recording action failed.');
    } finally {
      btn.disabled = false;
    }
  }

  /* ---------- Leaving ---------- */
  async function leaveAndShowEnded() {
    if (ws) { ws.onclose = null; ws.close(); }
    try { if (meetingId) await ArckAPI.request(`/meetings/${meetingId}/leave`, { method: 'POST' }); } catch (_) { /* best-effort */ }
    document.getElementById('meetRoom').style.display = 'none';
    document.getElementById('meetEndedScreen').style.display = 'flex';
    meetingId = null;
  }

});
