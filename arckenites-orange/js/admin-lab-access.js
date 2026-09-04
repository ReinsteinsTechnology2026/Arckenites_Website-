const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDate = (iso) => iso ? new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
const formatSlotTime = (t) => {
  if (!t) return '—';
  const [h, m] = t.split(':').map(Number);
  const period = h >= 12 ? 'PM' : 'AM';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${String(m).padStart(2, '0')} ${period}`;
};

const SLOT_STATE_LABEL = { ACTIVE: '🟢 Active', NOT_STARTED: '⏳ Not Started', COMPLETED: '✅ Completed', NO_SLOT: 'No Slot Selected' };
const SLOT_STATE_BADGE = { ACTIVE: 'is-success', NOT_STARTED: 'is-info', COMPLETED: 'is-muted', NO_SLOT: 'is-muted' };
const ACCESS_MODE_LABEL = { AUTO: 'Auto', MANUAL_UNLOCK: 'Manually Unlocked', MANUAL_LOCK: 'Manually Locked' };

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
  const backdrop = document.getElementById('adminSidebarBackdrop');
  const openMobileSidebar = () => { sidebar.classList.add('is-mobile-open'); backdrop.classList.add('is-visible'); };
  const closeMobileSidebar = () => { sidebar.classList.remove('is-mobile-open'); backdrop.classList.remove('is-visible'); };
  document.getElementById('adminMobileToggle').addEventListener('click', openMobileSidebar);
  backdrop.addEventListener('click', closeMobileSidebar);
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

  const canManage = ArckAuth.hasPermission('lab_access.manage');

  /* ---------- Batch selector + student table ---------- */
  const batchSelect = document.getElementById('labAccessBatchSelect');
  const tableBody = document.getElementById('labAccessTableBody');

  const loadBatches = async () => {
    try {
      const batches = await ArckAPI.request('/admin/lab-access/batches');
      batchSelect.innerHTML = '<option value="">Select a batch&hellip;</option>'
        + batches.map((b) => `<option value="${b.id}">${escapeHtml(b.name)}</option>`).join('');
    } catch (_) {
      showServerBanner();
    }
  };

  const renderRow = (row) => {
    const slotText = row.slot_date
      ? `${formatDate(row.slot_date)}<br><span style="color:var(--muted-2); font-size:.82rem;">${formatSlotTime(row.slot_start_time)} – ${formatSlotTime(row.slot_end_time)}</span>`
      : '—';
    const modeNote = row.access_mode !== 'AUTO'
      ? `<div style="font-size:.72rem; color:var(--muted-2); margin-top:2px;">${ACCESS_MODE_LABEL[row.access_mode]}</div>` : '';
    const lastModified = row.updated_at
      ? `${formatDateTime(row.updated_at)}${row.updated_by_name ? `<br><span style="font-size:.76rem; color:var(--muted-2);">by ${escapeHtml(row.updated_by_name)}</span>` : ''}`
      : '—';
    const actions = canManage ? `
      <button type="button" class="btn btn-accent" style="padding:4px 12px;" data-action="unlock" ${row.access_mode === 'MANUAL_UNLOCK' ? 'disabled' : ''}>Unlock</button>
      <button type="button" class="btn btn-primary-outline is-danger" style="padding:4px 12px;" data-action="lock" ${row.access_mode === 'MANUAL_LOCK' ? 'disabled' : ''}>Lock</button>
      <button type="button" class="btn btn-primary-outline" style="padding:4px 12px;" data-action="reset" ${row.access_mode === 'AUTO' ? 'disabled' : ''}>Reset to Auto</button>
    ` : '';
    return `
      <tr data-student-id="${row.student_id}" data-student-name="${escapeHtml(row.full_name)}">
        <td>${escapeHtml(row.full_name)}</td>
        <td>${escapeHtml(row.username)}</td>
        <td>${slotText}</td>
        <td><span class="admin-activity-badge ${SLOT_STATE_BADGE[row.slot_state]}">${SLOT_STATE_LABEL[row.slot_state]}</span></td>
        <td><span class="admin-activity-badge ${row.status === 'UNLOCKED' ? 'is-success' : 'is-danger'}">${row.status === 'UNLOCKED' ? '🔓 Unlocked' : '🔒 Locked'}</span>${modeNote}</td>
        <td>${lastModified}</td>
        <td>
          <div style="display:flex; gap:6px; flex-wrap:wrap;">
            ${actions}
            <button type="button" class="btn btn-primary-outline" style="padding:4px 12px;" data-action="history">History</button>
          </div>
        </td>
      </tr>
    `;
  };

  const loadStudents = async (batchId) => {
    if (!batchId) {
      tableBody.innerHTML = '<tr><td colspan="7" class="admin-panel-empty">Select a batch to view its students.</td></tr>';
      return;
    }
    tableBody.innerHTML = '<tr><td colspan="7" class="admin-panel-empty">Loading&hellip;</td></tr>';
    try {
      const rows = await ArckAPI.request(`/admin/lab-access/batches/${batchId}/students`);
      tableBody.innerHTML = rows.length
        ? rows.map(renderRow).join('')
        : '<tr><td colspan="7" class="admin-panel-empty">No students enrolled in this batch.</td></tr>';
    } catch (_) {
      tableBody.innerHTML = '<tr><td colspan="7" class="admin-panel-empty">Couldn\'t load students.</td></tr>';
    }
  };

  batchSelect.addEventListener('change', () => loadStudents(batchSelect.value));

  /* ---------- History modal ---------- */
  const historyBackdrop = document.getElementById('labHistoryBackdrop');
  const historyModal = document.getElementById('labHistoryModal');
  const historyTitle = document.getElementById('labHistoryTitle');
  const historyBody = document.getElementById('labHistoryTableBody');

  const openHistory = async (studentId, studentName) => {
    historyTitle.textContent = `Access History — ${studentName}`;
    historyBackdrop.style.display = 'block';
    historyModal.style.display = 'block';
    historyBody.innerHTML = '<tr><td colspan="5" class="admin-panel-empty">Loading&hellip;</td></tr>';
    try {
      const entries = await ArckAPI.request(`/admin/lab-access/${studentId}/history`);
      historyBody.innerHTML = entries.length
        ? entries.map((e) => `
            <tr>
              <td>${formatDateTime(e.created_at)}</td>
              <td>${escapeHtml(e.admin_name)}</td>
              <td>${escapeHtml(e.action)}</td>
              <td>${escapeHtml(e.previous_status)} &rarr; ${escapeHtml(e.new_status)}</td>
              <td>${e.reason ? escapeHtml(e.reason) : '—'}</td>
            </tr>
          `).join('')
        : '<tr><td colspan="5" class="admin-panel-empty">No actions recorded yet.</td></tr>';
    } catch (_) {
      historyBody.innerHTML = '<tr><td colspan="5" class="admin-panel-empty">Couldn\'t load history.</td></tr>';
    }
  };

  const closeHistory = () => { historyBackdrop.style.display = 'none'; historyModal.style.display = 'none'; };
  document.getElementById('labHistoryCloseBtn').addEventListener('click', closeHistory);
  historyBackdrop.addEventListener('click', closeHistory);

  /* ---------- Row actions ---------- */
  tableBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn || btn.disabled) return;
    const row = btn.closest('tr');
    const studentId = row.dataset.studentId;
    const studentName = row.dataset.studentName;
    const action = btn.dataset.action;

    if (action === 'history') { openHistory(studentId, studentName); return; }

    const actionLabel = action === 'unlock' ? 'unlock' : action === 'lock' ? 'lock' : 'reset to automatic';
    if (!window.confirm(`Are you sure you want to ${actionLabel} lab access for ${studentName}?`)) return;

    const reason = window.prompt('Optional reason (recorded in the audit log):', '') || null;

    btn.disabled = true;
    try {
      const endpoint = action === 'unlock' ? 'unlock' : action === 'lock' ? 'lock' : 'reset-auto';
      const updated = await ArckAPI.request(`/admin/lab-access/${studentId}/${endpoint}`, { method: 'POST', body: { reason } });
      row.outerHTML = renderRow(updated);
    } catch (err) {
      window.alert(err.detail || 'Could not update lab access.');
      btn.disabled = false;
    }
  });

  await loadBatches();

});
