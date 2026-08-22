const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';

const STATUS_LABEL = { open: 'Open', in_progress: 'In Progress', waiting_for_student: 'Waiting for Requester', resolved: 'Resolved', closed: 'Closed' };
const REQUESTER_ROLE_LABEL = { student: 'Student', staff: 'Trainer' };
const STATUS_BADGE = { open: 'is-danger', in_progress: 'is-info', waiting_for_student: 'is-muted', resolved: 'is-success', closed: 'is-muted' };
const PRIORITY_LABEL = { low: 'Low', medium: 'Medium', high: 'High' };
const PRIORITY_BADGE = { low: 'is-muted', medium: 'is-info', high: 'is-danger' };
const CATEGORY_LABEL = {
  account_login: 'Account / Login', course_access: 'Course Access', batch: 'Batch', classes_schedule: 'Classes / Schedule',
  assignments: 'Assignments', assessments: 'Assessments', certificates: 'Certificates', payment_fees: 'Payment / Fees',
  technical_issue: 'Technical Issue', trainer_training: 'Trainer / Training', other: 'Other',
};

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('admin');
  if (!user) return;

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

  if (!ArckAuth.hasPermission('support.export')) {
    document.getElementById('exportTicketsBtn').style.display = 'none';
  }

  /* ---------- Stats ---------- */
  const loadStats = async () => {
    try {
      const s = await ArckAPI.request('/admin/support/stats');
      document.getElementById('statTotal').textContent = s.total;
      document.getElementById('statOpen').textContent = s.open;
      document.getElementById('statInProgress').textContent = s.in_progress;
      document.getElementById('statWaiting').textContent = s.waiting_for_student;
      document.getElementById('statResolved').textContent = s.resolved;

      const sidebarBadge = document.getElementById('supportSidebarBadge');
      if (s.unread > 0) { sidebarBadge.textContent = s.unread > 9 ? '9+' : String(s.unread); sidebarBadge.style.display = 'inline-flex'; }
      else { sidebarBadge.style.display = 'none'; }
    } catch (err) {
      if (err.status === 0) showServerBanner();
    }
  };

  /* ---------- Assignable admins (for the filter dropdown) ---------- */
  const assignedToSelect = document.getElementById('filterAssignedTo');
  try {
    const admins = await ArckAPI.request('/admin/support/assignable-admins');
    admins.forEach((a) => {
      const opt = document.createElement('option');
      opt.value = a.id;
      opt.textContent = a.full_name;
      assignedToSelect.appendChild(opt);
    });
  } catch (_) { /* filter still usable without this */ }

  /* ---------- Tickets table ---------- */
  const tbody = document.getElementById('ticketsTableBody');

  const buildQuery = () => {
    const params = new URLSearchParams();
    const search = document.getElementById('filterSearch').value.trim();
    const statusFilter = document.getElementById('filterStatus').value;
    const category = document.getElementById('filterCategory').value;
    const priority = document.getElementById('filterPriority').value;
    const assignedTo = document.getElementById('filterAssignedTo').value;
    const dateFrom = document.getElementById('filterDateFrom').value;
    const dateTo = document.getElementById('filterDateTo').value;
    if (search) params.set('search', search);
    if (statusFilter) params.set('status_filter', statusFilter);
    if (category) params.set('category', category);
    if (priority) params.set('priority', priority);
    if (assignedTo) params.set('assigned_to', assignedTo);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    return params.toString();
  };

  const rowHtml = (t) => `
    <tr onclick="window.location.href='admin-support-detail.html?id=${t.id}'" style="cursor:pointer;">
      <td>${t.unread ? '<i class="fa-solid fa-circle" style="color:var(--primary); font-size:.5rem; margin-right:6px;" title="Unread"></i>' : ''}${escapeHtml(t.ticket_number)}</td>
      <td>${escapeHtml(t.requester_name)} <span class="admin-activity-badge is-muted" style="margin-left:6px; font-size:.68rem;">${REQUESTER_ROLE_LABEL[t.requester_role] || t.requester_role}</span></td>
      <td>${escapeHtml(t.subject)}</td>
      <td>${CATEGORY_LABEL[t.category] || escapeHtml(t.category)}</td>
      <td><span class="admin-activity-badge ${PRIORITY_BADGE[t.priority] || 'is-muted'}">${PRIORITY_LABEL[t.priority] || t.priority}</span></td>
      <td><span class="admin-activity-badge ${STATUS_BADGE[t.status] || 'is-muted'}">${STATUS_LABEL[t.status] || t.status}</span></td>
      <td>${t.assigned_to_name ? escapeHtml(t.assigned_to_name) : '—'}</td>
      <td>${formatDateTime(t.updated_at)}</td>
    </tr>
  `;

  const loadTickets = async () => {
    tbody.innerHTML = '<tr><td colspan="8" class="admin-panel-empty">Loading&hellip;</td></tr>';
    try {
      const tickets = await ArckAPI.request(`/admin/support/tickets?${buildQuery()}`);
      tbody.innerHTML = tickets.length
        ? tickets.map(rowHtml).join('')
        : '<tr><td colspan="8" class="admin-panel-empty">No support tickets found.</td></tr>';
    } catch (err) {
      if (err.status === 0) showServerBanner();
      tbody.innerHTML = '<tr><td colspan="8" class="admin-panel-empty">Couldn\'t load tickets.</td></tr>';
    }
  };

  document.getElementById('ticketFiltersForm').addEventListener('submit', (e) => {
    e.preventDefault();
    loadTickets();
  });
  document.getElementById('clearFiltersBtn').addEventListener('click', () => {
    document.getElementById('ticketFiltersForm').reset();
    loadTickets();
  });

  document.getElementById('exportTicketsBtn').addEventListener('click', async () => {
    try {
      const res = await fetch(`${API_BASE}/admin/support/tickets/export?${buildQuery()}`, {
        headers: { Authorization: `Bearer ${ArckAPI.getToken()}` },
      });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'support_tickets.xlsx';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (_) {
      window.alert('Could not export tickets.');
    }
  });

  await loadStats();
  await loadTickets();

});
