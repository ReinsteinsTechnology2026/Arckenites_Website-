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
  let rowMenuRole = null;

  const closeRowMenu = () => { rowMenu.classList.remove('is-open'); rowMenuRole = null; };

  const openRowMenu = (trigger, role) => {
    rowMenuRole = role;
    rowMenu.classList.add('is-open');
    // Super Admin: only allow viewing (read-only), no rename/delete.
    const isLocked = role.slug === 'super_admin';
    rowMenu.querySelectorAll('[data-action="edit"], [data-action="delete"]').forEach((el) => {
      el.style.display = isLocked ? 'none' : '';
    });
    rowMenu.querySelector('[data-action="permissions"]').textContent = isLocked ? ' View Permissions' : ' View / Edit Permissions';

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

  /* ---------- Table rendering ---------- */
  const tbody = document.getElementById('rolesTableBody');
  const serverBanner = document.getElementById('adminServerBanner');
  const showServerBanner = () => serverBanner.classList.add('is-visible');

  const rowHtml = (role) => `
    <tr>
      <td>${escapeHtml(role.name)}${role.slug === 'super_admin' ? ' <i class="fa-solid fa-lock" style="color:var(--muted-2); font-size:.75rem;" title="Always has full access"></i>' : ''}</td>
      <td style="text-align:right;">${role.user_count}</td>
      <td style="text-align:right;">${role.slug === 'super_admin' ? 'All' : role.permission_count}</td>
      <td>${role.is_system ? '<span class="admin-activity-badge is-info">Built-in</span>' : '<span class="admin-activity-badge is-muted">Custom</span>'}</td>
      <td>
        <button type="button" class="table-action-btn" data-id="${role.id}" aria-label="Actions for ${escapeHtml(role.name)}"><i class="fa-solid fa-ellipsis-vertical"></i></button>
      </td>
    </tr>
  `;

  const renderRoles = (list) => {
    tbody.innerHTML = list.length
      ? list.map(rowHtml).join('')
      : '<tr><td colspan="5" class="admin-panel-empty">No roles yet.</td></tr>';
  };

  const loadRoles = async () => {
    try {
      const list = await ArckAPI.request('/admin/roles');
      renderRoles(list);
      return list;
    } catch (err) {
      if (err.status === 0) showServerBanner();
      if (err.status === 403) tbody.innerHTML = '<tr><td colspan="5" class="admin-panel-empty">You do not have permission to view roles.</td></tr>';
      else tbody.innerHTML = '<tr><td colspan="5" class="admin-panel-empty">Couldn\'t load roles.</td></tr>';
      return [];
    }
  };

  let roles = await loadRoles();
  const findRole = (id) => roles.find((r) => r.id === id);

  tbody.addEventListener('click', (e) => {
    const btn = e.target.closest('.table-action-btn');
    if (!btn) return;
    e.stopPropagation();
    const role = findRole(Number(btn.dataset.id));
    if (!role) return;
    if (rowMenuRole && rowMenuRole.id === role.id && rowMenu.classList.contains('is-open')) {
      closeRowMenu();
    } else {
      openRowMenu(btn, role);
    }
  });

  /* ---------- Add role ---------- */
  const addPanel = document.getElementById('addRolePanel');
  const toggleBtn = document.getElementById('toggleAddRoleBtn');
  const cancelBtn = document.getElementById('cancelAddRoleBtn');
  const form = document.getElementById('addRoleForm');
  const nameInput = document.getElementById('newRoleName');
  const descInput = document.getElementById('newRoleDescription');
  const errorBox = document.getElementById('addRoleError');
  const submitBtn = document.getElementById('addRoleSubmitBtn');

  const openAddPanel = () => { addPanel.style.display = 'block'; nameInput.focus(); };
  const closeAddPanel = () => { addPanel.style.display = 'none'; form.reset(); errorBox.style.display = 'none'; };
  toggleBtn.addEventListener('click', () => {
    if (addPanel.style.display === 'none') openAddPanel(); else closeAddPanel();
  });
  cancelBtn.addEventListener('click', closeAddPanel);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';
    submitBtn.disabled = true;
    try {
      await ArckAPI.request('/admin/roles', {
        method: 'POST',
        body: { name: nameInput.value.trim(), description: descInput.value.trim() || null, permission_keys: [] },
      });
      closeAddPanel();
      roles = await loadRoles();
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not create role.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

  /* ---------- Action: View / Edit Permissions ---------- */
  const showPermissions = async (role) => {
    const isLocked = role.slug === 'super_admin';
    openModal({
      title: `Permissions — ${escapeHtml(role.name)}`,
      bodyHtml: `<div id="permissionsGrid" class="admin-panel-empty">Loading&hellip;</div>`,
      footerHtml: isLocked
        ? `<button type="button" class="btn btn-primary-outline" data-close>Close</button>`
        : `<button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
           <button type="button" class="btn btn-accent" data-save>Save Permissions</button>`,
    });

    if (isLocked) {
      modalFooter.querySelector('[data-close]').addEventListener('click', closeModal);
      modalBody.innerHTML = `<p>Super Admin always has full access to every permission — this can't be changed.</p>`;
      return;
    }

    let catalog, detail;
    try {
      [catalog, detail] = await Promise.all([
        ArckAPI.request('/admin/permissions/catalog'),
        ArckAPI.request(`/admin/roles/${role.id}`),
      ]);
    } catch (err) {
      modalBody.innerHTML = `<p>Couldn't load permissions.</p>`;
      return;
    }

    const grouped = {};
    catalog.forEach((p) => {
      if (!grouped[p.module]) grouped[p.module] = [];
      grouped[p.module].push(p);
    });
    const granted = new Set(detail.granted_permission_keys);

    const groupsHtml = Object.entries(grouped).map(([moduleName, perms]) => `
      <div class="permission-group">
        <h4>${escapeHtml(moduleName)}</h4>
        ${perms.map((p) => `
          <div class="permission-item">
            <input type="checkbox" id="perm-${p.key}" data-key="${p.key}" ${granted.has(p.key) ? 'checked' : ''}>
            <label for="perm-${p.key}">${escapeHtml(p.description || p.key)}</label>
          </div>
        `).join('')}
      </div>
    `).join('');

    modalBody.innerHTML = `${groupsHtml}<div class="login-error" id="permissionsError" style="display:none; margin-top:14px;"></div>`;

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-save]').addEventListener('click', async () => {
      const permission_keys = Array.from(modalBody.querySelectorAll('input[type="checkbox"]:checked')).map((cb) => cb.dataset.key);
      const errEl = document.getElementById('permissionsError');
      try {
        await ArckAPI.request(`/admin/roles/${role.id}`, { method: 'PATCH', body: { permission_keys } });
        closeModal();
        roles = await loadRoles();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not save permissions.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Rename / Edit ---------- */
  const showEditRole = (role) => {
    openModal({
      title: 'Edit Role',
      bodyHtml: `
        <div class="admin-form-grid">
          <div>
            <label class="form-label">Role Name</label>
            <input type="text" class="form-control" id="editRoleName" value="${escapeHtml(role.name)}" ${role.is_system ? 'disabled' : ''}>
          </div>
          <div>
            <label class="form-label">Description</label>
            <input type="text" class="form-control" id="editRoleDescription" value="${role.description ? escapeHtml(role.description) : ''}" ${role.is_system ? 'disabled' : ''}>
          </div>
        </div>
        ${role.is_system ? '<p style="margin-top:10px;">Built-in role names can\'t be changed.</p>' : ''}
        <div class="login-error" id="editRoleError" style="display:none; margin-top:14px;"></div>
      `,
      footerHtml: role.is_system
        ? `<button type="button" class="btn btn-primary-outline" data-close>Close</button>`
        : `<button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
           <button type="button" class="btn btn-accent" data-save>Save Changes</button>`,
    });

    if (role.is_system) {
      modalFooter.querySelector('[data-close]').addEventListener('click', closeModal);
      return;
    }

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-save]').addEventListener('click', async () => {
      const nameEl = document.getElementById('editRoleName');
      const descEl = document.getElementById('editRoleDescription');
      const errEl = document.getElementById('editRoleError');
      try {
        await ArckAPI.request(`/admin/roles/${role.id}`, {
          method: 'PATCH',
          body: { name: nameEl.value.trim(), description: descEl.value.trim() || null },
        });
        closeModal();
        roles = await loadRoles();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not save changes.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Action: Delete Role ---------- */
  const showDeleteRole = (role) => {
    openModal({
      title: 'Delete Role',
      bodyHtml: `
        <p><strong>Are you sure you want to permanently delete this role?</strong></p>
        <p>This removes "${escapeHtml(role.name)}". Any admin currently assigned to it must be reassigned first.</p>
        <div class="login-error" id="deleteRoleError" style="display:none;"></div>
      `,
      footerHtml: `
        <button type="button" class="btn btn-primary-outline" data-cancel>Cancel</button>
        <button type="button" class="btn btn-accent" data-confirm style="background:#d92d20;border-color:#d92d20;">Delete Permanently</button>
      `,
    });

    modalFooter.querySelector('[data-cancel]').addEventListener('click', closeModal);
    modalFooter.querySelector('[data-confirm]').addEventListener('click', async () => {
      const errEl = document.getElementById('deleteRoleError');
      try {
        await ArckAPI.request(`/admin/roles/${role.id}`, { method: 'DELETE' });
        closeModal();
        roles = await loadRoles();
      } catch (err) {
        errEl.textContent = err.detail || 'Could not delete role.';
        errEl.style.display = 'block';
      }
    });
  };

  /* ---------- Row action menu → dispatch ---------- */
  rowMenu.addEventListener('click', (e) => {
    const item = e.target.closest('[data-action]');
    if (!item || !rowMenuRole) return;
    const role = rowMenuRole;
    closeRowMenu();

    switch (item.dataset.action) {
      case 'permissions': showPermissions(role); break;
      case 'edit': showEditRole(role); break;
      case 'delete': showDeleteRole(role); break;
    }
  });

  if (!ArckAuth.hasPermission('roles.create')) {
    toggleBtn.style.display = 'none';
  }

});
