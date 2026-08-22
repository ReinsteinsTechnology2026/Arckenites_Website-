const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—';

const ACTION_LABELS = {
  login_success: 'Login', login_failure: 'Failed Login', logout: 'Logout',
  password_change: 'Password Changed', account_locked: 'Account Locked', rate_limited: 'Rate Limited',
  session_revoked: 'Session Revoked',
  student_created: 'Created Student', student_updated: 'Edited Student', student_enabled: 'Enabled Student',
  student_disabled: 'Disabled Student', student_deleted: 'Deleted Student', student_password_reset: 'Reset Student Password',
  trainer_created: 'Created Trainer', trainer_updated: 'Edited Trainer', trainer_enabled: 'Enabled Trainer',
  trainer_disabled: 'Disabled Trainer', trainer_deleted: 'Deleted Trainer', trainer_password_reset: 'Reset Trainer Password',
  trainer_permissions_changed: 'Changed Trainer Permissions',
  program_created: 'Created Program', program_updated: 'Updated Program', program_deleted: 'Deleted Program',
  batch_created: 'Created Batch', batch_updated: 'Updated Batch', batch_deleted: 'Deleted Batch',
  admin_user_created: 'Created Admin', admin_user_updated: 'Edited Admin', admin_user_enabled: 'Enabled Admin',
  admin_user_disabled: 'Disabled Admin', admin_user_deleted: 'Deleted Admin', admin_user_password_reset: 'Reset Admin Password',
  admin_user_role_changed: 'Changed Admin Role',
  role_created: 'Created Role', role_updated: 'Updated Role', role_deleted: 'Deleted Role', permission_changed: 'Changed Permissions',
  settings_changed: 'Changed Settings',
};

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('admin');
  if (!user) return; // requireRole already redirected

  /* ---------- Profile ---------- */
  const initials = user.full_name.split(' ').filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || 'A';
  document.getElementById('adminAvatarInitials').textContent = initials;
  document.getElementById('adminProfileName').textContent = user.full_name;
  document.getElementById('adminProfileRole').textContent = user.role;

  /* ---------- Sidebar: collapsible groups ---------- */
  document.querySelectorAll('.admin-sidebar-group-header').forEach((header) => {
    header.addEventListener('click', () => {
      const group = header.closest('.admin-sidebar-group');
      const isOpen = group.classList.toggle('is-open');
      header.setAttribute('aria-expanded', String(isOpen));
    });
  });

  /* ---------- Sidebar: desktop collapse ---------- */
  const sidebar = document.getElementById('adminSidebar');
  document.getElementById('adminSidebarCollapseBtn').addEventListener('click', () => {
    sidebar.classList.toggle('is-collapsed');
  });

  /* ---------- Sidebar: mobile off-canvas ---------- */
  const sidebarBackdrop = document.getElementById('adminSidebarBackdrop');
  const openMobileSidebar = () => { sidebar.classList.add('is-mobile-open'); sidebarBackdrop.classList.add('is-visible'); };
  const closeMobileSidebar = () => { sidebar.classList.remove('is-mobile-open'); sidebarBackdrop.classList.remove('is-visible'); };
  document.getElementById('adminMobileToggle').addEventListener('click', openMobileSidebar);
  sidebarBackdrop.addEventListener('click', closeMobileSidebar);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMobileSidebar(); });

  /* ---------- Profile dropdown ---------- */
  const profileTrigger = document.getElementById('adminProfileTrigger');
  const profilePanel = document.getElementById('adminProfilePanel');
  profileTrigger.addEventListener('click', (e) => { e.stopPropagation(); profilePanel.classList.toggle('is-open'); });
  document.addEventListener('click', () => profilePanel.classList.remove('is-open'));

  /* ---------- Logout ---------- */
  document.getElementById('adminSidebarLogout').addEventListener('click', () => ArckAuth.logout());
  document.getElementById('adminProfileLogout').addEventListener('click', () => ArckAuth.logout());

  const serverBanner = document.getElementById('adminServerBanner');
  const showServerBanner = () => serverBanner.classList.add('is-visible');

  /* ---------- Filters ---------- */
  const form = document.getElementById('logFiltersForm');
  const searchInput = document.getElementById('filterSearch');
  const moduleSelect = document.getElementById('filterModule');
  const roleSelect = document.getElementById('filterRole');
  const statusSelect = document.getElementById('filterStatus');
  const dateFromInput = document.getElementById('filterDateFrom');
  const dateToInput = document.getElementById('filterDateTo');

  let page = 1;
  const pageSize = 25;

  const buildQuery = (forExport) => {
    const params = new URLSearchParams();
    if (searchInput.value.trim()) params.set('q', searchInput.value.trim());
    if (moduleSelect.value) params.set('module', moduleSelect.value);
    if (roleSelect.value) params.set('role', roleSelect.value);
    if (statusSelect.value) params.set('status', statusSelect.value);
    if (dateFromInput.value) params.set('date_from', dateFromInput.value);
    if (dateToInput.value) params.set('date_to', dateToInput.value);
    if (!forExport) {
      params.set('page', String(page));
      params.set('page_size', String(pageSize));
    }
    return params.toString();
  };

  /* ---------- Table rendering ---------- */
  const tbody = document.getElementById('logsTableBody');
  const pagination = document.getElementById('logsPagination');

  const statusBadge = (status) => {
    if (status === 'success') return '<span class="admin-activity-badge is-success">Success</span>';
    if (status === 'failure') return '<span class="admin-activity-badge is-danger">Failure</span>';
    return '—';
  };

  const rowHtml = (entry) => `
    <tr>
      <td>${formatDateTime(entry.created_at)}</td>
      <td>${entry.user_name ? escapeHtml(entry.user_name) : (entry.username ? escapeHtml(entry.username) : '—')}</td>
      <td>${entry.role ? escapeHtml(entry.role) : '—'}</td>
      <td>${escapeHtml(ACTION_LABELS[entry.event_type] || entry.event_type)}</td>
      <td>${entry.module ? escapeHtml(entry.module) : '—'}</td>
      <td>${entry.target ? escapeHtml(entry.target) : '—'}</td>
      <td>${statusBadge(entry.status)}</td>
      <td>${entry.ip_address ? escapeHtml(entry.ip_address) : '—'}</td>
      <td style="max-width:260px; white-space:normal; color:var(--muted);">${entry.detail ? escapeHtml(entry.detail) : '—'}</td>
    </tr>
  `;

  const renderPagination = (total) => {
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    pagination.innerHTML = `
      <button type="button" class="btn btn-primary-outline" id="prevPageBtn" ${page <= 1 ? 'disabled' : ''}>Previous</button>
      <span style="align-self:center; color:var(--muted);">Page ${page} of ${totalPages}</span>
      <button type="button" class="btn btn-primary-outline" id="nextPageBtn" ${page >= totalPages ? 'disabled' : ''}>Next</button>
    `;
    document.getElementById('prevPageBtn').addEventListener('click', () => { page -= 1; loadLogs(); });
    document.getElementById('nextPageBtn').addEventListener('click', () => { page += 1; loadLogs(); });
  };

  const loadLogs = async () => {
    tbody.innerHTML = '<tr><td colspan="9" class="admin-panel-empty">Loading&hellip;</td></tr>';
    try {
      const data = await ArckAPI.request(`/admin/activity-logs?${buildQuery(false)}`);
      tbody.innerHTML = data.items.length
        ? data.items.map(rowHtml).join('')
        : '<tr><td colspan="9" class="admin-panel-empty">No matching log entries.</td></tr>';
      renderPagination(data.total);
    } catch (err) {
      if (err.status === 0) showServerBanner();
      if (err.status === 403) tbody.innerHTML = '<tr><td colspan="9" class="admin-panel-empty">You do not have permission to view activity logs.</td></tr>';
      else tbody.innerHTML = '<tr><td colspan="9" class="admin-panel-empty">Couldn\'t load activity logs.</td></tr>';
      pagination.innerHTML = '';
    }
  };

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    page = 1;
    loadLogs();
  });

  document.getElementById('clearFiltersBtn').addEventListener('click', () => {
    form.reset();
    page = 1;
    loadLogs();
  });

  /* ---------- Export CSV ---------- */
  document.getElementById('exportLogsBtn').addEventListener('click', async () => {
    try {
      const token = ArckAPI.getToken();
      const res = await fetch(`${API_BASE}/admin/activity-logs/export?${buildQuery(true)}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'activity_logs.csv';
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (_) {
      window.alert('Could not export activity logs.');
    }
  });

  if (!ArckAuth.hasPermission('activity_logs.export')) {
    document.getElementById('exportLogsBtn').style.display = 'none';
  }

  await loadLogs();

});
