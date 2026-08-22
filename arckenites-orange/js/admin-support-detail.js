const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
const formatFileSize = (bytes) => bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

const CATEGORY_LABEL = {
  account_login: 'Account / Login', course_access: 'Course Access', batch: 'Batch', classes_schedule: 'Classes / Schedule',
  assignments: 'Assignments', assessments: 'Assessments', certificates: 'Certificates', payment_fees: 'Payment / Fees',
  technical_issue: 'Technical Issue', trainer_training: 'Trainer / Training', other: 'Other',
};
const REQUESTER_ROLE_LABEL = { student: 'Student', staff: 'Trainer' };

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('admin');
  if (!user) return;

  const ticketId = Number(new URLSearchParams(window.location.search).get('id'));

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

  if (!ticketId) {
    document.getElementById('ticketNotFound').style.display = 'block';
    return;
  }

  /* ---------- Permission-gated controls ---------- */
  const canReply = ArckAuth.hasPermission('support.reply');
  const canAddNote = ArckAuth.hasPermission('support.add_internal_note');
  const canChangeStatus = ArckAuth.hasPermission('support.change_status');
  const canChangePriority = ArckAuth.hasPermission('support.change_priority');
  const canAssign = ArckAuth.hasPermission('support.assign');
  const canClose = ArckAuth.hasPermission('support.close');
  const canDelete = ArckAuth.hasPermission('support.delete');

  if (!canAddNote) document.getElementById('internalNoteOption').style.display = 'none';
  if (!canReply && !canAddNote) document.getElementById('replyForm').style.display = 'none';
  if (!canChangeStatus) document.getElementById('statusSelect').disabled = true;
  if (!canChangePriority) document.getElementById('prioritySelect').disabled = true;
  if (!canAssign) document.getElementById('assignSelect').disabled = true;
  if (!canClose) document.getElementById('closeTicketBtn').style.display = 'none';
  if (!canDelete) document.getElementById('deleteTicketBtn').style.display = 'none';

  /* ---------- Assignable admins ---------- */
  const assignSelect = document.getElementById('assignSelect');
  try {
    const admins = await ArckAPI.request('/admin/support/assignable-admins');
    admins.forEach((a) => {
      const opt = document.createElement('option');
      opt.value = a.id;
      opt.textContent = a.full_name;
      assignSelect.appendChild(opt);
    });
  } catch (_) { /* fine without it */ }

  /* ---------- Render ---------- */
  let ticket = null;

  const messageHtml = (m) => {
    const roleClass = m.is_internal ? 'is-internal' : (m.sender_type === 'admin' ? 'is-admin' : 'is-student');
    const attachmentsHtml = m.attachments.length
      ? `<div class="ticket-message-attachments">${m.attachments.map((a) => `
          <a href="${API_BASE}/admin/support/tickets/${ticket.id}/attachments/${a.id}" target="_blank" rel="noopener" data-auth-download="${a.id}">
            <i class="fa-solid fa-paperclip"></i> ${escapeHtml(a.file_name)} (${formatFileSize(a.file_size)})
          </a>
        `).join('')}</div>`
      : '';
    return `
      <div class="ticket-message ${roleClass}">
        <div class="ticket-message-meta">
          ${m.is_internal ? '<span class="ticket-message-internal-label"><i class="fa-solid fa-lock"></i> Internal Note</span> &middot; ' : ''}
          <strong>${escapeHtml(m.sender_name)}</strong> &middot; ${formatDateTime(m.created_at)}
        </div>
        <div>${escapeHtml(m.message)}</div>
        ${attachmentsHtml}
      </div>
    `;
  };

  const renderTicket = () => {
    document.getElementById('ticketBreadcrumb').textContent = ticket.ticket_number;
    document.getElementById('ticketSubjectHeading').textContent = ticket.subject;
    document.title = `${ticket.ticket_number} | Arckenites Hub`;

    document.getElementById('infoTicketNumber').textContent = ticket.ticket_number;
    document.getElementById('infoRequesterName').textContent = ticket.requester_name;
    document.getElementById('infoRequesterUsername').textContent = ticket.requester_username;
    document.getElementById('infoRequesterRole').textContent = REQUESTER_ROLE_LABEL[ticket.requester_role] || ticket.requester_role;
    document.getElementById('infoCreated').textContent = formatDateTime(ticket.created_at);
    document.getElementById('infoUpdated').textContent = formatDateTime(ticket.updated_at);
    document.getElementById('infoCategory').textContent = CATEGORY_LABEL[ticket.category] || ticket.category;

    if (ticket.resolved_at) {
      document.getElementById('infoResolvedRow').style.display = 'flex';
      document.getElementById('infoResolved').textContent = formatDateTime(ticket.resolved_at);
    }
    if (ticket.closed_at) {
      document.getElementById('infoClosedRow').style.display = 'flex';
      document.getElementById('infoClosed').textContent = formatDateTime(ticket.closed_at);
    }

    document.getElementById('statusSelect').value = ticket.status === 'closed' ? 'in_progress' : ticket.status;
    document.getElementById('prioritySelect').value = ticket.priority;
    assignSelect.value = ticket.assigned_to || '';

    const isClosed = ticket.status === 'closed';
    document.getElementById('statusSelect').disabled = !canChangeStatus || isClosed;
    document.getElementById('closeTicketBtn').style.display = (canClose && !isClosed) ? 'inline-flex' : 'none';
    document.getElementById('reopenTicketBtn').style.display = (canChangeStatus && isClosed) ? 'inline-flex' : 'none';
    document.getElementById('replyForm').style.display = (isClosed || (!canReply && !canAddNote)) ? 'none' : 'block';

    const thread = document.getElementById('conversationThread');
    thread.innerHTML = ticket.messages.length
      ? ticket.messages.map(messageHtml).join('')
      : '<p style="color:var(--muted); text-align:center;">No messages yet.</p>';
    thread.scrollTop = thread.scrollHeight;

    // Attachment links need the Authorization header — intercept the click and stream via fetch+blob.
    thread.querySelectorAll('[data-auth-download]').forEach((a) => {
      a.addEventListener('click', async (e) => {
        e.preventDefault();
        try {
          const res = await fetch(a.href, { headers: { Authorization: `Bearer ${ArckAPI.getToken()}` } });
          if (!res.ok) throw new Error('download failed');
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          window.open(url, '_blank');
        } catch (_) {
          window.alert('Could not download this attachment.');
        }
      });
    });
  };

  const loadTicket = async () => {
    try {
      ticket = await ArckAPI.request(`/admin/support/tickets/${ticketId}`);
      document.getElementById('ticketDetailContent').style.display = 'block';
      renderTicket();
    } catch (err) {
      if (err.status === 0) { showServerBanner(); return; }
      document.getElementById('ticketNotFound').style.display = 'block';
    }
  };

  /* ---------- Status / Priority / Assign ---------- */
  document.getElementById('statusSelect').addEventListener('change', async (e) => {
    try {
      await ArckAPI.request(`/admin/support/tickets/${ticketId}/status`, { method: 'PATCH', body: { status: e.target.value } });
      await loadTicket();
    } catch (err) {
      window.alert(err.detail || 'Could not change status.');
      renderTicket();
    }
  });

  document.getElementById('prioritySelect').addEventListener('change', async (e) => {
    try {
      await ArckAPI.request(`/admin/support/tickets/${ticketId}/priority`, { method: 'PATCH', body: { priority: e.target.value } });
      await loadTicket();
    } catch (err) {
      window.alert(err.detail || 'Could not change priority.');
      renderTicket();
    }
  });

  assignSelect.addEventListener('change', async (e) => {
    const assignedTo = e.target.value ? Number(e.target.value) : null;
    try {
      await ArckAPI.request(`/admin/support/tickets/${ticketId}/assign`, { method: 'PATCH', body: { assigned_to: assignedTo } });
      await loadTicket();
    } catch (err) {
      window.alert(err.detail || 'Could not assign this ticket.');
      renderTicket();
    }
  });

  document.getElementById('closeTicketBtn').addEventListener('click', async () => {
    if (!window.confirm('Close this ticket? The requester will no longer be able to reply unless they reopen it.')) return;
    try {
      await ArckAPI.request(`/admin/support/tickets/${ticketId}/close`, { method: 'POST' });
      await loadTicket();
    } catch (err) {
      window.alert(err.detail || 'Could not close this ticket.');
    }
  });

  document.getElementById('reopenTicketBtn').addEventListener('click', async () => {
    try {
      await ArckAPI.request(`/admin/support/tickets/${ticketId}/reopen`, { method: 'POST' });
      await loadTicket();
    } catch (err) {
      window.alert(err.detail || 'Could not reopen this ticket.');
    }
  });

  document.getElementById('deleteTicketBtn').addEventListener('click', async () => {
    if (!window.confirm('Delete this ticket permanently? This cannot be undone.')) return;
    try {
      await ArckAPI.request(`/admin/support/tickets/${ticketId}`, { method: 'DELETE' });
      window.location.href = 'admin-support.html';
    } catch (err) {
      window.alert(err.detail || 'Could not delete this ticket.');
    }
  });

  /* ---------- Reply / Internal note ---------- */
  const replyForm = document.getElementById('replyForm');
  const replyMessage = document.getElementById('replyMessage');
  const replyAttachment = document.getElementById('replyAttachment');
  const replyError = document.getElementById('replyError');
  const replySubmitBtn = document.getElementById('replySubmitBtn');

  replyForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    replyError.style.display = 'none';

    const mode = document.querySelector('input[name="replyMode"]:checked').value;
    const endpoint = mode === 'internal'
      ? `/admin/support/tickets/${ticketId}/notes`
      : `/admin/support/tickets/${ticketId}/messages`;

    replySubmitBtn.disabled = true;
    try {
      const message = await ArckAPI.request(endpoint, { method: 'POST', body: { message: replyMessage.value.trim() } });

      const file = replyAttachment.files[0];
      if (file) {
        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch(`${API_BASE}/admin/support/tickets/${ticketId}/messages/${message.id}/attachments`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${ArckAPI.getToken()}` },
          body: formData,
        });
        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}));
          throw { detail: errBody.detail || 'Attachment upload failed, but your message was sent.' };
        }
      }

      replyForm.reset();
      await loadTicket();
    } catch (err) {
      replyError.textContent = err.detail || 'Could not send this message.';
      replyError.style.display = 'block';
    } finally {
      replySubmitBtn.disabled = false;
    }
  });

  await loadTicket();

});
