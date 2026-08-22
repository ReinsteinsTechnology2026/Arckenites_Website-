const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

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
  let rowMenuAdmin = null;

  const closeRowMenu = () => { rowMenu.classList.remove('is-open'); rowMenuAdmin = null; };

  const openRowMenu = (trigger, admin) => {
    rowMenuAdmin = admin;
    rowMenu.querySelector('[data-toggle-label]').textContent = admin.is_active ? 'Disable Account' : 'Enable Account';
    rowMenu.classList.add('is-open');

    const rect = trigger.getBoundingClientRect();
    const menuWidth = 220;
    let left = rect.right - menuWidth;
    if (left < 8) left = 8;
    let top = rect.bottom + 6;
    rowMenu.style.left = `${left}px`;
    rowMenu.style.top = `${top}px`;

    const menuHeight = rowMenu.offsetHeight;
    if (top + menuHeight > window.innerHeight - 8) {
      rowMenu.style.top = `${Math.max(8, rect.top - menuHeight - 6)}px`;
    }
  };

  document.addEventListener('click', (e) => {
    if (!rowMenu.contains(e.target)) closeRowMenu();
  });

  /* ---------- Roles (for the Role selects) ---------- */
  let roles = [];
  const loadRoles = async () => {
    try {
      roles = await ArckAPI.request('/admin/roles');
    } catch (_) {
      roles = [];
    }
  };
  const roleOptionsHtml = (selectedId) => roles.map((r) => `
    <option value="${r.id}" ${r.id === selectedId ? 'selected' : ''}>${escapeHtml(r.name)}</option>
  `).join('');

  /* ---------- KPI cards ---------- */
  const kpiTotal = document.getElementById('kpiTotalAdmins');
  const kpiActive = document.getElementById('kpiActiveAdmins');
  const kpiDisabled = document.getElementById('kpiDisabledAdmins');

  const renderKpis = (list) => {
    kpiTotal.textContent = list.length;
    kpiActive.textContent = list.filter((a) => a.is_active).length;
    kpiDisabled.textContent = list.filter((a) => !a.is_active).length;
  };

  /* ---------- Table rendering ---------- */
  const tbody = document.getElementById('adminUsersTableBody');
  const serverBanner = document.getElementById('adminServerBanner');
  const showServerBanner = () => serverBanner.classList.add('is-visible');

  const statusBadge = (admin) => {
    if (!admin.is_active) return '<span class="admin-activity-badge is-danger">Disabled</span>';
    if (admin.must_change_password) return '<span class="admin-activity-badge is-info">Pending First Login</span>';
    return '<span class="admin-activity-badge is-success">Active</span>';
  };

  const rowHtml = (admin) => `
    <tr>
      <td>${escapeHtml(admin.full_name)}</td>
      <td>${escapeHtml(admin.username)}</td>
      <td>${admin.email ? escapeHtml(admin.email) : '—'}</td>
      <td>${admin.admin_role ? escapeHtml(admin.admin_role.name) : '—'}</td>
      <td>${statusBadge(admin)}</td>
      <td title="${admin.last_login_at ? new Date(admin.last_login_at).toLocaleString() : ''}">${admin.last_login_at ? formatDateTime(admin.last_login_at) : 'Never'}</td>
      <td title="${new Date(admin.created_at).toLocaleString()}">${formatDate(admin.created_at)}</td>
      <td>
        <button type="button" class="table-action-btn" data-id="${admin.id}" aria-label="Actions for ${escapeHtml(admin.full_name)}"><i class="fa-solid fa-ellipsis-vertical"></i></button>
      </td>
    </tr>
  `;

  const renderAdmins = (list) => {
    tbody.innerHTML = list.length
      ? list.map(rowHtml).join('')
      : '<tr><td colspan="8" class="admin-panel-empty">No admin users yet. Click "Add Admin" to create the first one.</td></tr>';
    renderKpis(list);
  };

  const loadAdmins = async () => {
    try {
      const list = await ArckAPI.request('/admin/admin-users');
      renderAdmins(list);
      return list;
    } catch (err) {
      if (err.status === 0) showServerBanner();
      if (err.status === 403) tbody.innerHTML = '<tr><td colspan="8" class="admin-panel-empty">You do not have permission to view admin users.</td></tr>';
      else tbody.innerHTML = '<tr><td colspan="8" class="admin-panel-empty">Couldn\'t load admin users.</td></tr>';
      renderKpis([]);
      return [];
    }
  };

  await loadRoles();
  let admins = await loadAdmins();
  const findAdmin = (id) => admins.find((a) => a.id === id);
  const upsertAdmin = (updated) => {
    admins = admins.map((a) => (a.id === updated.id ? updated : a));
    renderAdmins(admins);
  };

  tbody.addEventListener('click', (e) => {
    const btn = e.target.closest('.table-action-btn');
    if (!btn) return;
    e.stopPropagation();
    const admin = findAdmin(Number(btn.dataset.id));
    if (!admin) return;
    if (rowMenuAdmin && rowMenuAdmin.id === admin.id && rowMenu.classList.contains('is-open')) {
      closeRowMenu();
    } else {
      openRowMenu(btn, admin);
    }
  });

  /* ---------- Add admin ---------- */
  const addPanel = document.getElementById('addAdminPanel');
  const toggleBtn = document.getElementById('toggleAddAdminBtn');
  const cancelBtn = document.getElementById('cancelAddAdminBtn');
  const form = document.getElementById('addAdminForm');
  const nameInput = document.getElementById('newAdminName');
  const roleSelect = document.getElementById('newAdminRole');
  const passwordInput = document.getElementById('newAdminPassword');
  const errorBox = document.getElementById('addAdminError');
  const submitBtn = document.getElementById('addAdminSubmitBtn');
  const notice = document.getElementById('newAdminNotice');

  roleSelect.innerHTML = roleOptionsHtml(null);

  document.getElementById('generateAdminPasswordBtn').addEventListener('click', () => {
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
      const created = await ArckAPI.request('/admin/admin-users', {
        method: 'POST',
        body: {
          full_name: nameInput.value.trim(),
          admin_role_id: Number(roleSelect.value),
          temp_password: passwordInput.value,
        },
      });
      admins = [created, ...admins];
      renderAdmins(admins);
      closeAddPanel();

      notice.innerHTML = `Created <code>${escapeHtml(created.username)}</code> for ${escapeHtml(created.full_name)}. Share the username and temporary password with them — they'll be asked to set their own password on first login.`;
      notice.style.display = 'flex';
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not create admin.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

  /* ---------- Action: View Details ---------- */
  const showViewDetails = (admin) => {
    openModal({
      title: 'Admin Details',
      bodyHtml: `
        <dl class="detail-list">
          <div class="detail-row"><dt>Name</dt><dd>${escapeHtml(admin.full_name)}</dd></div>
          <div class="detail-row"><dt>Username</dt><dd>${escapeHtml(admin.username)}</dd></div>
          <div class="detail-row"><dt>Email</dt><dd>${admin.email ? escapeHtml(admin.email) : '—'}</dd></div>
          <div class="detail-row"><dt>Role</dt><dd>${admin.admin_role ? escapeHtml(admin.admin_role.name) : '—'}</dd></div>
          <div class="detail-row"><dt>Account Status</dt><dd>${statusBadge(admin)}</dd></div>
          <div class="detail-row"><dt>Created</dt><dd>${formatDateTime(admin.created_at)}</dd></div>
          <div class="detail-row"><dt>Last Login</dt><dd>${admin.last_login_at ? formatDateTime(admin.last_login_at) : 'Never'}</dd></div>
        </dl>
      `,
      footerHtml: `<button type="button" class="btn btn-primary-outline" data-close>Close</button>`,
    });
    modalFooter.querySelector('[data-close]').addEventListener('click', closeModal);
  };

  /* ---------- Action: Edit Admin ---------- */
  const showEditAdmin = (admin) => {
    openModal({
      title: 'Edit Admin',
      bodyHtml: `
        <div class="admin-form-grid">
          <div>
            <label class="form-label">Full Name</label>
            <input type="text" class="form-control" id="editAdminName" value="${escapeHtml(admin.full_name)}" required>
          </div>
          <div>
            <label class="form-label">Email</label>
            <input type="email" class="form-control" id="editAdminEmail" value="${admin.email ? escapeHtml(admin.email) : ''}">
          </div>
        </div>
        <div class="login-error" id="editAdminError" style="display:none; margin-top:14px;"></div>
      `,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn btn-accent" data-save>Save Changes</button>
      `,
    });

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-save]').addEventListener('click', async () => {
      const nameEl = document.getElementById('editAdminName');
      const emailEl = document.getElementById('editAdminEmail');
      const errEl = document.getElementById('editAdminError');
      try {
        const updated = await ArckAPI.request(`/admin/admin-users/${admin.id}`, {
          method: 'PATCH',
          body: { full_name: nameEl.value.trim(), email: emailEl.value.trim() || null },
        });
        upsertAdmin(updated);
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not save changes.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Change Role ---------- */
  const showChangeRole = (admin) => {
    openModal({
      title: `Change Role — ${escapeHtml(admin.full_name)}`,
      bodyHtml: `
        <label class="form-label">Role</label>
        <select class="form-select" id="changeRoleSelect">${roleOptionsHtml(admin.admin_role ? admin.admin_role.id : null)}</select>
        <div class="login-error" id="changeRoleError" style="display:none; margin-top:14px;"></div>
      `,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn btn-accent" data-save>Save Role</button>
      `,
    });

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-save]').addEventListener('click', async () => {
      const selectEl = document.getElementById('changeRoleSelect');
      const errEl = document.getElementById('changeRoleError');
      try {
        const updated = await ArckAPI.request(`/admin/admin-users/${admin.id}`, {
          method: 'PATCH',
          body: { admin_role_id: Number(selectEl.value) },
        });
        upsertAdmin(updated);
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not change role.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Reset Password ---------- */
  const showResetPassword = (admin) => {
    openModal({
      title: `Reset Password — ${escapeHtml(admin.full_name)}`,
      bodyHtml: `
        <p>Set a new temporary password for this admin. They'll be required to change it on their next login, and any devices they're currently logged in on will be signed out.</p>
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
        const updated = await ArckAPI.request(`/admin/admin-users/${admin.id}`, {
          method: 'PATCH',
          body: { reset_temp_password: pwdEl.value },
        });
        upsertAdmin(updated);
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not reset password.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Revoke Sessions ---------- */
  const showRevokeSessions = (admin) => {
    openModal({
      title: 'Revoke Sessions',
      bodyHtml: `<p>Sign <strong>${escapeHtml(admin.full_name)}</strong> out of every device they're currently logged in on?</p>
        <div class="login-error" id="revokeSessionsError" style="display:none;"></div>`,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn btn-accent" data-confirm>Revoke Sessions</button>
      `,
    });

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-confirm]').addEventListener('click', async () => {
      const errEl = document.getElementById('revokeSessionsError');
      try {
        await ArckAPI.request(`/admin/admin-users/${admin.id}/revoke-sessions`, { method: 'POST' });
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not revoke sessions.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Enable / Disable ---------- */
  const showToggleActive = (admin) => {
    const enabling = !admin.is_active;
    openModal({
      title: enabling ? 'Enable Account' : 'Disable Account',
      bodyHtml: `<p>${enabling
        ? `Re-enable <strong>${escapeHtml(admin.full_name)}</strong>'s account? They will be able to log in again.`
        : `Disable <strong>${escapeHtml(admin.full_name)}</strong>'s account? They will not be able to log in, and any active sessions will be signed out immediately.`}</p>
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
        const updated = await ArckAPI.request(`/admin/admin-users/${admin.id}`, {
          method: 'PATCH',
          body: { is_active: enabling },
        });
        upsertAdmin(updated);
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not update account status.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Delete Account ---------- */
  const showDeleteAccount = (admin) => {
    openModal({
      title: 'Delete Admin Account',
      bodyHtml: `
        <p><strong>Are you sure you want to permanently delete this admin account?</strong></p>
        <p>This removes ${escapeHtml(admin.full_name)} (${escapeHtml(admin.username)})'s login permanently. This cannot be undone.</p>
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
        await ArckAPI.request(`/admin/admin-users/${admin.id}`, { method: 'DELETE' });
        admins = admins.filter((a) => a.id !== admin.id);
        renderAdmins(admins);
        closeModal();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not delete admin.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Row action menu → dispatch ---------- */
  rowMenu.addEventListener('click', (e) => {
    const item = e.target.closest('[data-action]');
    if (!item || !rowMenuAdmin) return;
    const admin = rowMenuAdmin;
    closeRowMenu();

    switch (item.dataset.action) {
      case 'view': showViewDetails(admin); break;
      case 'edit': showEditAdmin(admin); break;
      case 'role': showChangeRole(admin); break;
      case 'reset': showResetPassword(admin); break;
      case 'sessions': showRevokeSessions(admin); break;
      case 'toggle-active': showToggleActive(admin); break;
      case 'delete': showDeleteAccount(admin); break;
    }
  });

  /* ---------- Hide Add button if not permitted (server still enforces regardless) ---------- */
  if (!ArckAuth.hasPermission('admin_users.create')) {
    toggleBtn.style.display = 'none';
  }

});
