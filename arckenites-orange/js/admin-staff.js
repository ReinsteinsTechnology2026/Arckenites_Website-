/* Keep in sync with PERMISSION_KEYS in backend/app/models/staff.py */
const PERMISSION_GROUPS = [
  { title: 'Students', items: [
    { key: 'view_students', label: 'View Students' },
    { key: 'manage_students', label: 'Manage Students' },
  ] },
  { title: 'Attendance', items: [
    { key: 'view_attendance', label: 'View Attendance' },
    { key: 'mark_attendance', label: 'Mark Attendance' },
  ] },
  { title: 'Assignments', items: [
    { key: 'view_assignments', label: 'View Assignments' },
    { key: 'manage_assignments', label: 'Manage Assignments' },
  ] },
  { title: 'Courses', items: [
    { key: 'view_courses', label: 'View Courses' },
  ] },
  { title: 'Batches', items: [
    { key: 'view_batches', label: 'View Batches' },
  ] },
];
const PERMISSION_LABELS = Object.fromEntries(
  PERMISSION_GROUPS.flatMap((g) => g.items).map((i) => [i.key, i.label])
);

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
  const openMobileSidebar = () => {
    sidebar.classList.add('is-mobile-open');
    sidebarBackdrop.classList.add('is-visible');
  };
  const closeMobileSidebar = () => {
    sidebar.classList.remove('is-mobile-open');
    sidebarBackdrop.classList.remove('is-visible');
  };
  document.getElementById('adminMobileToggle').addEventListener('click', openMobileSidebar);
  sidebarBackdrop.addEventListener('click', closeMobileSidebar);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMobileSidebar(); });

  /* ---------- Profile dropdown ---------- */
  const profileTrigger = document.getElementById('adminProfileTrigger');
  const profilePanel = document.getElementById('adminProfilePanel');
  profileTrigger.addEventListener('click', (e) => {
    e.stopPropagation();
    profilePanel.classList.toggle('is-open');
  });
  document.addEventListener('click', () => profilePanel.classList.remove('is-open'));

  /* ---------- Logout ---------- */
  document.getElementById('adminSidebarLogout').addEventListener('click', () => ArckAuth.logout());
  document.getElementById('adminProfileLogout').addEventListener('click', () => ArckAuth.logout());

  /* ---------- Helpers ---------- */
  const escapeHtml = (str) => String(str).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const generatePassword = () => {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%';
    const bytes = new Uint32Array(12);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    return Array.from(bytes, (b) => chars[b % chars.length]).join('');
  };

  const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'Never';
  const formatDate = (iso) => iso ? new Date(iso).toLocaleDateString() : '—';

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
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

  /* ---------- Row action menu (kebab dropdown) ---------- */
  const rowMenu = document.getElementById('rowActionMenu');
  let rowMenuTrainer = null;

  const closeRowMenu = () => { rowMenu.classList.remove('is-open'); rowMenuTrainer = null; };

  const openRowMenu = (trigger, trainer) => {
    rowMenuTrainer = trainer;
    rowMenu.querySelector('[data-toggle-label]').textContent = trainer.is_active ? 'Disable Account' : 'Enable Account';
    rowMenu.classList.add('is-open');

    const rect = trigger.getBoundingClientRect();
    const menuWidth = 220;
    let left = rect.right - menuWidth;
    if (left < 8) left = 8;
    let top = rect.bottom + 6;
    rowMenu.style.left = `${left}px`;
    rowMenu.style.top = `${top}px`;

    // Flip above the trigger if it would overflow the viewport bottom.
    const menuHeight = rowMenu.offsetHeight;
    if (top + menuHeight > window.innerHeight - 8) {
      rowMenu.style.top = `${Math.max(8, rect.top - menuHeight - 6)}px`;
    }
  };

  document.addEventListener('click', (e) => {
    if (!rowMenu.contains(e.target)) closeRowMenu();
  });

  /* ---------- KPI cards ---------- */
  const kpiTotal = document.getElementById('kpiTotalTrainers');
  const kpiActive = document.getElementById('kpiActiveTrainers');
  const kpiPending = document.getElementById('kpiPendingTrainers');

  const renderKpis = (list) => {
    kpiTotal.textContent = list.length;
    kpiActive.textContent = list.filter((t) => t.is_active && !t.must_change_password).length;
    kpiPending.textContent = list.filter((t) => t.is_active && t.must_change_password).length;
  };

  /* ---------- Table rendering ---------- */
  const tbody = document.getElementById('staffTableBody');
  const serverBanner = document.getElementById('adminServerBanner');
  const showServerBanner = () => serverBanner.classList.add('is-visible');

  const statusBadge = (trainer) => {
    if (!trainer.is_active) return '<span class="admin-activity-badge is-danger">Disabled</span>';
    if (trainer.must_change_password) return '<span class="admin-activity-badge is-info">Awaiting First Login</span>';
    return '<span class="admin-activity-badge is-success">Active</span>';
  };

  const rowHtml = (trainer) => `
    <tr>
      <td>${escapeHtml(trainer.username)}</td>
      <td>${escapeHtml(trainer.full_name)}</td>
      <td>${statusBadge(trainer)}</td>
      <td title="${trainer.last_login_at ? new Date(trainer.last_login_at).toLocaleString() : ''}">${trainer.last_login_at ? formatDateTime(trainer.last_login_at) : 'Never'}</td>
      <td title="${new Date(trainer.created_at).toLocaleString()}">${formatDate(trainer.created_at)}</td>
      <td>
        <button type="button" class="table-action-btn" data-id="${trainer.id}" aria-label="Actions for ${escapeHtml(trainer.full_name)}"><i class="fa-solid fa-ellipsis-vertical"></i></button>
      </td>
    </tr>
  `;

  const renderTrainers = (list) => {
    tbody.innerHTML = list.length
      ? list.map(rowHtml).join('')
      : '<tr><td colspan="6" class="admin-panel-empty">No trainers yet. Click "Add Trainer" to create the first one.</td></tr>';
    renderKpis(list);
  };

  const loadTrainers = async () => {
    try {
      const list = await ArckAPI.request('/admin/staff');
      renderTrainers(list);
      return list;
    } catch (err) {
      if (err.status === 0) showServerBanner();
      tbody.innerHTML = '<tr><td colspan="6" class="admin-panel-empty">Couldn\'t load trainers.</td></tr>';
      renderKpis([]);
      return [];
    }
  };

  let trainers = await loadTrainers();
  const findTrainer = (id) => trainers.find((t) => t.id === id);
  const upsertTrainer = (updated) => {
    trainers = trainers.map((t) => (t.id === updated.id ? updated : t));
    renderTrainers(trainers);
  };

  tbody.addEventListener('click', (e) => {
    const btn = e.target.closest('.table-action-btn');
    if (!btn) return;
    e.stopPropagation();
    const trainer = findTrainer(Number(btn.dataset.id));
    if (!trainer) return;
    if (rowMenuTrainer && rowMenuTrainer.id === trainer.id && rowMenu.classList.contains('is-open')) {
      closeRowMenu();
    } else {
      openRowMenu(btn, trainer);
    }
  });

  /* ---------- Add trainer ---------- */
  const addPanel = document.getElementById('addStaffPanel');
  const toggleBtn = document.getElementById('toggleAddStaffBtn');
  if (!ArckAuth.hasPermission('trainers.create')) toggleBtn.style.display = 'none';
  if (!ArckAuth.hasPermission('trainers.delete')) {
    const deleteMenuItem = rowMenu.querySelector('[data-action="delete"]');
    if (deleteMenuItem) deleteMenuItem.style.display = 'none';
  }
  const cancelBtn = document.getElementById('cancelAddStaffBtn');
  const form = document.getElementById('addStaffForm');
  const nameInput = document.getElementById('newStaffName');
  const passwordInput = document.getElementById('newStaffPassword');
  const errorBox = document.getElementById('addStaffError');
  const submitBtn = document.getElementById('addStaffSubmitBtn');
  const notice = document.getElementById('newStaffNotice');

  document.getElementById('generateStaffPasswordBtn').addEventListener('click', () => {
    passwordInput.value = generatePassword();
  });

  const openAddPanel = () => { addPanel.style.display = 'block'; nameInput.focus(); };
  const closeAddPanel = () => {
    addPanel.style.display = 'none';
    form.reset();
    errorBox.style.display = 'none';
  };
  toggleBtn.addEventListener('click', () => {
    if (addPanel.style.display === 'none') openAddPanel(); else closeAddPanel();
  });
  cancelBtn.addEventListener('click', closeAddPanel);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';
    notice.style.display = 'none';

    submitBtn.disabled = true;
    try {
      const created = await ArckAPI.request('/admin/staff', {
        method: 'POST',
        body: {
          full_name: nameInput.value.trim(),
          temp_password: passwordInput.value,
        },
      });
      trainers = [created, ...trainers];
      renderTrainers(trainers);
      closeAddPanel();

      notice.innerHTML = `Created <code>${escapeHtml(created.username)}</code> for ${escapeHtml(created.full_name)}. Share the username and temporary password with the trainer — they'll be asked to set their own password on first login.`;
      notice.style.display = 'flex';
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not create trainer.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

  /* ---------- Action: View Details ---------- */
  const showViewDetails = (trainer) => {
    const grantedPermissions = Object.entries(trainer.permissions || {})
      .filter(([, v]) => v)
      .map(([k]) => PERMISSION_LABELS[k] || k);

    openModal({
      title: 'Trainer Details',
      bodyHtml: `
        <dl class="detail-list">
          <div class="detail-row"><dt>Name</dt><dd>${escapeHtml(trainer.full_name)}</dd></div>
          <div class="detail-row"><dt>Username</dt><dd>${escapeHtml(trainer.username)}</dd></div>
          <div class="detail-row"><dt>Email</dt><dd>${trainer.email ? escapeHtml(trainer.email) : '—'}</dd></div>
          <div class="detail-row"><dt>Account Status</dt><dd>${statusBadge(trainer)}</dd></div>
          <div class="detail-row"><dt>Created</dt><dd>${formatDateTime(trainer.created_at)}</dd></div>
          <div class="detail-row"><dt>Last Login</dt><dd>${trainer.last_login_at ? formatDateTime(trainer.last_login_at) : 'Never'}</dd></div>
        </dl>
        <div class="detail-section-title">Access</div>
        <p style="margin:0;">${grantedPermissions.length ? grantedPermissions.map(escapeHtml).join(', ') : 'No permissions granted yet.'}</p>
      `,
      footerHtml: `<button type="button" class="btn btn-primary-outline" data-close>Close</button>`,
    });
    modalFooter.querySelector('[data-close]').addEventListener('click', closeModal);
  };

  /* ---------- Action: Edit Trainer ---------- */
  const showEditTrainer = (trainer) => {
    openModal({
      title: 'Edit Trainer',
      bodyHtml: `
        <div class="admin-form-grid">
          <div>
            <label class="form-label">Full Name</label>
            <input type="text" class="form-control" id="editStaffName" value="${escapeHtml(trainer.full_name)}" required>
          </div>
          <div>
            <label class="form-label">Email</label>
            <input type="email" class="form-control" id="editStaffEmail" value="${trainer.email ? escapeHtml(trainer.email) : ''}">
          </div>
        </div>
        <div class="login-error" id="editStaffError" style="display:none; margin-top:14px;"></div>
      `,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn btn-accent" data-save>Save Changes</button>
      `,
    });

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-save]').addEventListener('click', async () => {
      const nameEl = document.getElementById('editStaffName');
      const emailEl = document.getElementById('editStaffEmail');
      const errEl = document.getElementById('editStaffError');
      try {
        const updated = await ArckAPI.request(`/admin/staff/${trainer.id}`, {
          method: 'PATCH',
          body: { full_name: nameEl.value.trim(), email: emailEl.value.trim() || null },
        });
        upsertTrainer(updated);
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not save changes.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Reset Password ---------- */
  const showResetPassword = (trainer) => {
    openModal({
      title: `Reset Password — ${escapeHtml(trainer.full_name)}`,
      bodyHtml: `
        <p>Set a new temporary password for this trainer. They'll be required to change it on their next login, and any devices they're currently logged in on will be signed out.</p>
        <label class="form-label">New Temporary Password</label>
        <div class="password-field-row">
          <input type="text" class="form-control" id="resetPasswordInput" minlength="8" required>
          <button type="button" class="btn btn-primary-outline" id="resetPasswordGenerateBtn">Generate</button>
        </div>
        <div class="login-error" id="resetPasswordError" style="display:none; margin-top:14px;"></div>
      `,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn btn-accent" data-save>Reset Password</button>
      `,
    });

    document.getElementById('resetPasswordGenerateBtn').addEventListener('click', () => {
      document.getElementById('resetPasswordInput').value = generatePassword();
    });
    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-save]').addEventListener('click', async () => {
      const pwdEl = document.getElementById('resetPasswordInput');
      const errEl = document.getElementById('resetPasswordError');
      if (pwdEl.value.length < 8) {
        errEl.textContent = 'Password must be at least 8 characters.';
        errEl.style.display = 'block';
        return;
      }
      try {
        const updated = await ArckAPI.request(`/admin/staff/${trainer.id}`, {
          method: 'PATCH',
          body: { reset_temp_password: pwdEl.value },
        });
        upsertTrainer(updated);
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not reset password.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Manage Permissions ---------- */
  const showManagePermissions = (trainer) => {
    const current = trainer.permissions || {};
    const groupsHtml = PERMISSION_GROUPS.map((group) => `
      <div class="permission-group">
        <h4>${escapeHtml(group.title)}</h4>
        ${group.items.map((item) => `
          <div class="permission-item">
            <input type="checkbox" id="perm-${item.key}" data-key="${item.key}" ${current[item.key] ? 'checked' : ''}>
            <label for="perm-${item.key}">${escapeHtml(item.label)}</label>
          </div>
        `).join('')}
      </div>
    `).join('');

    openModal({
      title: `Manage Permissions — ${escapeHtml(trainer.full_name)}`,
      bodyHtml: `${groupsHtml}<div class="login-error" id="permissionsError" style="display:none; margin-top:14px;"></div>`,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn btn-accent" data-save>Save Permissions</button>
      `,
    });

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-save]').addEventListener('click', async () => {
      const permissions = {};
      modalBody.querySelectorAll('input[type="checkbox"]').forEach((cb) => { permissions[cb.dataset.key] = cb.checked; });
      const errEl = document.getElementById('permissionsError');
      try {
        const updated = await ArckAPI.request(`/admin/staff/${trainer.id}`, {
          method: 'PATCH',
          body: { permissions },
        });
        upsertTrainer(updated);
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not save permissions.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Enable / Disable ---------- */
  const showToggleActive = (trainer) => {
    const enabling = !trainer.is_active;
    openModal({
      title: enabling ? 'Enable Account' : 'Disable Account',
      bodyHtml: `<p>${enabling
        ? `Re-enable <strong>${escapeHtml(trainer.full_name)}</strong>'s account? They will be able to log in again.`
        : `Disable <strong>${escapeHtml(trainer.full_name)}</strong>'s account? They will not be able to log in, and any active sessions will be signed out immediately.`}</p>
        <div class="login-error" id="toggleActiveError" style="display:none;"></div>`,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn ${enabling ? 'btn-accent' : 'btn-primary-outline'}" data-confirm style="${enabling ? '' : 'border-color:#d92d20;color:#d92d20;'}">${enabling ? 'Enable Account' : 'Disable Account'}</button>
      `,
    });

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-confirm]').addEventListener('click', async () => {
      const errEl = document.getElementById('toggleActiveError');
      try {
        const updated = await ArckAPI.request(`/admin/staff/${trainer.id}`, {
          method: 'PATCH',
          body: { is_active: enabling },
        });
        upsertTrainer(updated);
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not update account status.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Delete Account ---------- */
  const showDeleteAccount = (trainer) => {
    openModal({
      title: 'Delete Trainer Account',
      bodyHtml: `
        <p><strong>Are you sure you want to permanently delete this trainer account?</strong></p>
        <p>This removes <strong>${escapeHtml(trainer.full_name)}</strong> (${escapeHtml(trainer.username)})'s login permanently. This cannot be undone.</p>
        <div class="login-error" id="deleteAccountError" style="display:none;"></div>
      `,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn btn-accent" data-confirm style="background:#d92d20;border-color:#d92d20;">Delete Permanently</button>
      `,
    });

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-confirm]').addEventListener('click', async () => {
      const errEl = document.getElementById('deleteAccountError');
      try {
        await ArckAPI.request(`/admin/staff/${trainer.id}`, { method: 'DELETE' });
        trainers = trainers.filter((t) => t.id !== trainer.id);
        renderTrainers(trainers);
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not delete trainer.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Row action menu → dispatch ---------- */
  rowMenu.addEventListener('click', (e) => {
    const item = e.target.closest('[data-action]');
    if (!item || !rowMenuTrainer) return;
    const trainer = rowMenuTrainer;
    closeRowMenu();

    switch (item.dataset.action) {
      case 'view': showViewDetails(trainer); break;
      case 'edit': showEditTrainer(trainer); break;
      case 'reset': showResetPassword(trainer); break;
      case 'permissions': showManagePermissions(trainer); break;
      case 'toggle-active': showToggleActive(trainer); break;
      case 'delete': showDeleteAccount(trainer); break;
    }
  });

});
