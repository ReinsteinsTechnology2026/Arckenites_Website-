const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDate = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const formatTime = (iso) => iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—';
const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
const formatFileSize = (bytes) => !bytes ? '—' : bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
const formatDuration = (mins) => mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;

const STATUS_LABEL = { SCHEDULED: 'Scheduled', LIVE: 'Live', COMPLETED: 'Completed', CANCELLED: 'Cancelled' };
const STATUS_BADGE = { SCHEDULED: 'is-info', LIVE: 'is-success', COMPLETED: 'is-muted', CANCELLED: 'is-danger' };
const RECORDING_STATUS_LABEL = { RECORDING: 'Recording', PROCESSING: 'Processing', AVAILABLE: 'Available', FAILED: 'Not captured' };
const RECORDING_STATUS_BADGE = { RECORDING: 'is-danger', PROCESSING: 'is-info', AVAILABLE: 'is-success', FAILED: 'is-muted' };
const PARTICIPANT_STATUS_LABEL = { INVITED: 'Invited', WAITING: 'Waiting', ADMITTED: 'Admitted', JOINED: 'Joined', LEFT: 'Left', REMOVED: 'Removed', REJECTED: 'Rejected' };

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('admin');
  if (!user) return;

  /* ---------- Shared admin chrome (profile / sidebar / logout) ---------- */
  const initials = user.full_name.split(' ').filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || 'A';
  document.getElementById('adminAvatarInitials').textContent = initials;
  document.getElementById('adminProfileName').textContent = user.full_name;
  document.getElementById('adminProfileRole').textContent = user.role;

  document.querySelectorAll('.admin-sidebar-group-header').forEach((header) => {
    header.addEventListener('click', () => {
      const group = header.closest('.admin-sidebar-group');
      const isOpen = group.classList.toggle('is-open');
      header.setAttribute('aria-expanded', String(isOpen));
    });
  });

  const sidebar = document.getElementById('adminSidebar');
  document.getElementById('adminSidebarCollapseBtn').addEventListener('click', () => sidebar.classList.toggle('is-collapsed'));

  const backdrop = document.getElementById('adminSidebarBackdrop');
  const openMobileSidebar = () => { sidebar.classList.add('is-mobile-open'); backdrop.classList.add('is-visible'); };
  const closeMobileSidebar = () => { sidebar.classList.remove('is-mobile-open'); backdrop.classList.remove('is-visible'); };
  document.getElementById('adminMobileToggle').addEventListener('click', openMobileSidebar);
  backdrop.addEventListener('click', closeMobileSidebar);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMobileSidebar(); });

  const profileTrigger = document.getElementById('adminProfileTrigger');
  const profilePanel = document.getElementById('adminProfilePanel');
  profileTrigger.addEventListener('click', (e) => { e.stopPropagation(); profilePanel.classList.toggle('is-open'); });
  document.addEventListener('click', () => profilePanel.classList.remove('is-open'));

  document.getElementById('adminSidebarLogout').addEventListener('click', () => ArckAuth.logout());
  document.getElementById('adminProfileLogout').addEventListener('click', () => ArckAuth.logout());

  const serverBanner = document.getElementById('adminServerBanner');
  const showServerBanner = () => serverBanner.classList.add('is-visible');

  const canManage = ArckAuth.hasPermission('meetings.manage');
  const canEdit = ArckAuth.hasPermission('meetings.edit');
  const canDelete = ArckAuth.hasPermission('meetings.delete');

  /* ---------- List view: stats + table ---------- */
  const listView = document.getElementById('meetingsListView');
  const detailView = document.getElementById('meetingDetailView');
  const meetingsTableBody = document.getElementById('meetingsTableBody');

  const loadStats = async () => {
    try {
      const s = await ArckAPI.request('/admin/meetings/stats');
      document.getElementById('kpiTotalMeetings').textContent = s.total_meetings;
      document.getElementById('kpiScheduledMeetings').textContent = s.scheduled_meetings;
      document.getElementById('kpiLiveMeetings').textContent = s.live_meetings;
      document.getElementById('kpiCompletedMeetings').textContent = s.completed_meetings;
      document.getElementById('kpiTotalParticipants').textContent = s.total_participants;
    } catch (_) { showServerBanner(); }
  };

  const meetingRowActions = (m) => {
    const actions = [];
    if (m.status === 'SCHEDULED') {
      if (canManage) actions.push(`<button type="button" class="btn btn-accent" style="padding:4px 12px;" data-action="start" data-id="${m.id}">Start</button>`);
      if (canEdit) actions.push(`<button type="button" class="btn btn-primary-outline" style="padding:4px 12px;" data-action="cancel" data-id="${m.id}">Cancel</button>`);
    } else if (m.status === 'LIVE') {
      actions.push(`<a class="btn btn-accent" style="padding:4px 12px;" href="meeting-room.html?token=${encodeURIComponent(m.meeting_token)}">Join</a>`);
      if (canManage) actions.push(`<button type="button" class="btn btn-primary-outline is-danger" style="padding:4px 12px;" data-action="end" data-id="${m.id}">End</button>`);
    }
    actions.push(`<button type="button" class="btn btn-primary-outline" style="padding:4px 12px;" data-action="view" data-id="${m.id}">Details</button>`);
    if (canDelete && m.status !== 'LIVE') actions.push(`<button type="button" class="btn btn-primary-outline is-danger" style="padding:4px 12px;" data-action="delete" data-id="${m.id}">Delete</button>`);
    return actions.join(' ');
  };

  const meetingRowHtml = (m) => `
    <tr>
      <td>${escapeHtml(m.title)}</td>
      <td><code>${escapeHtml(m.meeting_token)}</code></td>
      <td>${escapeHtml(m.host_name)}</td>
      <td>${formatDate(m.scheduled_at)}</td>
      <td>${formatTime(m.scheduled_at)}</td>
      <td>${formatDuration(m.duration_minutes)}</td>
      <td><span class="admin-activity-badge ${STATUS_BADGE[m.status] || 'is-muted'}">${STATUS_LABEL[m.status] || m.status}</span></td>
      <td>${m.participant_count}</td>
      <td>${m.recording_enabled ? '<i class="fa-solid fa-circle-dot" style="color:var(--primary);" title="Recording enabled"></i>' : '<span style="color:var(--muted-2);">—</span>'}</td>
      <td><div style="display:flex; gap:6px; flex-wrap:wrap;">${meetingRowActions(m)}</div></td>
    </tr>
  `;

  const loadMeetings = async () => {
    try {
      const meetings = await ArckAPI.request('/admin/meetings');
      meetingsTableBody.innerHTML = meetings.length ? meetings.map(meetingRowHtml).join('') : '<tr><td colspan="10" class="admin-panel-empty">No meetings scheduled yet.</td></tr>';
    } catch (_) {
      meetingsTableBody.innerHTML = '<tr><td colspan="10" class="admin-panel-empty">Couldn\'t load meetings.</td></tr>';
      showServerBanner();
    }
  };

  meetingsTableBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    const action = btn.dataset.action;
    if (action === 'view') { showDetail(id); return; }

    const confirmMsg = { start: 'Start this meeting now?', end: 'End this meeting for everyone?', cancel: 'Cancel this meeting?', delete: 'Permanently delete this meeting and all its data?' }[action];
    if (confirmMsg && !window.confirm(confirmMsg)) return;

    btn.disabled = true;
    try {
      if (action === 'delete') {
        await ArckAPI.request(`/admin/meetings/${id}`, { method: 'DELETE' });
      } else {
        await ArckAPI.request(`/admin/meetings/${id}/${action}`, { method: 'POST', body: {} });
      }
      await Promise.all([loadMeetings(), loadStats()]);
    } catch (err) {
      window.alert(err.detail || 'Action failed.');
      btn.disabled = false;
    }
  });

  /* ---------- Create Meeting panel ---------- */
  const createPanel = document.getElementById('createMeetingPanel');
  const createForm = document.getElementById('createMeetingForm');
  const toggleCreateBtn = document.getElementById('toggleCreateMeetingBtn');
  const hostSelect = document.getElementById('meetingHost');

  toggleCreateBtn.addEventListener('click', async () => {
    const show = createPanel.style.display === 'none';
    createPanel.style.display = show ? 'block' : 'none';
    if (show && !hostSelect.options.length) {
      try {
        const hosts = await ArckAPI.request('/admin/meetings/lookup/hosts');
        hostSelect.innerHTML = hosts.map((h) => `<option value="${h.id}">${escapeHtml(h.full_name)} (${h.role})</option>`).join('');
      } catch (_) { /* left empty, form validation will catch it */ }
    }
  });
  document.getElementById('cancelCreateMeetingBtn').addEventListener('click', () => { createPanel.style.display = 'none'; createForm.reset(); selectedParticipants.clear(); renderSelectedParticipants(); });

  const participantSearchInput = document.getElementById('meetingParticipantSearch');
  const participantResultsEl = document.getElementById('meetingParticipantResults');
  const participantSelectedEl = document.getElementById('meetingParticipantSelected');
  const selectedParticipants = new Map();
  let participantSearchDebounce = null;

  const renderSelectedParticipants = () => {
    participantSelectedEl.innerHTML = Array.from(selectedParticipants.values()).map((p) => `
      <div class="batch-student-row">
        <span>${escapeHtml(p.full_name)} &middot; @${escapeHtml(p.username)}</span>
        <button type="button" class="btn btn-primary-outline is-danger" data-remove="${p.id}">Remove</button>
      </div>
    `).join('');
  };
  participantSelectedEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-remove]');
    if (!btn) return;
    selectedParticipants.delete(Number(btn.dataset.remove));
    renderSelectedParticipants();
  });
  participantSearchInput.addEventListener('input', () => {
    const q = participantSearchInput.value.trim();
    clearTimeout(participantSearchDebounce);
    if (!q) { participantResultsEl.innerHTML = ''; return; }
    participantSearchDebounce = setTimeout(async () => {
      try {
        const results = await ArckAPI.request(`/admin/meetings/lookup/participants?q=${encodeURIComponent(q)}`);
        participantResultsEl.innerHTML = results.length
          ? results.map((u) => `<div class="batch-student-row"><span>${escapeHtml(u.full_name)} &middot; @${escapeHtml(u.username)} (${u.role})</span><button type="button" class="btn btn-accent" data-pick='${JSON.stringify(u).replace(/'/g, '&#39;')}'>Add</button></div>`).join('')
          : '<div class="batch-student-row">No matching users.</div>';
      } catch (_) { participantResultsEl.innerHTML = '<div class="batch-student-row">Search failed.</div>'; }
    }, 300);
  });
  participantResultsEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-pick]');
    if (!btn) return;
    const u = JSON.parse(btn.dataset.pick);
    selectedParticipants.set(u.id, u);
    renderSelectedParticipants();
    participantSearchInput.value = '';
    participantResultsEl.innerHTML = '';
  });

  createForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const errorBox = document.getElementById('createMeetingError');
    errorBox.style.display = 'none';
    const submitBtn = document.getElementById('createMeetingSubmitBtn');

    const date = document.getElementById('meetingDate').value;
    const time = document.getElementById('meetingTime').value;
    if (!date || !time) { errorBox.textContent = 'Pick a date and start time.'; errorBox.style.display = 'block'; return; }

    const body = {
      title: document.getElementById('meetingTitle').value.trim(),
      description: document.getElementById('meetingDescription').value.trim() || null,
      host_id: Number(hostSelect.value),
      scheduled_at: new Date(`${date}T${time}`).toISOString(),
      duration_minutes: Number(document.getElementById('meetingDuration').value),
      password: document.getElementById('meetingPassword').value.trim() || null,
      max_participants: document.getElementById('meetingMaxParticipants').value ? Number(document.getElementById('meetingMaxParticipants').value) : null,
      mic_enabled: document.getElementById('toggleMic').checked,
      camera_enabled: document.getElementById('toggleCamera').checked,
      screen_share_enabled: document.getElementById('toggleScreenShare').checked,
      chat_enabled: document.getElementById('toggleChat').checked,
      notes_enabled: document.getElementById('toggleNotes').checked,
      participant_notes_enabled: document.getElementById('toggleParticipantNotes').checked,
      recording_enabled: document.getElementById('toggleRecording').checked,
      waiting_room_enabled: document.getElementById('toggleWaitingRoom').checked,
      require_approval: document.getElementById('toggleRequireApproval').checked,
      participant_user_ids: Array.from(selectedParticipants.keys()),
    };

    submitBtn.disabled = true;
    try {
      await ArckAPI.request('/admin/meetings', { method: 'POST', body });
      createPanel.style.display = 'none';
      createForm.reset();
      selectedParticipants.clear();
      renderSelectedParticipants();
      await Promise.all([loadMeetings(), loadStats()]);
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not create meeting.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

  /* ---------- Detail view ---------- */
  let currentMeetingId = null;
  let notesLoaded = false, chatLoaded = false, recordingsLoaded = false, participantsLoaded = false;

  const showDetail = async (id) => {
    currentMeetingId = id;
    notesLoaded = chatLoaded = recordingsLoaded = participantsLoaded = false;
    listView.style.display = 'none';
    detailView.style.display = 'block';
    document.querySelectorAll('.admin-tab').forEach((t) => t.classList.toggle('is-active', t.dataset.tab === 'participants'));
    document.querySelectorAll('[data-tab-panel]').forEach((p) => { p.style.display = p.dataset.tabPanel === 'participants' ? 'block' : 'none'; });
    await loadDetailInfo();
    await loadDetailParticipants();
  };

  document.getElementById('backToMeetingsBtn').addEventListener('click', () => {
    detailView.style.display = 'none';
    listView.style.display = 'block';
    loadMeetings();
    loadStats();
  });

  const loadDetailInfo = async () => {
    const infoBody = document.getElementById('detailInfoBody');
    const actionsEl = document.getElementById('detailActions');
    infoBody.innerHTML = 'Loading&hellip;';
    try {
      const m = await ArckAPI.request(`/admin/meetings/${currentMeetingId}`);
      document.getElementById('detailTitle').textContent = m.title;
      document.getElementById('detailSubline').textContent = `${formatDate(m.scheduled_at)} at ${formatTime(m.scheduled_at)} — hosted by ${m.host_name}`;

      const joinLink = `${location.origin}/meeting-room.html?token=${encodeURIComponent(m.meeting_token)}`;
      const featureBadges = [
        ['Mic', m.mic_enabled], ['Camera', m.camera_enabled], ['Screen Share', m.screen_share_enabled],
        ['Chat', m.chat_enabled], ['Notes', m.notes_enabled], ['Recording', m.recording_enabled],
        ['Waiting Room', m.waiting_room_enabled], ['Approval Required', m.require_approval],
      ].map(([label, on]) => `<span class="admin-activity-badge ${on ? 'is-success' : 'is-muted'}">${label}</span>`).join(' ');

      infoBody.innerHTML = `
        <div class="admin-form-grid">
          <div><span class="form-label">Meeting ID</span><p><code>${escapeHtml(m.meeting_token)}</code></p></div>
          <div><span class="form-label">Status</span><p><span class="admin-activity-badge ${STATUS_BADGE[m.status]}">${STATUS_LABEL[m.status]}</span></p></div>
          <div><span class="form-label">Duration</span><p>${formatDuration(m.duration_minutes)}</p></div>
          <div><span class="form-label">Password Protected</span><p>${m.has_password ? 'Yes' : 'No'}</p></div>
          <div><span class="form-label">Max Participants</span><p>${m.max_participants ?? 'Unlimited'}</p></div>
          <div><span class="form-label">Started / Ended</span><p>${formatDateTime(m.started_at)} ${m.ended_at ? '– ' + formatDateTime(m.ended_at) : ''}</p></div>
        </div>
        ${m.description ? `<p style="margin-top:16px; color:var(--muted);">${escapeHtml(m.description)}</p>` : ''}
        <div style="margin-top:16px; display:flex; gap:8px; flex-wrap:wrap;">${featureBadges}</div>
        <div style="margin-top:18px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
          <input type="text" class="form-control" readonly value="${escapeHtml(joinLink)}" style="max-width:420px;">
          <button type="button" class="btn btn-primary-outline" id="copyJoinLinkBtn"><i class="fa-solid fa-link"></i> Copy Meeting Link</button>
        </div>
      `;
      document.getElementById('copyJoinLinkBtn').addEventListener('click', () => {
        navigator.clipboard.writeText(joinLink).then(() => window.alert('Meeting link copied.'));
      });

      const actions = [];
      if (m.status === 'LIVE') actions.push(`<a class="btn btn-accent" href="meeting-room.html?token=${encodeURIComponent(m.meeting_token)}"><i class="fa-solid fa-video"></i> Join Meeting</a>`);
      actionsEl.innerHTML = actions.join(' ');
    } catch (_) {
      infoBody.innerHTML = '<div class="admin-panel-empty">Couldn\'t load meeting details.</div>';
    }
  };

  /* ---------- Detail tabs ---------- */
  document.getElementById('detailTabs').addEventListener('click', async (e) => {
    const tabBtn = e.target.closest('.admin-tab');
    if (!tabBtn) return;
    const tab = tabBtn.dataset.tab;
    document.querySelectorAll('.admin-tab').forEach((t) => t.classList.toggle('is-active', t === tabBtn));
    document.querySelectorAll('[data-tab-panel]').forEach((p) => { p.style.display = p.dataset.tabPanel === tab ? 'block' : 'none'; });

    if (tab === 'chat' && !chatLoaded) { chatLoaded = true; await loadDetailChat(); }
    if (tab === 'notes' && !notesLoaded) { notesLoaded = true; await loadDetailNotes(); }
    if (tab === 'recordings' && !recordingsLoaded) { recordingsLoaded = true; await loadDetailRecordings(); }
  });

  const detailParticipantsBody = document.getElementById('detailParticipantsBody');
  const loadDetailParticipants = async () => {
    if (participantsLoaded) return;
    participantsLoaded = true;
    try {
      const m = await ArckAPI.request(`/admin/meetings/${currentMeetingId}`);
      detailParticipantsBody.innerHTML = m.participants.length ? m.participants.map((p) => `
        <tr>
          <td>${escapeHtml(p.full_name)} ${p.role === 'HOST' ? '<span class="admin-activity-badge is-info">Host</span>' : ''}</td>
          <td>${p.role}</td>
          <td>${PARTICIPANT_STATUS_LABEL[p.status] || p.status}</td>
          <td>${formatDateTime(p.joined_at)}</td>
          <td>${formatDateTime(p.left_at)}</td>
          <td>${p.role !== 'HOST' && canManage ? `<button type="button" class="btn btn-primary-outline is-danger" style="padding:4px 12px;" data-remove-participant="${p.id}">Remove</button>` : ''}</td>
        </tr>
      `).join('') : '<tr><td colspan="6" class="admin-panel-empty">No participants invited yet.</td></tr>';
    } catch (_) {
      detailParticipantsBody.innerHTML = '<tr><td colspan="6" class="admin-panel-empty">Couldn\'t load participants.</td></tr>';
    }
  };
  detailParticipantsBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-remove-participant]');
    if (!btn) return;
    if (!window.confirm('Remove this participant from the meeting?')) return;
    btn.disabled = true;
    try {
      await ArckAPI.request(`/admin/meetings/${currentMeetingId}/participants/${btn.dataset.removeParticipant}`, { method: 'DELETE' });
      participantsLoaded = false;
      await loadDetailParticipants();
    } catch (err) { window.alert(err.detail || 'Could not remove participant.'); btn.disabled = false; }
  });

  const detailParticipantSearch = document.getElementById('detailParticipantSearch');
  const detailParticipantResults = document.getElementById('detailParticipantResults');
  let detailParticipantDebounce = null;
  detailParticipantSearch.addEventListener('input', () => {
    const q = detailParticipantSearch.value.trim();
    clearTimeout(detailParticipantDebounce);
    if (!q) { detailParticipantResults.innerHTML = ''; return; }
    detailParticipantDebounce = setTimeout(async () => {
      try {
        const results = await ArckAPI.request(`/admin/meetings/lookup/participants?q=${encodeURIComponent(q)}`);
        detailParticipantResults.innerHTML = results.length
          ? results.map((u) => `<div class="batch-student-row"><span>${escapeHtml(u.full_name)} &middot; @${escapeHtml(u.username)}</span><button type="button" class="btn btn-accent" data-add-participant="${u.id}">Add</button></div>`).join('')
          : '<div class="batch-student-row">No matching users.</div>';
      } catch (_) { detailParticipantResults.innerHTML = '<div class="batch-student-row">Search failed.</div>'; }
    }, 300);
  });
  detailParticipantResults.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-add-participant]');
    if (!btn) return;
    btn.disabled = true;
    try {
      await ArckAPI.request(`/admin/meetings/${currentMeetingId}/participants`, { method: 'POST', body: { user_id: Number(btn.dataset.addParticipant) } });
      detailParticipantSearch.value = '';
      detailParticipantResults.innerHTML = '';
      participantsLoaded = false;
      await loadDetailParticipants();
    } catch (err) { window.alert(err.detail || 'Could not add participant.'); btn.disabled = false; }
  });

  const loadDetailChat = async () => {
    const el = document.getElementById('detailChatBody');
    try {
      const messages = await ArckAPI.request(`/admin/meetings/${currentMeetingId}/messages`);
      el.innerHTML = messages.length ? messages.map((m) => `
        <div style="margin-bottom:12px; ${m.is_system ? 'color:var(--muted-2); font-style:italic;' : ''}">
          ${!m.is_system ? `<strong>${escapeHtml(m.sender_name)}</strong> <span style="color:var(--muted-2); font-size:.78rem;">${formatDateTime(m.created_at)}</span><br>` : ''}
          ${escapeHtml(m.message)}
        </div>
      `).join('') : '<div class="admin-panel-empty">No chat messages yet.</div>';
    } catch (_) { el.innerHTML = '<div class="admin-panel-empty">Couldn\'t load chat history.</div>'; }
  };

  const loadDetailNotes = async () => {
    const el = document.getElementById('detailNotesBody');
    try {
      const note = await ArckAPI.request(`/admin/meetings/${currentMeetingId}/notes`);
      const items = note.action_items.map((i) => `<div>${i.done ? '☑' : '☐'} ${escapeHtml(i.text)}</div>`).join('') || '<span style="color:var(--muted-2);">No action items.</span>';
      el.innerHTML = `
        <h3 style="font-size:1rem; margin-bottom:8px;">Notes</h3>
        <div style="white-space:pre-wrap; color:var(--ink); margin-bottom:20px;">${note.content ? escapeHtml(note.content) : '<span style="color:var(--muted-2);">No notes written yet.</span>'}</div>
        <h3 style="font-size:1rem; margin-bottom:8px;">Action Items</h3>
        ${items}
        ${note.updated_by_name ? `<p style="margin-top:16px; font-size:.8rem; color:var(--muted-2);">Last updated by ${escapeHtml(note.updated_by_name)} on ${formatDateTime(note.updated_at)}</p>` : ''}
      `;
    } catch (_) { el.innerHTML = '<div class="admin-panel-empty">Couldn\'t load notes.</div>'; }
  };

  const loadDetailRecordings = async () => {
    const body = document.getElementById('detailRecordingsBody');
    try {
      const recordings = await ArckAPI.request(`/admin/meetings/${currentMeetingId}/recordings`);
      body.innerHTML = recordings.length ? recordings.map((r) => `
        <tr>
          <td>${formatDateTime(r.started_at)}</td>
          <td>${r.duration_seconds ? `${Math.round(r.duration_seconds / 60)}m` : '—'}</td>
          <td>${formatFileSize(r.file_size_bytes)}</td>
          <td><span class="admin-activity-badge ${RECORDING_STATUS_BADGE[r.status]}">${RECORDING_STATUS_LABEL[r.status]}</span></td>
          <td>
            ${r.status === 'AVAILABLE' ? `<button type="button" class="btn btn-primary-outline" style="padding:4px 12px;" data-download-recording="${r.id}">Download</button>` : ''}
            ${canDelete ? `<button type="button" class="btn btn-primary-outline is-danger" style="padding:4px 12px;" data-delete-recording="${r.id}">Delete</button>` : ''}
          </td>
        </tr>
      `).join('') : '<tr><td colspan="5" class="admin-panel-empty">No recordings yet.</td></tr>';
    } catch (_) { body.innerHTML = '<tr><td colspan="5" class="admin-panel-empty">Couldn\'t load recordings.</td></tr>'; }
  };
  document.getElementById('detailRecordingsBody').addEventListener('click', async (e) => {
    const delBtn = e.target.closest('[data-delete-recording]');
    if (delBtn) {
      if (!window.confirm('Delete this recording permanently?')) return;
      delBtn.disabled = true;
      try {
        await ArckAPI.request(`/admin/meetings/${currentMeetingId}/recordings/${delBtn.dataset.deleteRecording}`, { method: 'DELETE' });
        recordingsLoaded = false;
        await loadDetailRecordings();
      } catch (err) { window.alert(err.detail || 'Could not delete recording.'); delBtn.disabled = false; }
      return;
    }
    const dlBtn = e.target.closest('[data-download-recording]');
    if (dlBtn) {
      dlBtn.disabled = true;
      try {
        const res = await fetch(`${API_BASE}/admin/meetings/${currentMeetingId}/recordings/${dlBtn.dataset.downloadRecording}/download`, {
          headers: { Authorization: `Bearer ${ArckAPI.getToken()}` },
        });
        if (!res.ok) throw new Error('Download failed');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `meeting-${currentMeetingId}-recording-${dlBtn.dataset.downloadRecording}.mp4`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (_) {
        window.alert('Could not download this recording.');
      } finally {
        dlBtn.disabled = false;
      }
    }
  });

  await Promise.all([loadStats(), loadMeetings()]);

});
