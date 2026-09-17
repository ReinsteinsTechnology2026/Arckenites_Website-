const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';

const STATUS_LABEL = { active: '🟢 Active', expired: '🔴 Expired', revoked: '⚪ Revoked' };
const STATUS_BADGE = { active: 'is-success', expired: 'is-danger', revoked: 'is-muted' };
const AGENT_LABEL = { online: '🟢 Online', offline: '🔴 Offline', never_connected: '⚪ Never connected' };

function formatRemaining(expiresAtIso, status) {
  if (status !== 'active') return '—';
  const ms = new Date(expiresAtIso).getTime() - Date.now();
  if (ms <= 0) return 'Expiring…';
  const totalMin = Math.floor(ms / 60000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

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

  /* ---------- Tabs ---------- */
  document.querySelectorAll('#labVmTabs .settings-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('#labVmTabs .settings-tab').forEach((t) => t.classList.remove('is-active'));
      document.querySelectorAll('.settings-tab-panel').forEach((p) => p.classList.remove('is-active'));
      tab.classList.add('is-active');
      document.querySelector(`.settings-tab-panel[data-panel="${tab.dataset.tab}"]`).classList.add('is-active');
    });
  });

  /* ---------- Permissions ---------- */
  const canGrant = ArckAuth.hasPermission('lab_vm.grant');
  const canRevoke = ArckAuth.hasPermission('lab_vm.revoke');
  const canManageVms = ArckAuth.hasPermission('lab_vm.manage_vms');
  const canViewHistory = ArckAuth.hasPermission('lab_vm.view_history');

  if (canGrant) document.getElementById('grantAccessBtn').style.display = '';
  if (canManageVms) document.getElementById('addVmBtn').style.display = '';

  /* ---------- Generic modal ---------- */
  const modalBackdrop = document.getElementById('modalBackdrop');
  const modalTitle = document.getElementById('modalTitle');
  const modalBody = document.getElementById('modalBody');
  const modalFooter = document.getElementById('modalFooter');

  const openModal = ({ title, bodyHtml, footerHtml }) => {
    modalTitle.textContent = title;
    modalBody.innerHTML = bodyHtml;
    modalFooter.innerHTML = footerHtml || '';
    modalBackdrop.classList.add('is-open');
  };
  const closeModal = () => {
    modalBackdrop.classList.remove('is-open');
    modalBody.innerHTML = '';
    modalFooter.innerHTML = '';
  };
  document.getElementById('modalClose').addEventListener('click', closeModal);
  modalBackdrop.addEventListener('click', (e) => { if (e.target === modalBackdrop) closeModal(); });

  /* ================= ACCESS TAB ================= */

  const accessTableBody = document.getElementById('labVmAccessTableBody');
  let vmOptions = []; // cached VM list for the Grant modal's <select>
  let studentOptions = []; // cached student list for the Grant modal's <select>

  const renderAccessRow = (row) => {
    const actions = [];
    if (canRevoke && row.status === 'active') {
      actions.push(`<button type="button" class="btn btn-primary-outline is-danger" style="padding:4px 12px;" data-action="revoke" data-student-id="${row.student_id}">Revoke</button>`);
    }
    if (canViewHistory) {
      actions.push(`<button type="button" class="btn btn-primary-outline" style="padding:4px 12px;" data-action="history" data-student-id="${row.student_id}" data-student-name="${escapeHtml(row.student_name)}">History</button>`);
    }
    return `
      <tr data-row-id="${row.id}" data-expires-at="${row.expires_at}" data-status="${row.status}">
        <td>${escapeHtml(row.student_name)}</td>
        <td>${escapeHtml(row.vm_name)}</td>
        <td><span class="admin-activity-badge ${STATUS_BADGE[row.status] || 'is-muted'}">${STATUS_LABEL[row.status] || row.status}</span></td>
        <td class="js-remaining">${formatRemaining(row.expires_at, row.status)}</td>
        <td>${row.granted_by_name ? escapeHtml(row.granted_by_name) : '—'}</td>
        <td><div style="display:flex; gap:6px; flex-wrap:wrap;">${actions.join('')}</div></td>
      </tr>
    `;
  };

  const loadAccessTable = async () => {
    try {
      const rows = await ArckAPI.request('/admin/lab-vm-access');
      accessTableBody.innerHTML = rows.length
        ? rows.map(renderAccessRow).join('')
        : '<tr><td colspan="6" class="admin-panel-empty">No VM lab access has been granted yet.</td></tr>';
    } catch (_) {
      accessTableBody.innerHTML = '<tr><td colspan="6" class="admin-panel-empty">Couldn\'t load VM lab access.</td></tr>';
      showServerBanner();
    }
  };

  // Re-render just the "Remaining" countdown cells every second — cosmetic
  // only, always derived from the server's own expires_at, never a locally
  // tracked timer. The backend watchdog (and every live read) is what
  // actually flips status to expired; this just keeps the on-screen number
  // from going stale between polls.
  setInterval(() => {
    accessTableBody.querySelectorAll('tr[data-expires-at]').forEach((tr) => {
      const cell = tr.querySelector('.js-remaining');
      if (cell) cell.textContent = formatRemaining(tr.dataset.expiresAt, tr.dataset.status);
    });
  }, 1000);

  // Re-poll the server periodically so status/remaining reflect reality
  // even if this admin leaves the tab open for a long time.
  setInterval(loadAccessTable, 20000);

  accessTableBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const studentId = btn.dataset.studentId;

    if (btn.dataset.action === 'history') {
      openHistory(studentId, btn.dataset.studentName);
      return;
    }

    if (btn.dataset.action === 'revoke') {
      if (!window.confirm('Revoke this student\'s VM lab access now?')) return;
      const reason = window.prompt('Optional reason (recorded in the audit log):', '') || null;
      btn.disabled = true;
      try {
        await ArckAPI.request(`/admin/lab-vm-access/${studentId}/revoke`, { method: 'POST', body: { reason } });
        await loadAccessTable();
      } catch (err) {
        window.alert(err.detail || 'Could not revoke access.');
        btn.disabled = false;
      }
    }
  });

  /* ---------- Grant Access modal ---------- */
  const showGrantModal = async () => {
    if (!studentOptions.length) {
      try {
        studentOptions = await ArckAPI.request('/admin/students');
      } catch (_) {
        window.alert('Could not load the student list.');
        return;
      }
    }
    if (!vmOptions.length) {
      try {
        vmOptions = await ArckAPI.request('/admin/lab-vms');
      } catch (_) {
        window.alert('Could not load the VM inventory.');
        return;
      }
    }
    const activeVms = vmOptions.filter((v) => v.is_active);
    if (!activeVms.length) {
      window.alert('There are no active VMs in the inventory yet. Add one under the VM Inventory tab first.');
      return;
    }

    openModal({
      title: 'Grant VM Lab Access',
      bodyHtml: `
        <div class="admin-form-grid">
          <div>
            <label class="form-label">Student</label>
            <select class="form-select" id="grantStudentSelect">
              ${studentOptions.map((s) => `<option value="${s.id}">${escapeHtml(s.full_name)} (${escapeHtml(s.username)})</option>`).join('')}
            </select>
          </div>
          <div>
            <label class="form-label">VM</label>
            <select class="form-select" id="grantVmSelect">
              ${activeVms.map((v) => `<option value="${v.id}">${escapeHtml(v.name)} — ${escapeHtml(v.hostname)}</option>`).join('')}
            </select>
          </div>
          <div>
            <label class="form-label">Duration (minutes)</label>
            <input type="number" class="form-control" id="grantDurationInput" value="120" min="1" max="1440">
          </div>
        </div>
        <p style="color:var(--muted-2); font-size:.85rem; margin-top:10px;">
          If this student already has active access to another VM, it will be revoked and replaced by this grant.
        </p>
        <div class="login-error" id="grantAccessError" style="display:none; margin-top:14px;"></div>
      `,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn btn-accent" data-save>Grant Access</button>
      `,
    });

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-save]').addEventListener('click', async () => {
      const studentId = document.getElementById('grantStudentSelect').value;
      const vmId = parseInt(document.getElementById('grantVmSelect').value, 10);
      const duration = parseInt(document.getElementById('grantDurationInput').value, 10);
      const errEl = document.getElementById('grantAccessError');
      if (!duration || duration < 1 || duration > 1440) {
        errEl.textContent = 'Duration must be between 1 and 1440 minutes.';
        errEl.style.display = 'block';
        return;
      }
      try {
        await ArckAPI.request(`/admin/lab-vm-access/${studentId}/grant`, {
          method: 'POST',
          body: { vm_id: vmId, duration_minutes: duration },
        });
        closeModal();
        await loadAccessTable();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not grant access.';
        errEl.style.display = 'block';
      }
    });
  };
  document.getElementById('grantAccessBtn').addEventListener('click', showGrantModal);

  /* ---------- History modal ---------- */
  const historyBackdrop = document.getElementById('labVmHistoryBackdrop');
  const historyModal = document.getElementById('labVmHistoryModal');
  const historyTitle = document.getElementById('labVmHistoryTitle');
  const historyBody = document.getElementById('labVmHistoryTableBody');

  const openHistory = async (studentId, studentName) => {
    historyTitle.textContent = `VM Lab Access History — ${studentName}`;
    historyBackdrop.style.display = 'block';
    historyModal.style.display = 'block';
    historyBody.innerHTML = '<tr><td colspan="4" class="admin-panel-empty">Loading&hellip;</td></tr>';
    try {
      const entries = await ArckAPI.request('/admin/lab-vm-access/audit');
      const forStudent = entries.filter((e) => String(e.student_id) === String(studentId));
      historyBody.innerHTML = forStudent.length
        ? forStudent.map((e) => `
            <tr>
              <td>${formatDateTime(e.created_at)}</td>
              <td>${escapeHtml(e.action)}${e.vm_name ? ` — ${escapeHtml(e.vm_name)}` : ''}</td>
              <td>${e.performed_by_name ? escapeHtml(e.performed_by_name) : 'System'}</td>
              <td>${e.reason ? escapeHtml(e.reason) : '—'}</td>
            </tr>
          `).join('')
        : '<tr><td colspan="4" class="admin-panel-empty">No history recorded yet.</td></tr>';
    } catch (_) {
      historyBody.innerHTML = '<tr><td colspan="4" class="admin-panel-empty">Couldn\'t load history.</td></tr>';
    }
  };
  const closeHistory = () => { historyBackdrop.style.display = 'none'; historyModal.style.display = 'none'; };
  document.getElementById('labVmHistoryCloseBtn').addEventListener('click', closeHistory);
  historyBackdrop.addEventListener('click', closeHistory);

  /* ================= INVENTORY TAB ================= */

  const inventoryTableBody = document.getElementById('labVmInventoryTableBody');

  const renderVmRow = (vm) => {
    const actions = canManageVms ? `
      <button type="button" class="btn btn-primary-outline" style="padding:4px 12px;" data-action="edit">Edit</button>
      <button type="button" class="btn btn-primary-outline" style="padding:4px 12px;" data-action="regen">Regenerate Token</button>
      <button type="button" class="btn btn-primary-outline is-danger" style="padding:4px 12px;" data-action="delete">Delete</button>
    ` : '';
    return `
      <tr data-vm-id="${vm.id}">
        <td>${escapeHtml(vm.name)}</td>
        <td>${escapeHtml(vm.hostname)}:${vm.rdp_port}</td>
        <td>${escapeHtml(vm.student_rdp_username)}</td>
        <td>${AGENT_LABEL[vm.agent_status] || vm.agent_status}</td>
        <td><span class="admin-activity-badge ${vm.is_active ? 'is-success' : 'is-muted'}">${vm.is_active ? 'Active' : 'Disabled'}</span></td>
        <td><div style="display:flex; gap:6px; flex-wrap:wrap;">${actions}</div></td>
      </tr>
    `;
  };

  const loadInventory = async () => {
    try {
      vmOptions = await ArckAPI.request('/admin/lab-vms');
      inventoryTableBody.innerHTML = vmOptions.length
        ? vmOptions.map(renderVmRow).join('')
        : '<tr><td colspan="6" class="admin-panel-empty">No VMs in the inventory yet.</td></tr>';
    } catch (_) {
      inventoryTableBody.innerHTML = '<tr><td colspan="6" class="admin-panel-empty">Couldn\'t load the VM inventory.</td></tr>';
      showServerBanner();
    }
  };
  setInterval(loadInventory, 20000);

  const showTokenModal = (vm) => {
    openModal({
      title: `Agent Token — ${vm.name}`,
      bodyHtml: `
        <p>Copy this token into the Windows Lab Agent's <code>config.ini</code> on this VM now — it will not be shown again.</p>
        <input type="text" class="form-control" readonly value="${escapeHtml(vm.agent_token)}" onclick="this.select()" style="font-family:monospace;">
      `,
      footerHtml: `<button type="button" class="btn btn-accent" data-close>Done</button>`,
    });
    modalFooter.querySelector('[data-close]').addEventListener('click', closeModal);
  };

  const showVmFormModal = (vm) => {
    const isEdit = !!vm;
    openModal({
      title: isEdit ? `Edit VM — ${vm.name}` : 'Add VM',
      bodyHtml: `
        <div class="admin-form-grid">
          <div>
            <label class="form-label">Name</label>
            <input type="text" class="form-control" id="vmNameInput" value="${isEdit ? escapeHtml(vm.name) : ''}" required>
          </div>
          <div>
            <label class="form-label">Hostname / IP</label>
            <input type="text" class="form-control" id="vmHostnameInput" value="${isEdit ? escapeHtml(vm.hostname) : ''}" placeholder="lab-vm-1.internal or 10.0.x.x" required>
          </div>
          <div>
            <label class="form-label">RDP Port</label>
            <input type="number" class="form-control" id="vmPortInput" value="${isEdit ? vm.rdp_port : 3389}" min="1" max="65535">
          </div>
          <div>
            <label class="form-label">Student RDP Username</label>
            <input type="text" class="form-control" id="vmUsernameInput" value="${isEdit ? escapeHtml(vm.student_rdp_username) : ''}" required>
          </div>
          <div>
            <label class="form-label">Operating System</label>
            <input type="text" class="form-control" id="vmOsInput" value="${isEdit && vm.operating_system ? escapeHtml(vm.operating_system) : ''}" placeholder="e.g. Windows 11 Pro">
          </div>
          <div>
            <label class="form-label">Description</label>
            <input type="text" class="form-control" id="vmDescInput" value="${isEdit && vm.description ? escapeHtml(vm.description) : ''}">
          </div>
        </div>
        <div class="login-error" id="vmFormError" style="display:none; margin-top:14px;"></div>
      `,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn btn-accent" data-save>${isEdit ? 'Save Changes' : 'Add VM'}</button>
      `,
    });

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-save]').addEventListener('click', async () => {
      const errEl = document.getElementById('vmFormError');
      const payload = {
        name: document.getElementById('vmNameInput').value.trim(),
        hostname: document.getElementById('vmHostnameInput').value.trim(),
        rdp_port: parseInt(document.getElementById('vmPortInput').value, 10) || 3389,
        student_rdp_username: document.getElementById('vmUsernameInput').value.trim(),
        operating_system: document.getElementById('vmOsInput').value.trim() || null,
        description: document.getElementById('vmDescInput').value.trim() || null,
      };
      if (!payload.name || !payload.hostname || !payload.student_rdp_username) {
        errEl.textContent = 'Name, hostname, and student RDP username are required.';
        errEl.style.display = 'block';
        return;
      }
      try {
        if (isEdit) {
          await ArckAPI.request(`/admin/lab-vms/${vm.id}`, { method: 'PATCH', body: payload });
          closeModal();
          await loadInventory();
        } else {
          const created = await ArckAPI.request('/admin/lab-vms', { method: 'POST', body: payload });
          await loadInventory();
          showTokenModal(created);
        }
      } catch (err) {
        errEl.textContent = err.detail || 'Could not save this VM.';
        errEl.style.display = 'block';
      }
    });
  };
  document.getElementById('addVmBtn').addEventListener('click', () => showVmFormModal(null));

  inventoryTableBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const row = btn.closest('tr');
    const vmId = row.dataset.vmId;
    const vm = vmOptions.find((v) => String(v.id) === String(vmId));
    if (!vm) return;

    if (btn.dataset.action === 'edit') { showVmFormModal(vm); return; }

    if (btn.dataset.action === 'regen') {
      if (!window.confirm(`Regenerate the agent token for ${vm.name}? The old token will stop working immediately — update the agent's config before its next check-in.`)) return;
      try {
        const updated = await ArckAPI.request(`/admin/lab-vms/${vm.id}/regenerate-token`, { method: 'POST' });
        showTokenModal(updated);
        await loadInventory();
      } catch (err) {
        window.alert(err.detail || 'Could not regenerate the token.');
      }
      return;
    }

    if (btn.dataset.action === 'delete') {
      if (!window.confirm(`Remove ${vm.name} from the inventory? This cannot be undone.`)) return;
      btn.disabled = true;
      try {
        await ArckAPI.request(`/admin/lab-vms/${vm.id}`, { method: 'DELETE' });
        await loadInventory();
      } catch (err) {
        window.alert(err.detail || 'Could not delete this VM — it may have an active access grant.');
        btn.disabled = false;
      }
    }
  });

  await Promise.all([loadAccessTable(), loadInventory()]);

});
