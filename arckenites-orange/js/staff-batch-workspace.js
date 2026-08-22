const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDate = (iso) => iso ? new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const formatTime = (t) => t ? new Date(`1970-01-01T${t}`).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';

const BATCH_STATUS_BADGE = { upcoming: 'is-info', active: 'is-success', paused: 'is-pending', completed: 'is-muted', cancelled: 'is-danger' };
const BATCH_STATUS_LABEL = { upcoming: 'Upcoming', active: 'Active', paused: 'Paused', completed: 'Completed', cancelled: 'Cancelled' };

const formatDateKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('staff');
  if (!user) return;

  const batchId = Number(new URLSearchParams(window.location.search).get('id'));
  if (!batchId) {
    document.getElementById('workspaceBatchName').textContent = 'Batch not found';
    return;
  }

  document.getElementById('staffWelcome').textContent = `Welcome, ${user.full_name}`;
  document.getElementById('staffLogoutBtn').addEventListener('click', () => ArckAuth.logout());

  /* ---------- Batch header ---------- */
  const chatBadgeEl = document.getElementById('workspaceChatBadge');
  let liveUnreadCount = 0;
  const updateChatBadge = (count) => {
    liveUnreadCount = count;
    if (count > 0) {
      chatBadgeEl.textContent = count > 9 ? '9+' : String(count);
      chatBadgeEl.style.display = 'inline-flex';
    } else {
      chatBadgeEl.style.display = 'none';
    }
  };

  const loadBatchHeader = async () => {
    try {
      const batch = await ArckAPI.request(`/staff/me/batches/${batchId}`);
      document.getElementById('workspaceBatchName').textContent = batch.name;
      document.title = `${batch.name} | Arckenites`;
      document.getElementById('workspaceBatchProgram').textContent = batch.program_name || batch.course || 'No program linked';
      document.getElementById('workspaceBatchStatus').innerHTML = `<span class="admin-activity-badge ${BATCH_STATUS_BADGE[batch.status] || 'is-muted'}">${BATCH_STATUS_LABEL[batch.status] || batch.status}</span>`;
      document.getElementById('workspaceBatchStudents').textContent = `${batch.student_count}${batch.max_capacity ? ' / ' + batch.max_capacity : ''} students`;
      updateChatBadge(batch.unread_chat_count);
    } catch (_) {
      document.getElementById('workspaceBatchName').textContent = 'Batch not found';
    }
  };

  /* ---------- Tabs ---------- */
  const tabs = document.querySelectorAll('.workspace-tab[data-tab]');
  const panels = document.querySelectorAll('section[data-tab-panel]');
  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      tabs.forEach((t) => t.classList.remove('is-active'));
      tab.classList.add('is-active');
      const name = tab.dataset.tab;
      panels.forEach((p) => { p.style.display = p.dataset.tabPanel === name ? 'block' : 'none'; });
      if (name === 'chat') loadChat();
    });
  });

  document.getElementById('workspaceSupportTab').addEventListener('click', () => {
    window.location.href = 'staff-support.html';
  });

  /* ---------- Calendar ---------- */
  let sessions = [];
  const today = new Date();
  let calYear = today.getFullYear();
  let calMonth = today.getMonth();

  document.getElementById('calendarWeekdays').innerHTML = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    .map((d) => `<div class="calendar-weekday">${d}</div>`).join('');

  const renderCalendar = () => {
    document.getElementById('calMonthLabel').textContent = new Date(calYear, calMonth, 1)
      .toLocaleDateString(undefined, { month: 'long', year: 'numeric' });

    const startWeekday = new Date(calYear, calMonth, 1).getDay();
    const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
    const daysInPrevMonth = new Date(calYear, calMonth, 0).getDate();

    const cells = [];
    for (let i = startWeekday - 1; i >= 0; i--) {
      cells.push(new Date(calYear, calMonth - 1, daysInPrevMonth - i));
    }
    for (let d = 1; d <= daysInMonth; d++) {
      cells.push(new Date(calYear, calMonth, d));
    }
    let nextDay = 1;
    while (cells.length % 7 !== 0) {
      cells.push(new Date(calYear, calMonth + 1, nextDay++));
    }

    const todayKey = formatDateKey(today);

    document.getElementById('calendarDays').innerHTML = cells.map((dateObj) => {
      const dateKey = formatDateKey(dateObj);
      const otherMonth = dateObj.getMonth() !== calMonth;
      const daySessions = sessions.filter((s) => s.session_date === dateKey);
      const chips = daySessions.slice(0, 3).map((s) => `
        <button type="button" class="calendar-event-chip" data-open-session="${s.id}" title="${escapeHtml(s.title)}">${s.start_time ? formatTime(s.start_time) + ' ' : ''}${escapeHtml(s.title)}</button>
      `).join('');
      const more = daySessions.length > 3 ? `<div class="calendar-event-more">+${daySessions.length - 3} more</div>` : '';
      return `
        <div class="calendar-day ${otherMonth ? 'is-other-month' : ''} ${dateKey === todayKey ? 'is-today' : ''}" data-day-date="${dateKey}">
          <span class="calendar-day-number">${dateObj.getDate()}</span>
          ${chips}${more}
        </div>
      `;
    }).join('');
  };

  const loadSessions = async () => {
    try {
      sessions = await ArckAPI.request(`/staff/me/batches/${batchId}/sessions`);
    } catch (_) {
      sessions = [];
    }
    renderCalendar();
  };

  document.getElementById('calPrevBtn').addEventListener('click', () => {
    calMonth--; if (calMonth < 0) { calMonth = 11; calYear--; }
    renderCalendar();
  });
  document.getElementById('calNextBtn').addEventListener('click', () => {
    calMonth++; if (calMonth > 11) { calMonth = 0; calYear++; }
    renderCalendar();
  });
  document.getElementById('calTodayBtn').addEventListener('click', () => {
    calYear = today.getFullYear(); calMonth = today.getMonth();
    renderCalendar();
  });

  /* ---------- Schedule Class modal (create/edit/delete) ---------- */
  let editingSessionId = null;
  const modalBackdrop = document.getElementById('sessionModalBackdrop');

  const openSessionModal = (prefillDate, session) => {
    editingSessionId = session ? session.id : null;
    document.getElementById('sessionModalTitle').textContent = session ? 'Edit Class' : 'Schedule Class';
    document.getElementById('sessionTitle').value = session ? session.title : '';
    document.getElementById('sessionDate').value = session ? session.session_date : prefillDate;
    document.getElementById('sessionStartTime').value = (session && session.start_time) ? session.start_time.slice(0, 5) : '';
    document.getElementById('sessionEndTime').value = (session && session.end_time) ? session.end_time.slice(0, 5) : '';
    document.getElementById('sessionMeetingLink').value = (session && session.meeting_link) || '';
    document.getElementById('sessionNotes').value = (session && session.notes) || '';
    document.getElementById('sessionDeleteBtn').style.display = session ? 'inline-flex' : 'none';
    document.getElementById('sessionFormError').style.display = 'none';
    modalBackdrop.classList.add('is-open');
  };
  const closeSessionModal = () => modalBackdrop.classList.remove('is-open');

  document.getElementById('calendarDays').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-open-session]');
    if (chip) {
      const session = sessions.find((s) => s.id === Number(chip.dataset.openSession));
      if (session) openSessionModal(null, session);
      return;
    }
    const dayCell = e.target.closest('[data-day-date]');
    if (dayCell) openSessionModal(dayCell.dataset.dayDate, null);
  });

  document.getElementById('sessionModalCloseBtn').addEventListener('click', closeSessionModal);
  document.getElementById('sessionCancelBtn').addEventListener('click', closeSessionModal);
  modalBackdrop.addEventListener('click', (e) => { if (e.target === modalBackdrop) closeSessionModal(); });

  document.getElementById('sessionSaveBtn').addEventListener('click', async () => {
    const errorBox = document.getElementById('sessionFormError');
    errorBox.style.display = 'none';
    const title = document.getElementById('sessionTitle').value.trim();
    const date = document.getElementById('sessionDate').value;
    if (!title || !date) {
      errorBox.textContent = 'Title and date are required.';
      errorBox.style.display = 'block';
      return;
    }
    const body = {
      title, session_date: date,
      start_time: document.getElementById('sessionStartTime').value || null,
      end_time: document.getElementById('sessionEndTime').value || null,
      meeting_link: document.getElementById('sessionMeetingLink').value.trim() || null,
      notes: document.getElementById('sessionNotes').value.trim() || null,
    };
    try {
      if (editingSessionId) {
        await ArckAPI.request(`/staff/me/batches/${batchId}/sessions/${editingSessionId}`, { method: 'PATCH', body });
      } else {
        await ArckAPI.request(`/staff/me/batches/${batchId}/sessions`, { method: 'POST', body });
      }
      closeSessionModal();
      await loadSessions();
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not save this class.';
      errorBox.style.display = 'block';
    }
  });

  document.getElementById('sessionDeleteBtn').addEventListener('click', async () => {
    if (!editingSessionId) return;
    if (!window.confirm('Delete this class? This cannot be undone.')) return;
    try {
      await ArckAPI.request(`/staff/me/batches/${batchId}/sessions/${editingSessionId}`, { method: 'DELETE' });
      closeSessionModal();
      await loadSessions();
    } catch (err) {
      window.alert(err.detail || 'Could not delete this class.');
    }
  });

  /* ---------- Batch chat ---------- */
  const chatMessagesEl = document.getElementById('batchChatMessages');

  const appendBatchMessage = (m) => {
    const bubble = document.createElement('div');
    const isMe = m.sender_id === user.id;
    bubble.className = `chat-bubble ${isMe ? 'is-me' : 'is-them'}`;
    const time = new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    bubble.innerHTML = `
      <div class="chat-bubble-meta">${isMe ? 'You' : escapeHtml(m.sender_name)} &middot; ${time}</div>
      <div class="chat-bubble-body">${escapeHtml(m.message)}</div>
    `;
    chatMessagesEl.appendChild(bubble);
    chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
  };

  const loadChat = async () => {
    try {
      const messages = await ArckAPI.request(`/staff/me/batches/${batchId}/chat/messages`);
      chatMessagesEl.innerHTML = messages.length ? '' : '<div class="admin-panel-empty">No messages yet. Say hello to your batch!</div>';
      messages.forEach(appendBatchMessage);
      updateChatBadge(0);
    } catch (_) {
      chatMessagesEl.innerHTML = '<div class="admin-panel-empty">Couldn\'t load the chat.</div>';
    }
  };

  document.getElementById('batchChatForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = document.getElementById('batchChatInput');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    try {
      await ArckAPI.request(`/staff/me/batches/${batchId}/chat/messages`, { method: 'POST', body: { message: text } });
      // appended via the live WS echo below, same as it arrives for every recipient
    } catch (err) {
      window.alert(err.detail || 'Could not send this message.');
    }
  });

  window.addEventListener('arck-ws-message', (e) => {
    const data = e.detail;
    if (data.type !== 'batch_message' || data.batch_id !== batchId) return;
    const chatTabActive = document.querySelector('.workspace-tab[data-tab="chat"]').classList.contains('is-active');
    if (chatTabActive) {
      appendBatchMessage(data.message);
    } else if (data.message.sender_id !== user.id) {
      updateChatBadge(liveUnreadCount + 1);
    }
  });

  /* ---------- Meeting ---------- */
  document.getElementById('joinBatchMeetingBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const room = await ArckAPI.request(`/staff/me/batches/${batchId}/video`);
      ArckVideo.openRoom({ roomName: room.room_name, displayName: room.display_name, subject: room.subject, shareable: true });
    } catch (err) {
      window.alert(err.detail || 'Could not open this batch\'s video room.');
    } finally {
      btn.disabled = false;
    }
  });

  /* ---------- Class Details Updation: Study Materials ---------- */
  const materialsBody = document.getElementById('materialsTableBody');
  const materialRowHtml = (m) => `
    <tr>
      <td>${escapeHtml(m.title)}</td>
      <td><a href="${escapeHtml(m.file_url)}" target="_blank" rel="noopener">Open</a></td>
      <td>${m.description ? escapeHtml(m.description) : '—'}</td>
      <td>${formatDate(m.created_at.slice(0, 10))}</td>
      <td><button type="button" class="table-action-btn is-danger" data-delete-material="${m.id}" title="Delete"><i class="fa-solid fa-trash"></i></button></td>
    </tr>
  `;
  const loadMaterials = async () => {
    try {
      const materials = await ArckAPI.request(`/staff/me/batches/${batchId}/materials`);
      materialsBody.innerHTML = materials.length ? materials.map(materialRowHtml).join('') : '<tr><td colspan="5" class="admin-panel-empty">No materials uploaded yet.</td></tr>';
    } catch (_) {
      materialsBody.innerHTML = '<tr><td colspan="5" class="admin-panel-empty">Couldn\'t load materials.</td></tr>';
    }
  };
  materialsBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-delete-material]');
    if (!btn) return;
    if (!window.confirm('Delete this material?')) return;
    btn.disabled = true;
    try {
      await ArckAPI.request(`/staff/me/batches/${batchId}/materials/${btn.dataset.deleteMaterial}`, { method: 'DELETE' });
      await loadMaterials();
    } catch (err) {
      window.alert(err.detail || 'Could not delete this material.');
      btn.disabled = false;
    }
  });
  document.getElementById('uploadMaterialForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errorBox = document.getElementById('materialFormError');
    errorBox.style.display = 'none';
    const submitBtn = document.getElementById('materialSubmitBtn');
    submitBtn.disabled = true;
    const body = {
      title: document.getElementById('materialTitle').value.trim(),
      file_url: document.getElementById('materialUrl').value.trim(),
      description: document.getElementById('materialDescription').value.trim() || null,
    };
    try {
      await ArckAPI.request(`/staff/me/batches/${batchId}/materials`, { method: 'POST', body });
      e.target.reset();
      await loadMaterials();
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not upload this material.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

  /* ---------- Class Details Updation: Class Videos ---------- */
  const videosBody = document.getElementById('videosTableBody');
  const videoRowHtml = (v) => `
    <tr>
      <td>${escapeHtml(v.title)}</td>
      <td><a href="${escapeHtml(v.video_url)}" target="_blank" rel="noopener">Open</a></td>
      <td>${v.description ? escapeHtml(v.description) : '—'}</td>
      <td>${formatDate(v.created_at.slice(0, 10))}</td>
      <td><button type="button" class="table-action-btn is-danger" data-delete-video="${v.id}" title="Delete"><i class="fa-solid fa-trash"></i></button></td>
    </tr>
  `;
  const loadVideos = async () => {
    try {
      const videos = await ArckAPI.request(`/staff/me/batches/${batchId}/videos`);
      videosBody.innerHTML = videos.length ? videos.map(videoRowHtml).join('') : '<tr><td colspan="5" class="admin-panel-empty">No videos uploaded yet.</td></tr>';
    } catch (_) {
      videosBody.innerHTML = '<tr><td colspan="5" class="admin-panel-empty">Couldn\'t load videos.</td></tr>';
    }
  };
  videosBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-delete-video]');
    if (!btn) return;
    if (!window.confirm('Delete this video?')) return;
    btn.disabled = true;
    try {
      await ArckAPI.request(`/staff/me/batches/${batchId}/videos/${btn.dataset.deleteVideo}`, { method: 'DELETE' });
      await loadVideos();
    } catch (err) {
      window.alert(err.detail || 'Could not delete this video.');
      btn.disabled = false;
    }
  });
  document.getElementById('uploadVideoForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errorBox = document.getElementById('videoFormError');
    errorBox.style.display = 'none';
    const submitBtn = document.getElementById('videoSubmitBtn');
    submitBtn.disabled = true;
    const body = {
      title: document.getElementById('videoTitle').value.trim(),
      video_url: document.getElementById('videoUrl').value.trim(),
      description: document.getElementById('videoDescription').value.trim() || null,
    };
    try {
      await ArckAPI.request(`/staff/me/batches/${batchId}/videos`, { method: 'POST', body });
      e.target.reset();
      await loadVideos();
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not upload this video.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

  await loadBatchHeader();
  await loadSessions();
  await loadMaterials();
  await loadVideos();

});
