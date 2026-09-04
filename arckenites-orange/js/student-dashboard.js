const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDate = (iso) => iso ? new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const formatTime = (t) => t ? new Date(`1970-01-01T${t}`).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—';
const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
const formatFileSize = (bytes) => bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

const CATEGORY_LABEL = {
  account_login: 'Account / Login', course_access: 'Course Access', batch: 'Batch', classes_schedule: 'Classes / Schedule',
  assignments: 'Assignments', assessments: 'Assessments', certificates: 'Certificates', payment_fees: 'Payment / Fees',
  technical_issue: 'Technical Issue', trainer_training: 'Trainer / Training', other: 'Other',
};
const STATUS_LABEL = { open: 'Open', in_progress: 'In Progress', waiting_for_student: 'Waiting for Student', resolved: 'Resolved', closed: 'Closed' };
const STATUS_BADGE = { open: 'is-danger', in_progress: 'is-info', waiting_for_student: 'is-muted', resolved: 'is-success', closed: 'is-muted' };
const PRIORITY_LABEL = { low: 'Low', medium: 'Medium', high: 'High' };
const PRIORITY_BADGE = { low: 'is-muted', medium: 'is-info', high: 'is-danger' };

/* Keep in sync with BatchStatusEnum in backend/app/models/batch.py */
const BATCH_STATUS_LABEL = { upcoming: 'Upcoming', active: 'Active', paused: 'Paused', completed: 'Completed', cancelled: 'Cancelled' };
const BATCH_STATUS_BADGE = { upcoming: 'is-info', active: 'is-success', paused: 'is-pending', completed: 'is-muted', cancelled: 'is-danger' };

