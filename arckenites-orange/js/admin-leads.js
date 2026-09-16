const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const STATUS_LABELS = { new: 'New', contacted: 'Contacted', qualified: 'Qualified', converted: 'Converted', lost: 'Lost' };
const STATUS_BADGE = { new: 'is-info', contacted: 'is-pending', qualified: 'is-muted', converted: 'is-success', lost: 'is-danger' };

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
  const openMobileSidebar = () => {
    sidebar.classList.add('is-mobile-open');
    backdrop.classList.add('is-visible');
  };
  const closeMobileSidebar = () => {
    sidebar.classList.remove('is-mobile-open');
    backdrop.classList.remove('is-visible');
  };
  document.getElementById('adminMobileToggle').addEventListener('click', openMobileSidebar);
  backdrop.addEventListener('click', closeMobileSidebar);
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

  /* ---------- Export to Excel ---------- */
  const exportBtn = document.getElementById('exportLeadsBtn');
  if (!ArckAuth.hasPermission('leads.export')) exportBtn.style.display = 'none';
  exportBtn.addEventListener('click', async () => {
    exportBtn.disabled = true;
    try {
      const res = await fetch(`${API_BASE}/admin/leads/export`, {
        headers: { Authorization: `Bearer ${ArckAPI.getToken()}` },
      });
      if (!res.ok) throw new Error('Export failed');

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `arckenites-leads-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (_) {
      window.alert('Could not export leads. Check that the server is running and try again.');
    } finally {
      exportBtn.disabled = false;
    }
  });

  /* ---------- Add-lead panel toggle ---------- */
  const addPanel = document.getElementById('addLeadPanel');
  const toggleBtn = document.getElementById('toggleAddLeadBtn');
  if (!ArckAuth.hasPermission('leads.create')) toggleBtn.style.display = 'none';
  const cancelBtn = document.getElementById('cancelAddLeadBtn');
  const form = document.getElementById('addLeadForm');
  const nameInput = document.getElementById('newLeadName');
  const phoneInput = document.getElementById('newLeadPhone');
  const emailInput = document.getElementById('newLeadEmail');
  const sourceInput = document.getElementById('newLeadSource');
  const notesInput = document.getElementById('newLeadNotes');
  const errorBox = document.getElementById('addLeadError');
  const submitBtn = document.getElementById('addLeadSubmitBtn');

  const openAddPanel = () => { closeEditPanel(); addPanel.style.display = 'block'; nameInput.focus(); };
  const closeAddPanel = () => {
    addPanel.style.display = 'none';
    form.reset();
    errorBox.style.display = 'none';
  };
  toggleBtn.addEventListener('click', () => {
    if (addPanel.style.display === 'none') openAddPanel(); else closeAddPanel();
  });
  cancelBtn.addEventListener('click', closeAddPanel);

  /* ---------- Table rendering ---------- */
  const tbody = document.getElementById('leadsTableBody');
  const serverBanner = document.getElementById('adminServerBanner');
  const showServerBanner = () => serverBanner.classList.add('is-visible');

  const canEditLeads = ArckAuth.hasPermission('leads.edit');
  const canDeleteLeads = ArckAuth.hasPermission('leads.delete');

  const statusBadge = (lead) => `<span class="admin-activity-badge ${STATUS_BADGE[lead.status] || 'is-muted'}">${STATUS_LABELS[lead.status] || lead.status}</span>`;

  const rowHtml = (lead) => `
    <tr>
      <td>${escapeHtml(lead.full_name)}</td>
      <td>${lead.phone ? escapeHtml(lead.phone) : '—'}</td>
      <td>${lead.email ? escapeHtml(lead.email) : '—'}</td>
      <td>${lead.source ? escapeHtml(lead.source) : '—'}</td>
      <td>${statusBadge(lead)}</td>
      <td>${lead.created_by_name ? escapeHtml(lead.created_by_name) : '—'}</td>
      <td title="${new Date(lead.created_at).toLocaleString()}">${new Date(lead.created_at).toLocaleDateString()}</td>
      <td>
        <div class="admin-row-actions">
          ${canEditLeads ? `<button type="button" class="table-action-btn" data-action="edit" data-id="${lead.id}" title="Edit ${escapeHtml(lead.full_name)}"><i class="fa-solid fa-pen"></i></button>` : ''}
          ${canDeleteLeads ? `<button type="button" class="table-action-btn is-danger" data-action="delete" data-id="${lead.id}" title="Delete ${escapeHtml(lead.full_name)}"><i class="fa-solid fa-trash"></i></button>` : ''}
        </div>
      </td>
    </tr>
  `;

  const renderLeads = (list) => {
    tbody.innerHTML = list.length
      ? list.map(rowHtml).join('')
      : `<tr><td colspan="8" class="admin-panel-empty">No leads yet. Click "Add New Lead" to create the first one.</td></tr>`;
  };

  const loadLeads = async () => {
    try {
      const list = await ArckAPI.request('/admin/leads');
      renderLeads(list);
      return list;
    } catch (err) {
      if (err.status === 0) showServerBanner();
      tbody.innerHTML = `<tr><td colspan="8" class="admin-panel-empty">Couldn't load leads.</td></tr>`;
      return [];
    }
  };

  let leads = await loadLeads();

  /* ---------- Create lead ---------- */
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';
    submitBtn.disabled = true;

    try {
      const created = await ArckAPI.request('/admin/leads', {
        method: 'POST',
        body: {
          full_name: nameInput.value.trim(),
          phone: phoneInput.value.trim() || null,
          email: emailInput.value.trim() || null,
          source: sourceInput.value.trim() || null,
          notes: notesInput.value.trim() || null,
        },
      });
      leads = [created, ...leads];
      renderLeads(leads);
      closeAddPanel();
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not create lead.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

  /* ---------- Edit lead ---------- */
  const editPanel = document.getElementById('editLeadPanel');
  const editForm = document.getElementById('editLeadForm');
  const editIdInput = document.getElementById('editLeadId');
  const editNameInput = document.getElementById('editLeadName');
  const editPhoneInput = document.getElementById('editLeadPhone');
  const editEmailInput = document.getElementById('editLeadEmail');
  const editSourceInput = document.getElementById('editLeadSource');
  const editStatusSelect = document.getElementById('editLeadStatus');
  const editNotesInput = document.getElementById('editLeadNotes');
  const editErrorBox = document.getElementById('editLeadError');
  const editSubmitBtn = document.getElementById('editLeadSubmitBtn');
  const cancelEditBtn = document.getElementById('cancelEditLeadBtn');

  const closeEditPanel = () => {
    editPanel.style.display = 'none';
    editForm.reset();
    editErrorBox.style.display = 'none';
  };
  const openEditPanel = (lead) => {
    closeAddPanel();
    editIdInput.value = lead.id;
    editNameInput.value = lead.full_name;
    editPhoneInput.value = lead.phone || '';
    editEmailInput.value = lead.email || '';
    editSourceInput.value = lead.source || '';
    editStatusSelect.value = lead.status;
    editNotesInput.value = lead.notes || '';
    editErrorBox.style.display = 'none';
    editPanel.style.display = 'block';
    editNameInput.focus();
  };
  cancelEditBtn.addEventListener('click', closeEditPanel);

  editForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    editErrorBox.style.display = 'none';
    editSubmitBtn.disabled = true;

    const body = {
      full_name: editNameInput.value.trim(),
      phone: editPhoneInput.value.trim() || null,
      email: editEmailInput.value.trim() || null,
      source: editSourceInput.value.trim() || null,
      status: editStatusSelect.value,
      notes: editNotesInput.value.trim() || null,
    };

    try {
      const updated = await ArckAPI.request(`/admin/leads/${editIdInput.value}`, { method: 'PATCH', body });
      leads = leads.map((l) => (l.id === updated.id ? updated : l));
      renderLeads(leads);
      closeEditPanel();
    } catch (err) {
      editErrorBox.textContent = err.detail || 'Could not update lead.';
      editErrorBox.style.display = 'block';
    } finally {
      editSubmitBtn.disabled = false;
    }
  });

  /* ---------- Row actions (edit / delete) ---------- */
  tbody.addEventListener('click', async (e) => {
    const btn = e.target.closest('.table-action-btn');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const lead = leads.find((l) => l.id === id);
    if (!lead) return;

    if (btn.dataset.action === 'edit') {
      openEditPanel(lead);
      return;
    }

    if (btn.dataset.action === 'delete') {
      const confirmed = window.confirm(`Delete lead "${lead.full_name}"? This cannot be undone.`);
      if (!confirmed) return;

      btn.disabled = true;
      try {
        await ArckAPI.request(`/admin/leads/${id}`, { method: 'DELETE' });
        leads = leads.filter((l) => l.id !== id);
        renderLeads(leads);
        if (editIdInput.value === String(id)) closeEditPanel();
      } catch (err) {
        window.alert(err.detail || 'Could not delete lead.');
        btn.disabled = false;
      }
    }
  });

});
