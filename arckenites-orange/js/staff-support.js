const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
const formatFileSize = (bytes) => bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

const CATEGORY_LABEL = {
  account_login: 'Account / Login', course_access: 'Course Access', batch: 'Batch', classes_schedule: 'Classes / Schedule',
  assignments: 'Assignments', assessments: 'Assessments', certificates: 'Certificates', payment_fees: 'Payment / Fees',
  technical_issue: 'Technical Issue', trainer_training: 'Trainer / Training', other: 'Other',
};
const STATUS_LABEL = { open: 'Open', in_progress: 'In Progress', waiting_for_student: 'Waiting for You', resolved: 'Resolved', closed: 'Closed' };
const STATUS_BADGE = { open: 'is-danger', in_progress: 'is-info', waiting_for_student: 'is-muted', resolved: 'is-success', closed: 'is-muted' };
const PRIORITY_LABEL = { low: 'Low', medium: 'Medium', high: 'High' };
const PRIORITY_BADGE = { low: 'is-muted', medium: 'is-info', high: 'is-danger' };

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('staff');
  if (!user) return; // requireRole already redirected

  document.getElementById('staffWelcome').textContent = `Welcome, ${user.full_name}`;
  document.getElementById('staffLogoutBtn').addEventListener('click', () => ArckAuth.logout());

  /* ---------- Ticket list / create / detail view switching ---------- */
  const ticketListView = document.getElementById('ticketListView');
  const ticketCreateView = document.getElementById('ticketCreateView');
  const ticketDetailView = document.getElementById('ticketDetailView');
  const myTicketsBody = document.getElementById('myTicketsBody');

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
      const tickets = await ArckAPI.request('/staff/me/tickets');
      myTicketsBody.innerHTML = tickets.length
        ? tickets.map(ticketRowHtml).join('')
        : `<tr><td colspan="6" class="admin-panel-empty">
             No support requests yet.<br><br>
             Need help with a batch, class, or account issue?<br><br>
             <button type="button" class="btn btn-accent" id="emptyStateCreateTicketBtn"><i class="fa-solid fa-plus"></i> Create Support Ticket</button>
           </td></tr>`;

      const emptyBtn = document.getElementById('emptyStateCreateTicketBtn');
      if (emptyBtn) emptyBtn.addEventListener('click', () => showTicketView('create'));
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
      const ticket = await ArckAPI.request('/staff/me/tickets', { method: 'POST', body });

      const fileInput = document.getElementById('ticketAttachment');
      const file = fileInput.files[0];
      if (file) {
        const firstMessageId = ticket.messages[0].id;
        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch(`${API_BASE}/staff/me/tickets/${ticket.id}/messages/${firstMessageId}/attachments`, {
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
    const roleClass = m.sender_type === 'staff' ? 'is-student' : 'is-admin';
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
      const ticket = await ArckAPI.request(`/staff/me/tickets/${ticketId}`);
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
            const res = await fetch(`${API_BASE}/staff/me/tickets/${a.dataset.ticket}/attachments/${a.dataset.downloadAttachment}`, {
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

      await loadTickets(); // refresh unread markers now that this ticket's been read
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
      const message = await ArckAPI.request(`/staff/me/tickets/${currentTicketId}/messages`, {
        method: 'POST', body: { message: document.getElementById('studentReplyMessage').value.trim() },
      });

      const fileInput = document.getElementById('studentReplyAttachment');
      const file = fileInput.files[0];
      if (file) {
        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch(`${API_BASE}/staff/me/tickets/${currentTicketId}/messages/${message.id}/attachments`, {
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
      await ArckAPI.request(`/staff/me/tickets/${currentTicketId}/reopen`, { method: 'POST' });
      await openTicketDetail(currentTicketId);
    } catch (err) {
      window.alert(err.detail || 'Could not reopen this ticket.');
    }
  });

  await loadTickets();

});