/* Keep in sync with PROGRAM_LABELS in backend/app/models/student.py */
const PROGRAM_LABELS = {
  official_certification: 'Official Certification Program',
  corporate_training: 'Corporate Training Program',
  institutional: 'Institutional Program',
  placement_training: 'Placement Training Program',
  trainers_program: 'Arckenites Trainers Program',
  internship: 'Internship Program',
  interview_crack: "Interview 'n' Crack Program",
  job_assist: 'Arckenites Job Assist Program',
  college_projects: 'College Projects Program',
};

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('student');
  if (!user) return; // requireRole already redirected

  if (user.program !== 'institutional') {
    // Plain layout: no sidebar yet for this student's program.
    document.getElementById('simpleLayout').style.display = 'block';
    document.getElementById('simpleWelcome').textContent = `Welcome, ${user.full_name}`;
    document.getElementById('simpleLogoutBtn').addEventListener('click', () => ArckAuth.logout());
    return;
  }

  /* ---------- Sidebar layout (Institutional Program) ---------- */
  document.getElementById('sidebarLayout').style.display = 'flex';
  document.getElementById('studentLogoutBtn').addEventListener('click', () => ArckAuth.logout());

  /* ---------- Topbar profile dropdown (photo + name, same pattern as the admin portal) ---------- */
  const renderTopbarProfile = () => {
    document.getElementById('studentAvatarWrap').innerHTML = ArckAPI.avatarHtml(user.full_name, user.photo_url, 36);
    document.getElementById('studentAvatarWrapMenu').innerHTML = ArckAPI.avatarHtml(user.full_name, user.photo_url, 40);
    document.getElementById('studentProfileName').textContent = user.full_name;
  };
  renderTopbarProfile();

  const profileTrigger = document.getElementById('studentProfileTrigger');
  const profilePanel = document.getElementById('studentProfilePanel');
  profileTrigger.addEventListener('click', (e) => {
    e.stopPropagation();
    profilePanel.classList.toggle('is-open');
  });
  document.addEventListener('click', () => profilePanel.classList.remove('is-open'));
  document.getElementById('studentProfileLogout').addEventListener('click', () => ArckAuth.logout());
  document.getElementById('studentProfileSettingsLink').addEventListener('click', () => {
    profilePanel.classList.remove('is-open');
    activatePanel('settings');
  });

  // One shared, hidden file input for the whole page — the avatar control
  // inside the Profile page is the only thing that opens it now (clicking
  // the topbar/menu avatar just navigates to Profile — see below). Wiring
  // `change`/upload here ONCE (rather than inside a render function that
  // re-runs on every photo change) matters: this element is never
  // recreated, so re-attaching the listener there would stack duplicate
  // uploads per file picked.
  const photoInput = document.getElementById('profilePhotoInput');
  const uploadPhoto = async (file) => {
    try {
      const result = await ArckAPI.uploadFile('/users/me/photo', file);
      user.photo_url = result.photo_url;
      ArckAPI.setSession(ArckAPI.getToken(), user);
      renderTopbarProfile();
      renderProfilePhoto();
    } catch (err) {
      alert(err.message || 'Could not upload photo.');
    }
  };
  const removePhoto = async () => {
    try {
      await ArckAPI.request('/users/me/photo', { method: 'DELETE' });
      user.photo_url = null;
      ArckAPI.setSession(ArckAPI.getToken(), user);
      renderTopbarProfile();
      renderProfilePhoto();
    } catch (err) {
      alert(err.message || 'Could not remove photo.');
    }
  };
  photoInput.addEventListener('change', () => {
    const file = photoInput.files[0];
    photoInput.value = '';
    if (file) uploadPhoto(file);
  });
  // Clicking the avatar (topbar or dropdown menu) goes to the Profile page —
  // it does NOT upload a photo directly. Uploading only happens from the
  // dedicated avatar control inside the Profile page itself.
  document.getElementById('studentAvatarBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    activatePanel('settings');
  });
  document.getElementById('studentAvatarMenuBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    profilePanel.classList.remove('is-open');
    activatePanel('settings');
  });

  // Profile page — this is the student's OWN view (via /auth/me), so it's
  // the one place username/email/phone/student ID are shown; nowhere else
  // in the portal exposes these for any student, including this one.
  const renderProfilePhoto = () => {
    document.getElementById('profilePhotoTrigger').innerHTML = ArckAPI.avatarHtml(user.full_name, user.photo_url, 96);
    document.getElementById('profilePhotoCaption').textContent = user.photo_url ? 'Change Photo' : 'Upload Photo';
    document.getElementById('profilePhotoRemoveBtn').style.display = user.photo_url ? 'inline-block' : 'none';
  };
  document.getElementById('profilePhotoTrigger').addEventListener('click', () => photoInput.click());
  document.getElementById('profilePhotoCaption').addEventListener('click', () => photoInput.click());
  document.getElementById('profilePhotoRemoveBtn').addEventListener('click', removePhoto);
  renderProfilePhoto();

  // Name/Email/Phone/Address start disabled (view-only); "Edit" enables
  // them, and the same button (now labeled "Save") submits the PATCH.
  const nameInput = document.getElementById('profileNameInput');
  const emailInput = document.getElementById('profileEmailInput');
  const phoneInput = document.getElementById('profilePhoneInput');
  const addressInput = document.getElementById('profileAddressInput');
  const editSaveBtn = document.getElementById('profileEditSaveBtn');
  const editError = document.getElementById('profileEditError');
  const editableInputs = [nameInput, emailInput, phoneInput, addressInput];
  let isEditingProfile = false;

  const fillProfileFormFromUser = () => {
    nameInput.value = user.full_name || '';
    emailInput.value = user.email || '';
    phoneInput.value = user.phone || '';
    addressInput.value = user.address || '';
  };
  fillProfileFormFromUser();

  document.getElementById('profileEditForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    editError.style.display = 'none';

    if (!isEditingProfile) {
      isEditingProfile = true;
      editableInputs.forEach((input) => { input.disabled = false; });
      editSaveBtn.textContent = 'Save';
      nameInput.focus();
      return;
    }

    editSaveBtn.disabled = true;
    try {
      const updated = await ArckAPI.request('/students/me/profile', {
        method: 'PATCH',
        body: {
          full_name: nameInput.value.trim(),
          email: emailInput.value.trim() || null,
          phone: phoneInput.value.trim() || null,
          address: addressInput.value.trim() || null,
        },
      });
      Object.assign(user, updated);
      ArckAPI.setSession(ArckAPI.getToken(), user);
      renderTopbarProfile();
      renderAccountDetails();
      fillProfileFormFromUser();
      isEditingProfile = false;
      editableInputs.forEach((input) => { input.disabled = true; });
      editSaveBtn.textContent = 'Edit';
    } catch (err) {
      editError.textContent = err.message || 'Could not save your profile.';
      editError.style.display = 'block';
    } finally {
      editSaveBtn.disabled = false;
    }
  });

  // Read-only account info that isn't user-editable here — kept separate
  // from the editable form above. Re-rendered once batches are loaded (for
  // Batch/Status) and whenever the profile is saved.
  const renderAccountDetails = () => {
    const primaryBatch = myBatches[0];
    document.getElementById('profileAccountDetailsBody').innerHTML = `
      <div class="detail-row"><span class="form-label">Username</span><span>${escapeHtml(user.username)}</span></div>
      <div class="detail-row"><span class="form-label">Student ID</span><span>${escapeHtml(user.student_id || 'Not assigned yet')}</span></div>
      <div class="detail-row"><span class="form-label">Program</span><span>${escapeHtml(PROGRAM_LABELS[user.program] || 'Not selected yet')}</span></div>
      <div class="detail-row"><span class="form-label">Batch</span><span>${myBatches.length ? escapeHtml(myBatches.map((b) => b.name).join(', ')) : 'Not assigned yet'}</span></div>
      ${primaryBatch ? `<div class="detail-row"><span class="form-label">Status</span><span class="admin-activity-badge ${BATCH_STATUS_BADGE[primaryBatch.status] || 'is-muted'}">${BATCH_STATUS_LABEL[primaryBatch.status] || primaryBatch.status}</span></div>` : ''}
      <div class="detail-row"><span class="form-label">Account</span><span class="admin-activity-badge ${user.is_active ? 'is-success' : 'is-danger'}">${user.is_active ? 'Active' : 'Inactive'}</span></div>
      <div class="detail-row"><span class="form-label"></span><a class="btn btn-primary-outline" href="change-password.html" style="margin-top:6px;">Change Password</a></div>
    `;
  };

  // Cached from the loaders below, so the Overview panel can reuse the same
  // data instead of re-fetching it.
  let myBatches = [];
  let mySchedule = [];
  let myTickets = [];

  /* ---------- Sidebar: mobile off-canvas ---------- */
  const sidebar = document.getElementById('studentSidebar');
  const backdrop = document.getElementById('adminSidebarBackdrop');
  const openMobileSidebar = () => { sidebar.classList.add('is-mobile-open'); backdrop.classList.add('is-visible'); };
  const closeMobileSidebar = () => { sidebar.classList.remove('is-mobile-open'); backdrop.classList.remove('is-visible'); };
  document.getElementById('adminMobileToggle').addEventListener('click', openMobileSidebar);
  backdrop.addEventListener('click', closeMobileSidebar);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMobileSidebar(); });

  /* ---------- Panel switching ---------- */
  const navButtons = sidebar.querySelectorAll('.admin-sidebar-link[data-panel]');
  const panels = document.querySelectorAll('main.admin-main > section[data-panel]');

  // Shared by the sidebar nav, the overview's data-goto-panel shortcuts, and
  // the topbar profile dropdown's "Profile" link/avatar — "settings" has no
  // sidebar entry, so this can't just be navButtons.forEach + one click().
  const activatePanel = (name) => {
    navButtons.forEach((b) => b.classList.toggle('is-active', b.dataset.panel === name));
    panels.forEach((p) => { p.style.display = p.dataset.panel === name ? 'block' : 'none'; });
    closeMobileSidebar();
  };

  navButtons.forEach((btn) => {
    btn.addEventListener('click', () => activatePanel(btn.dataset.panel));
  });

  /* ---------- Support: ticket system ---------- */
  const ticketListView = document.getElementById('ticketListView');
  const ticketCreateView = document.getElementById('ticketCreateView');
  const ticketDetailView = document.getElementById('ticketDetailView');
  const myTicketsBody = document.getElementById('myTicketsBody');
  const supportSidebarBadge = document.getElementById('supportSidebarBadge');

  const showTicketView = (view) => {
    ticketListView.style.display = view === 'list' ? 'block' : 'none';
    ticketCreateView.style.display = view === 'create' ? 'block' : 'none';
    ticketDetailView.style.display = view === 'detail' ? 'block' : 'none';
  };

  document.getElementById('openSupportChatBtn').addEventListener('click', () => {
    document.getElementById('chatFloatBtn').click();
  });

  const ticketRowHtml = (t) => `
    <tr data-open-ticket="${t.id}" style="cursor:pointer;">
      <td>${t.unread ? '<i class="fa-solid fa-circle" style="color:var(--primary); font-size:.5rem; margin-right:6px;" title="Unread"></i>' : ''}${escapeHtml(t.ticket_number)}</td>
      <td>${escapeHtml(t.subject)}</td>
      <td>${CATEGORY_LABEL[t.category] || escapeHtml(t.category)}</td>
      <td><span class="admin-activity-badge ${PRIORITY_BADGE[t.priority] || 'is-muted'}">${PRIORITY_LABEL[t.priority] || t.priority}</span></td>
      <td><span class="admin-activity-badge ${STATUS_BADGE[t.status] || 'is-muted'}">${STATUS_LABEL[t.status] || t.status}</span></td>
      <td>${formatDateTime(t.updated_at)}</td>
    </tr>
  `;

  const loadTickets = async () => {
    try {
      const tickets = await ArckAPI.request('/students/me/tickets');
      myTicketsBody.innerHTML = tickets.length
        ? tickets.map(ticketRowHtml).join('')
        : `<tr><td colspan="6" class="admin-panel-empty">
             No support requests yet.<br><br>
             Need help with your course, account, or training?<br><br>
             <button type="button" class="btn btn-accent" id="emptyStateCreateTicketBtn"><i class="fa-solid fa-plus"></i> Create Support Ticket</button>
           </td></tr>`;

      const emptyBtn = document.getElementById('emptyStateCreateTicketBtn');
      if (emptyBtn) emptyBtn.addEventListener('click', () => showTicketView('create'));

      myTickets = tickets;

      const unreadCount = tickets.filter((t) => t.unread).length;
      if (unreadCount > 0) {
        supportSidebarBadge.textContent = unreadCount > 9 ? '9+' : String(unreadCount);
        supportSidebarBadge.style.display = 'inline-flex';
      } else {
        supportSidebarBadge.style.display = 'none';
      }
    } catch (_) {
      myTicketsBody.innerHTML = '<tr><td colspan="6" class="admin-panel-empty">Couldn\'t load your support tickets.</td></tr>';
    }
  };

  myTicketsBody.addEventListener('click', (e) => {
    const row = e.target.closest('[data-open-ticket]');
    if (!row) return;
    openTicketDetail(Number(row.dataset.openTicket));
  });

  /* ---------- Create ticket ---------- */
  document.getElementById('showCreateTicketBtn').addEventListener('click', () => showTicketView('create'));
  document.getElementById('cancelCreateTicketBtn').addEventListener('click', () => showTicketView('list'));

  const createTicketForm = document.getElementById('createTicketForm');
  const createTicketError = document.getElementById('createTicketError');
  const createTicketSubmitBtn = document.getElementById('createTicketSubmitBtn');

  createTicketForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    createTicketError.style.display = 'none';
    createTicketSubmitBtn.disabled = true;

    const body = {
      subject: document.getElementById('ticketSubject').value.trim(),
      category: document.getElementById('ticketCategory').value,
      priority: document.getElementById('ticketPriority').value,
      description: document.getElementById('ticketDescription').value.trim(),
    };

    try {
      const ticket = await ArckAPI.request('/students/me/tickets', { method: 'POST', body });

      const fileInput = document.getElementById('ticketAttachment');
      const file = fileInput.files[0];
      if (file) {
        const firstMessageId = ticket.messages[0].id;
        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch(`${API_BASE}/students/me/tickets/${ticket.id}/messages/${firstMessageId}/attachments`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${ArckAPI.getToken()}` },
          body: formData,
        });
        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}));
          window.alert(errBody.detail || 'Your ticket was created, but the attachment could not be uploaded.');
        }
      }

      createTicketForm.reset();
      await loadTickets();
      openTicketDetail(ticket.id);
    } catch (err) {
      createTicketError.textContent = err.detail || 'Could not create this ticket. Please try again.';
      createTicketError.style.display = 'block';
    } finally {
      createTicketSubmitBtn.disabled = false;
    }
  });

  /* ---------- Ticket detail ---------- */
  let currentTicketId = null;

  const ticketMessageHtml = (m, ticketId) => {
    const roleClass = m.sender_type === 'student' ? 'is-student' : 'is-admin';
    const attachmentsHtml = m.attachments.length
      ? `<div class="ticket-message-attachments">${m.attachments.map((a) => `
          <a href="#" data-download-attachment="${a.id}" data-ticket="${ticketId}">
            <i class="fa-solid fa-paperclip"></i> ${escapeHtml(a.file_name)} (${formatFileSize(a.file_size)})
          </a>
        `).join('')}</div>`
      : '';
    return `
      <div class="ticket-message ${roleClass}">
        <div class="ticket-message-meta"><strong>${escapeHtml(m.sender_name)}</strong> &middot; ${formatDateTime(m.created_at)}</div>
        <div>${escapeHtml(m.message)}</div>
        ${attachmentsHtml}
      </div>
    `;
  };

  const openTicketDetail = async (ticketId) => {
    currentTicketId = ticketId;
    showTicketView('detail');
    document.getElementById('ticketDetailSubject').textContent = 'Loading…';

    try {
      const ticket = await ArckAPI.request(`/students/me/tickets/${ticketId}`);
      document.getElementById('ticketDetailBreadcrumb').textContent = ticket.ticket_number;
      document.getElementById('ticketDetailSubject').textContent = ticket.subject;
      document.getElementById('ticketDetailStatus').innerHTML = `<span class="admin-activity-badge ${STATUS_BADGE[ticket.status] || 'is-muted'}">${STATUS_LABEL[ticket.status] || ticket.status}</span>`;
      document.getElementById('ticketDetailPriority').innerHTML = `<span class="admin-activity-badge ${PRIORITY_BADGE[ticket.priority] || 'is-muted'}">${PRIORITY_LABEL[ticket.priority] || ticket.priority}</span>`;
      document.getElementById('ticketDetailCategory').textContent = CATEGORY_LABEL[ticket.category] || ticket.category;

      const thread = document.getElementById('studentConversationThread');
      thread.innerHTML = ticket.messages.map((m) => ticketMessageHtml(m, ticket.id)).join('');
      thread.scrollTop = thread.scrollHeight;

      thread.querySelectorAll('[data-download-attachment]').forEach((a) => {
        a.addEventListener('click', async (e) => {
          e.preventDefault();
          try {
            const res = await fetch(`${API_BASE}/students/me/tickets/${a.dataset.ticket}/attachments/${a.dataset.downloadAttachment}`, {
              headers: { Authorization: `Bearer ${ArckAPI.getToken()}` },
            });
            if (!res.ok) throw new Error('download failed');
            const blob = await res.blob();
            window.open(URL.createObjectURL(blob), '_blank');
          } catch (_) {
            window.alert('Could not download this attachment.');
          }
        });
      });

      const isClosed = ticket.status === 'closed';
      document.getElementById('studentReplyPanel').style.display = isClosed ? 'none' : 'block';
      document.getElementById('ticketClosedNotice').style.display = isClosed ? 'block' : 'none';
      document.getElementById('reopenMyTicketBtn').style.display = isClosed ? 'inline-flex' : 'none';

      await loadTickets(); // refresh unread badge now that this ticket's been read
    } catch (_) {
      document.getElementById('ticketDetailSubject').textContent = 'Ticket not found';
    }
  };

  document.getElementById('backToTicketListLink').addEventListener('click', (e) => {
    e.preventDefault();
    showTicketView('list');
  });

  const studentReplyForm = document.getElementById('studentReplyForm');
  const studentReplyError = document.getElementById('studentReplyError');
  const studentReplySubmitBtn = document.getElementById('studentReplySubmitBtn');

  studentReplyForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    studentReplyError.style.display = 'none';
    studentReplySubmitBtn.disabled = true;

    try {
      const message = await ArckAPI.request(`/students/me/tickets/${currentTicketId}/messages`, {
        method: 'POST', body: { message: document.getElementById('studentReplyMessage').value.trim() },
      });

      const fileInput = document.getElementById('studentReplyAttachment');
      const file = fileInput.files[0];
      if (file) {
        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch(`${API_BASE}/students/me/tickets/${currentTicketId}/messages/${message.id}/attachments`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${ArckAPI.getToken()}` },
          body: formData,
        });
        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}));
          window.alert(errBody.detail || 'Your reply was sent, but the attachment could not be uploaded.');
        }
      }

      studentReplyForm.reset();
      await openTicketDetail(currentTicketId);
    } catch (err) {
      studentReplyError.textContent = err.detail || 'Could not send this reply.';
      studentReplyError.style.display = 'block';
    } finally {
      studentReplySubmitBtn.disabled = false;
    }
  });

  document.getElementById('reopenMyTicketBtn').addEventListener('click', async () => {
    try {
      await ArckAPI.request(`/students/me/tickets/${currentTicketId}/reopen`, { method: 'POST' });
      await openTicketDetail(currentTicketId);
    } catch (err) {
      window.alert(err.detail || 'Could not reopen this ticket.');
    }
  });

  /* ---------- My Batch Meetings — join-anytime, no class needed ---------- */
  const batchMeetingsList = document.getElementById('myBatchMeetingsList');

  const loadBatchMeetings = async () => {
    try {
      const batches = await ArckAPI.request('/students/me/batches');
      myBatches = batches;
      batchMeetingsList.innerHTML = batches.length
        ? batches.map((b) => `
            <div class="batch-student-row">
              <span>${escapeHtml(b.name)}</span>
              <span style="display:flex; gap:8px; align-items:center;">
                <button type="button" class="btn btn-primary-outline" data-chat-batch="${b.id}" data-chat-batch-name="${escapeHtml(b.name)}" style="position:relative;">
                  <i class="fa-solid fa-comments"></i> Chat
                  ${b.unread_chat_count > 0 ? `<span class="admin-notif-badge" style="position:absolute; top:-6px; right:-6px;">${b.unread_chat_count > 9 ? '9+' : b.unread_chat_count}</span>` : ''}
                </button>
                <button type="button" class="btn btn-accent" data-join-batch="${b.id}"><i class="fa-solid fa-video"></i> Join</button>
              </span>
            </div>
          `).join('')
        : '<p style="color:var(--muted);">You\'re not enrolled in any batch yet.</p>';
    } catch (_) {
      batchMeetingsList.innerHTML = '<p style="color:var(--muted);">Couldn\'t load your batches.</p>';
    }
  };

  batchMeetingsList.addEventListener('click', async (e) => {
    const joinBtn = e.target.closest('[data-join-batch]');
    if (joinBtn) {
      joinBtn.disabled = true;
      try {
        const room = await ArckAPI.request(`/students/me/batches/${joinBtn.dataset.joinBatch}/video`);
        ArckVideo.openRoom({ roomName: room.room_name, displayName: room.display_name, subject: room.subject });
      } catch (err) {
        window.alert(err.detail || 'Could not join this batch\'s meeting.');
      } finally {
        joinBtn.disabled = false;
      }
      return;
    }
    const chatBtn = e.target.closest('[data-chat-batch]');
    if (chatBtn) openBatchChat(Number(chatBtn.dataset.chatBatch), chatBtn.dataset.chatBatchName);
  });

  /* ---------- Batch group chat (modal) ---------- */
  const batchChatModalBackdrop = document.getElementById('batchChatModalBackdrop');
  const batchChatMessages = document.getElementById('batchChatMessages');
  let currentChatBatchId = null;

  const appendBatchMessage = (m) => {
    const bubble = document.createElement('div');
    const isMe = m.sender_id === user.id;
    bubble.className = `chat-bubble ${isMe ? 'is-me' : 'is-them'}`;
    const time = new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    bubble.innerHTML = `
      <div class="chat-bubble-meta">${isMe ? 'You' : escapeHtml(m.sender_name)} &middot; ${time}</div>
      <div class="chat-bubble-body">${escapeHtml(m.message)}</div>
    `;
    batchChatMessages.appendChild(bubble);
    batchChatMessages.scrollTop = batchChatMessages.scrollHeight;
  };

  const openBatchChat = async (batchId, batchName) => {
    currentChatBatchId = batchId;
    document.getElementById('batchChatModalTitle').textContent = `${batchName} — Chat`;
    batchChatMessages.innerHTML = '<div class="admin-panel-empty">Loading&hellip;</div>';
    batchChatModalBackdrop.classList.add('is-open');
    try {
      const messages = await ArckAPI.request(`/students/me/batches/${batchId}/chat/messages`);
      batchChatMessages.innerHTML = messages.length ? '' : '<div class="admin-panel-empty">No messages yet. Say hello to your batch!</div>';
      messages.forEach(appendBatchMessage);
      await loadBatchMeetings(); // refresh unread badge now that this batch's been read
    } catch (_) {
      batchChatMessages.innerHTML = '<div class="admin-panel-empty">Couldn\'t load the chat.</div>';
    }
  };

  const closeBatchChat = () => { batchChatModalBackdrop.classList.remove('is-open'); currentChatBatchId = null; };
  document.getElementById('batchChatModalCloseBtn').addEventListener('click', closeBatchChat);
  batchChatModalBackdrop.addEventListener('click', (e) => { if (e.target === batchChatModalBackdrop) closeBatchChat(); });

  document.getElementById('batchChatForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!currentChatBatchId) return;
    const input = document.getElementById('batchChatInput');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    try {
      await ArckAPI.request(`/students/me/batches/${currentChatBatchId}/chat/messages`, { method: 'POST', body: { message: text } });
    } catch (err) {
      window.alert(err.detail || 'Could not send this message.');
    }
  });

  window.addEventListener('arck-ws-message', (e) => {
    const data = e.detail;
    if (data.type !== 'batch_message') return;
    if (currentChatBatchId && data.batch_id === currentChatBatchId && batchChatModalBackdrop.classList.contains('is-open')) {
      appendBatchMessage(data.message);
    } else {
      loadBatchMeetings(); // pick up the new unread badge for whichever batch this was
    }
  });

  /* ---------- Class Schedule + Video Conference ---------- */
  const scheduleBody = document.getElementById('scheduleTableBody');

  const scheduleRowHtml = (s) => `
    <tr>
      <td>${escapeHtml(s.title)}</td>
      <td>${escapeHtml(s.batch_name)}</td>
      <td>${formatDate(s.session_date)}</td>
      <td>${(s.start_time && s.end_time) ? `${formatTime(s.start_time)} – ${formatTime(s.end_time)}` : '—'}</td>
      <td>${s.meeting_link ? `<a href="${escapeHtml(s.meeting_link)}" target="_blank" rel="noopener">Open Link</a>` : '—'}</td>
      <td><button type="button" class="btn btn-accent" data-join-session="${s.id}" data-subject="${escapeHtml(s.title)}"><i class="fa-solid fa-video"></i> Join</button></td>
      <td>${s.notes ? escapeHtml(s.notes) : '—'}</td>
    </tr>
  `;

  const loadSchedule = async () => {
    try {
      const sessions = await ArckAPI.request('/students/me/schedule');
      mySchedule = sessions;
      scheduleBody.innerHTML = sessions.length
        ? sessions.map(scheduleRowHtml).join('')
        : '<tr><td colspan="7" class="admin-panel-empty">No classes scheduled yet.</td></tr>';
    } catch (_) {
      scheduleBody.innerHTML = '<tr><td colspan="7" class="admin-panel-empty">Couldn\'t load your class schedule.</td></tr>';
    }
  };

  scheduleBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-join-session]');
    if (!btn) return;
    btn.disabled = true;
    try {
      const room = await ArckAPI.request(`/students/me/schedule/${btn.dataset.joinSession}/video`);
      await ArckVideo.openRoom({ roomName: room.room_name, displayName: room.display_name, subject: room.subject });
    } catch (err) {
      window.alert(err.detail || 'Could not join this class\'s video call.');
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById('startInstantMeetingBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const room = await ArckAPI.request('/me/video/ad-hoc', { method: 'POST' });
      await ArckVideo.openRoom({ roomName: room.room_name, displayName: room.display_name, subject: room.subject, shareable: true });
    } catch (err) {
      window.alert(err.detail || 'Could not start a meeting.');
    } finally {
      btn.disabled = false;
    }
  });

  /* ---------- Study Materials ---------- */
  const materialsBody = document.getElementById('materialsTableBody');

  const loadMaterials = async () => {
    try {
      const materials = await ArckAPI.request('/students/me/materials');
      materialsBody.innerHTML = materials.length
        ? materials.map((m) => `
            <tr>
              <td>${escapeHtml(m.batch_name)}</td>
              <td>${escapeHtml(m.title)}</td>
              <td><a href="${escapeHtml(m.file_url)}" target="_blank" rel="noopener">Open</a></td>
              <td>${m.description ? escapeHtml(m.description) : '—'}</td>
              <td>${formatDate(m.created_at.slice(0, 10))}</td>
            </tr>
          `).join('')
        : '<tr><td colspan="5" class="admin-panel-empty">No study materials posted yet.</td></tr>';
    } catch (_) {
      materialsBody.innerHTML = '<tr><td colspan="5" class="admin-panel-empty">Couldn\'t load study materials.</td></tr>';
    }
  };

  /* ---------- Class Videos ---------- */
  const videosBody = document.getElementById('videosTableBody');

  const loadVideos = async () => {
    try {
      const videos = await ArckAPI.request('/students/me/videos');
      videosBody.innerHTML = videos.length
        ? videos.map((v) => `
            <tr>
              <td>${escapeHtml(v.batch_name)}</td>
              <td>${escapeHtml(v.title)}</td>
              <td><a href="${escapeHtml(v.video_url)}" target="_blank" rel="noopener">Open</a></td>
              <td>${v.description ? escapeHtml(v.description) : '—'}</td>
              <td>${formatDate(v.created_at.slice(0, 10))}</td>
            </tr>
          `).join('')
        : '<tr><td colspan="5" class="admin-panel-empty">No class videos posted yet.</td></tr>';
    } catch (_) {
      videosBody.innerHTML = '<tr><td colspan="5" class="admin-panel-empty">Couldn\'t load class videos.</td></tr>';
    }
  };

  /* ---------- Lab Access Lock/Unlock Status ---------- */
  const labAccessBanner = document.getElementById('labAccessStatusBanner');
  const labPanelSection = document.querySelector('section[data-panel="lab"]');

  const loadLabAccessStatus = async () => {
    try {
      const state = await ArckAPI.request('/students/me/lab-access-status');
      if (state.status === 'UNLOCKED') {
        labAccessBanner.className = 'lab-access-banner is-unlocked';
        labAccessBanner.innerHTML = '<div><i class="fa-solid fa-lock-open"></i> <strong>🔓 Lab Access Available</strong><p>You can enter and use the Lab now.</p></div>';
      } else {
        labAccessBanner.className = 'lab-access-banner is-locked';
        labAccessBanner.innerHTML = '<div><i class="fa-solid fa-lock"></i> <strong>🔒 Lab Access Locked</strong><p>Your lab access is currently locked. Please wait until your scheduled lab slot or contact the administrator.</p></div>';
      }
    } catch (_) {
      labAccessBanner.className = 'lab-access-banner is-locked';
      labAccessBanner.innerHTML = '<div><i class="fa-solid fa-triangle-exclamation"></i> <strong>Couldn\'t check lab access.</strong><p>Refresh the page, or contact the administrator if this keeps happening.</p></div>';
    }
  };

  // Refresh while the Lab panel is open, so a status change made by an
  // admin shows up without the student needing to log out/in.
  setInterval(() => {
    if (labPanelSection.style.display !== 'none') loadLabAccessStatus();
  }, 20000);

  /* ---------- Lab Slot Booking ---------- */
  const labWeekSummaryEl = document.getElementById('labWeekSummary');
  const labSlotsListEl = document.getElementById('labSlotsList');
  const labMyBookingsBody = document.getElementById('labMyBookingsBody');

  const formatSlotTime = (t) => {
    const [h, m] = t.split(':').map(Number);
    const period = h >= 12 ? 'PM' : 'AM';
    const h12 = h % 12 === 0 ? 12 : h % 12;
    return `${h12}:${String(m).padStart(2, '0')} ${period}`;
  };

  const renderMyLabBookings = (bookings) => {
    labMyBookingsBody.innerHTML = bookings.length
      ? bookings.map((b) => `
          <tr>
            <td>${formatDate(b.slot_date)}</td>
            <td>${formatSlotTime(b.start_time)} – ${formatSlotTime(b.end_time)}</td>
            <td><button type="button" class="btn btn-primary-outline" style="padding:4px 12px;" data-cancel-booking="${b.id}">Cancel</button></td>
          </tr>
        `).join('')
      : '<tr><td colspan="3" class="admin-panel-empty">No upcoming lab bookings.</td></tr>';
  };

  const loadLabSlots = async () => {
    try {
      const data = await ArckAPI.request('/students/me/lab-slots');
      const { slots, week_summary, my_bookings } = data;

      labWeekSummaryEl.innerHTML = `<strong>${week_summary.hours_booked} / ${week_summary.weekly_cap} hours</strong> booked this week (${formatDate(week_summary.week_start)} – ${formatDate(week_summary.week_end)}) — <strong>${week_summary.hours_remaining} hours remaining</strong>`;

      const byDate = new Map();
      slots.forEach((s) => {
        if (!byDate.has(s.slot_date)) byDate.set(s.slot_date, []);
        byDate.get(s.slot_date).push(s);
      });

      labSlotsListEl.innerHTML = Array.from(byDate.keys()).sort().map((d) => `
        <div class="lab-day-row">
          <div class="lab-day-label">${formatDate(d)}</div>
          <div class="lab-day-slots">
            ${byDate.get(d).map((s) => {
              const seatsLeft = s.capacity - s.booked_count;
              let label = `${seatsLeft} seat${seatsLeft === 1 ? '' : 's'} left`;
              let cls = '';
              let disabled = false;
              if (s.is_booked_by_me) { label = 'Booked'; cls = 'is-booked'; disabled = true; }
              else if (s.is_past) { label = 'Past'; cls = 'is-disabled'; disabled = true; }
              else if (s.is_full) { label = 'Full'; cls = 'is-disabled'; disabled = true; }
              else if (week_summary.hours_remaining < 2) { cls = 'is-disabled'; disabled = true; }
              return `<button type="button" class="lab-slot-btn ${cls}" ${disabled ? 'disabled' : ''} data-slot-date="${d}" data-start-time="${s.start_time}">
                <span class="lab-slot-time">${formatSlotTime(s.start_time)} – ${formatSlotTime(s.end_time)}</span>
                <span class="lab-slot-status">${label}</span>
              </button>`;
            }).join('')}
          </div>
        </div>
      `).join('');

      renderMyLabBookings(my_bookings);
    } catch (_) {
      labWeekSummaryEl.textContent = '';
      labSlotsListEl.innerHTML = '<div class="admin-panel-empty">Couldn\'t load lab slots.</div>';
      labMyBookingsBody.innerHTML = '<tr><td colspan="3" class="admin-panel-empty">Couldn\'t load your bookings.</td></tr>';
    }
  };

  labSlotsListEl.addEventListener('click', async (e) => {
    const btn = e.target.closest('.lab-slot-btn');
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    try {
      await ArckAPI.request('/students/me/lab-slots/book', {
        method: 'POST',
        body: { slot_date: btn.dataset.slotDate, start_time: btn.dataset.startTime },
      });
      await loadLabSlots();
    } catch (err) {
      window.alert(err.detail || 'Could not book this slot.');
      btn.disabled = false;
    }
  });

  labMyBookingsBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-cancel-booking]');
    if (!btn) return;
    if (!window.confirm('Cancel this lab slot booking?')) return;
    btn.disabled = true;
    try {
      await ArckAPI.request(`/students/me/lab-slots/${btn.dataset.cancelBooking}`, { method: 'DELETE' });
      await loadLabSlots();
    } catch (err) {
      window.alert(err.detail || 'Could not cancel this booking.');
      btn.disabled = false;
    }
  });

  /* ---------- Dashboard Overview ---------- */
  const overviewSection = document.querySelector('section[data-panel="overview"]');
  const nextClassBody = document.getElementById('nextClassBody');

  const joinNextClass = async (btn) => {
    btn.disabled = true;
    try {
      const room = await ArckAPI.request(`/students/me/schedule/${btn.dataset.joinNextClass}/video`);
      await ArckVideo.openRoom({ roomName: room.room_name, displayName: room.display_name, subject: room.subject });
    } catch (err) {
      window.alert(err.detail || 'Could not join this class\'s video call.');
    } finally {
      btn.disabled = false;
    }
  };

  overviewSection.addEventListener('click', (e) => {
    const gotoBtn = e.target.closest('[data-goto-panel]');
    if (gotoBtn) {
      activatePanel(gotoBtn.dataset.gotoPanel);
      return;
    }
    const joinBtn = e.target.closest('[data-join-next-class]');
    if (joinBtn) joinNextClass(joinBtn);
  });

  const loadOverview = async () => {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const todayISO = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    const tomorrowISO = `${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(tomorrow.getDate())}`;
    const label = (dateStr) => (dateStr === todayISO ? 'Today' : dateStr === tomorrowISO ? 'Tomorrow' : formatDate(dateStr));

    // Greeting
    const hour = now.getHours();
    const greeting = hour < 12 ? 'Good Morning' : hour < 18 ? 'Good Afternoon' : 'Good Evening';
    document.getElementById('overviewGreeting').textContent = `${greeting}, ${user.full_name.split(' ')[0]}`;
    document.getElementById('overviewSubline').textContent = myBatches.length
      ? `You're enrolled in ${myBatches.map((b) => b.name).join(', ')}. Continue your learning journey and stay on track.`
      : "You're not enrolled in a batch yet — once admin allocates one, it'll show up here.";

    // Shared derived data
    const upcomingSessions = mySchedule
      .filter((s) => s.session_date >= todayISO)
      .sort((a, b) => (a.session_date + (a.start_time || '')).localeCompare(b.session_date + (b.start_time || '')));
    const openTicketCount = myTickets.filter((t) => !['resolved', 'closed'].includes(t.status)).length;
    const heldCount = mySchedule.filter((s) => s.session_date <= todayISO).length;

    // KPIs
    document.getElementById('kpiBatches').textContent = String(myBatches.length);
    document.getElementById('kpiNextClass').textContent = upcomingSessions.length ? label(upcomingSessions[0].session_date) : 'None';
    document.getElementById('kpiOpenTickets').textContent = String(openTicketCount);
    document.getElementById('kpiClassesProgress').textContent = mySchedule.length ? `${Math.round((heldCount / mySchedule.length) * 100)}%` : '—';

    // Continue Learning — no content/module-position tracking exists yet, so this is an honest placeholder, not fake progress.
    const continueLearningBody = document.getElementById('continueLearningBody');
    continueLearningBody.className = '';
    continueLearningBody.innerHTML = `
      <div style="padding:20px;">
        <p style="color:var(--muted); margin-bottom:16px;">Course-by-course progress tracking is coming soon. For now, pick up where you left off in your Study Materials or Class Videos.</p>
        <div style="display:flex; gap:12px; flex-wrap:wrap;">
          <button type="button" class="btn btn-primary-outline" data-goto-panel="materials"><i class="fa-solid fa-book-open"></i> Study Materials</button>
          <button type="button" class="btn btn-primary-outline" data-goto-panel="videos"><i class="fa-solid fa-video"></i> Class Videos</button>
        </div>
      </div>
    `;

    // Next Class
    if (upcomingSessions.length) {
      const s = upcomingSessions[0];
      nextClassBody.className = '';
      nextClassBody.innerHTML = `
        <div style="padding:20px;">
          <h3 style="margin:0 0 6px; font-size:1.05rem;">${escapeHtml(s.title)}</h3>
          <p style="color:var(--muted); margin:0 0 14px;">${escapeHtml(s.batch_name)}</p>
          <div class="detail-list" style="margin-bottom:16px;">
            <div class="detail-row"><span class="form-label">Date</span><span>${label(s.session_date)}</span></div>
            <div class="detail-row"><span class="form-label">Time</span><span>${(s.start_time && s.end_time) ? `${formatTime(s.start_time)} – ${formatTime(s.end_time)}` : '—'}</span></div>
          </div>
          <button type="button" class="btn btn-accent" data-join-next-class="${s.id}"><i class="fa-solid fa-video"></i> Join Class</button>
        </div>
      `;
    } else {
      nextClassBody.className = 'admin-panel-empty';
      nextClassBody.textContent = 'No upcoming classes scheduled.';
    }

    // Upcoming Schedule — real class sessions merged with real placement interviews.
    let upcomingInterviews = [];
    try {
      const interviews = await ArckAPI.request('/students/me/interviews');
      upcomingInterviews = interviews.filter((i) => i.status === 'scheduled' && i.interview_date >= todayISO);
    } catch (_) { /* interviews are a nice-to-have on this panel, not required */ }

    const scheduleItems = [
      ...upcomingSessions.map((s) => ({ date: s.session_date, time: s.start_time, text: `${s.title} — ${s.batch_name}`, icon: 'fa-video' })),
      ...upcomingInterviews.map((i) => ({ date: i.interview_date, time: i.interview_time, text: `${i.company_name} Interview${i.role ? ` — ${i.role}` : ''}`, icon: 'fa-user-tie' })),
    ].sort((a, b) => (a.date + (a.time || '')).localeCompare(b.date + (b.time || ''))).slice(0, 5);

    const upcomingScheduleBody = document.getElementById('upcomingScheduleBody');
    if (scheduleItems.length) {
      upcomingScheduleBody.className = '';
      upcomingScheduleBody.innerHTML = `<div class="detail-list" style="padding:20px;">${scheduleItems.map((it) => `
        <div class="detail-row">
          <span class="form-label"><i class="fa-solid ${it.icon}" style="margin-right:8px; color:var(--primary);"></i>${label(it.date)}${it.time ? ` &middot; ${formatTime(it.time)}` : ''}</span>
          <span>${escapeHtml(it.text)}</span>
        </div>
      `).join('')}</div>`;
    } else {
      upcomingScheduleBody.className = 'admin-panel-empty';
      upcomingScheduleBody.textContent = 'No upcoming sessions.';
    }

    // My Learning Progress — classes held vs. total per batch, derived from real ClassSession dates
    // (there's no attendance/LMS-completion tracking in the backend, so this is deliberately labeled
    // "classes" not "course completion").
    const learningProgressBody = document.getElementById('learningProgressBody');
    if (myBatches.length) {
      learningProgressBody.className = '';
      learningProgressBody.innerHTML = myBatches.map((b) => {
        const batchSessions = mySchedule.filter((s) => s.batch_id === b.id);
        const held = batchSessions.filter((s) => s.session_date <= todayISO).length;
        const total = batchSessions.length;
        if (total === 0) {
          return `
            <div class="progress-row">
              <div class="progress-row-label"><span>${escapeHtml(b.name)}</span><span>No classes scheduled yet</span></div>
              <div class="progress-track"><div class="progress-fill" style="width:0%;"></div></div>
            </div>
          `;
        }
        const pct = Math.round((held / total) * 100);
        return `
          <div class="progress-row">
            <div class="progress-row-label"><span>${escapeHtml(b.name)}</span><span>${held} / ${total} classes &middot; ${pct}%</span></div>
            <div class="progress-track"><div class="progress-fill" style="width:${pct}%;"></div></div>
          </div>
        `;
      }).join('');
    } else {
      learningProgressBody.className = 'admin-panel-empty';
      learningProgressBody.textContent = "You're not enrolled in any batch yet.";
    }

    // Support card
    document.getElementById('supportCardBody').innerHTML = openTicketCount > 0
      ? `
        <p style="color:var(--muted); margin-bottom:14px;">You have an open support request. Our team will get back to you soon.</p>
        <p style="margin-bottom:16px;"><strong>Open Tickets:</strong> ${openTicketCount}</p>
        <button type="button" class="btn btn-accent" data-goto-panel="support"><i class="fa-solid fa-life-ring"></i> View Support</button>
      `
      : `
        <p style="color:var(--muted); margin-bottom:16px;">Have an issue with your course, account, class, or training?</p>
        <button type="button" class="btn btn-accent" data-goto-panel="support"><i class="fa-solid fa-life-ring"></i> Contact Support</button>
      `;

    // Trainer + classmates for the primary batch — photo + name only, via
    // the batch-members endpoint (never the admin batch-detail schema,
    // which is the one place username is allowed to appear).
    const primaryBatch = myBatches[0];
    const trainerBody = document.getElementById('batchTrainerBody');
    const classmatesBody = document.getElementById('batchClassmatesBody');
    if (primaryBatch) {
      try {
        const members = await ArckAPI.request(`/students/me/batches/${primaryBatch.id}/members`);
        trainerBody.className = '';
        trainerBody.innerHTML = members.trainer
          ? `<div class="ak-identity">${ArckAPI.avatarHtml(members.trainer.full_name, members.trainer.photo_url, 44)}<span class="ak-identity-name">${escapeHtml(members.trainer.full_name)}</span></div>`
          : 'No trainer assigned yet.';

        classmatesBody.className = members.students.length ? '' : 'admin-panel-empty';
        classmatesBody.innerHTML = members.students.length
          ? `<div style="display:flex; flex-direction:column; gap:12px;">${members.students.map((s) => `
              <div class="ak-identity">${ArckAPI.avatarHtml(s.full_name, s.photo_url, 36)}<span class="ak-identity-name">${escapeHtml(s.full_name)}</span></div>
            `).join('')}</div>`
          : "You're the only student in this batch so far.";
      } catch (_) {
        trainerBody.textContent = "Couldn't load trainer.";
        classmatesBody.textContent = "Couldn't load classmates.";
      }
    } else {
      trainerBody.textContent = 'Not assigned to a batch yet.';
      classmatesBody.textContent = 'Not assigned to a batch yet.';
    }

    // Quick Actions — only panels that actually exist in this portal
    const quickActions = [
      { panel: 'schedule', label: 'Class Schedule', icon: 'fa-calendar-days' },
      { panel: 'materials', label: 'Study Materials', icon: 'fa-book-open' },
      { panel: 'videos', label: 'Class Videos', icon: 'fa-video' },
      { panel: 'lab', label: 'Lab Access', icon: 'fa-flask' },
      { panel: 'interviews', label: 'Interview Schedule', icon: 'fa-user-tie' },
      { panel: 'support', label: 'Support', icon: 'fa-life-ring' },
    ];
    document.getElementById('quickActionsBody').innerHTML = quickActions.map((a) => `
      <button type="button" class="btn btn-primary-outline" data-goto-panel="${a.panel}"><i class="fa-solid ${a.icon}"></i> ${a.label}</button>
    `).join('');
  };

  await loadBatchMeetings();
  await loadSchedule();
  await loadMaterials();
  await loadVideos();
  loadLabAccessStatus(); // independent of the slot list below — never lets one block the other
  await loadLabSlots();
  await loadTickets();
  await loadOverview();
  renderAccountDetails();

});
