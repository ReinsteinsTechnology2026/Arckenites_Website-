const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDate = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const MODE_LABEL = { online: 'Online', offline: 'Offline', hybrid: 'Hybrid' };

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

  /* ---------- Stats ---------- */
  const loadStats = async () => {
    try {
      const stats = await ArckAPI.request('/admin/programs/stats');
      document.getElementById('kpiTotalPrograms').textContent = stats.total_programs;
      document.getElementById('kpiActivePrograms').textContent = stats.active_programs;
      document.getElementById('kpiInactivePrograms').textContent = stats.inactive_programs;
      document.getElementById('kpiTotalEnrolled').textContent = stats.total_enrolled_students;
      document.getElementById('kpiUpcomingBatchPrograms').textContent = stats.programs_with_upcoming_batches;
    } catch (err) {
      if (err.status === 0) showServerBanner();
    }
  };

  /* ---------- Cards ---------- */
  const grid = document.getElementById('programCardGrid');
  let programs = [];

  const cardHtml = (p) => `
    <div class="program-card ${p.status === 'inactive' ? 'is-inactive' : ''}" data-id="${p.id}">
      <div class="program-card-header">
        <div>
          <h3>${escapeHtml(p.name)}</h3>
          ${p.code ? `<span class="program-card-code">${escapeHtml(p.code)}</span>` : ''}
        </div>
        <span class="admin-activity-badge ${p.status === 'active' ? 'is-success' : 'is-muted'}">${p.status === 'active' ? 'Active' : 'Inactive'}</span>
      </div>
      <p class="program-card-desc">${escapeHtml(p.description) || 'No description yet.'}</p>
      <div class="program-card-meta">
        <span><i class="fa-solid fa-tag"></i> ${escapeHtml(p.category) || 'Uncategorized'}</span>
        <span><i class="fa-solid fa-user-graduate"></i> ${p.enrolled_count} enrolled</span>
        <span><i class="fa-solid fa-people-group"></i> ${p.active_batch_count} active batches</span>
        <span><i class="fa-solid fa-calendar"></i> ${formatDate(p.created_at)}</span>
      </div>
      <div class="program-card-actions">
        <a class="btn btn-accent" href="admin-program-detail.html?id=${p.id}">Manage</a>
        <a class="btn btn-primary-outline" href="admin-program-detail.html?id=${p.id}">View</a>
        <button type="button" class="btn btn-primary-outline" data-action="edit" data-id="${p.id}">Edit</button>
        <button type="button" class="btn btn-primary-outline ${p.status === 'active' ? 'is-danger' : ''}" data-action="toggle" data-id="${p.id}">
          ${p.status === 'active' ? 'Deactivate' : 'Activate'}
        </button>
      </div>
    </div>
  `;

  const renderCards = () => {
    grid.innerHTML = programs.length
      ? programs.map(cardHtml).join('')
      : '<div class="admin-panel-empty">No programs yet. Click "Create New Program" to add the first one.</div>';
  };

  const loadPrograms = async () => {
    try {
      programs = await ArckAPI.request('/admin/programs');
      renderCards();
    } catch (err) {
      if (err.status === 0) showServerBanner();
      grid.innerHTML = '<div class="admin-panel-empty">Couldn\'t load programs.</div>';
    }
  };

  /* ---------- Create program ---------- */
  const addPanel = document.getElementById('addProgramPanel');
  const addForm = document.getElementById('addProgramForm');
  const addErrorBox = document.getElementById('addProgramError');
  const addSubmitBtn = document.getElementById('addProgramSubmitBtn');

  document.getElementById('toggleAddProgramBtn').addEventListener('click', () => {
    const isHidden = addPanel.style.display === 'none';
    addPanel.style.display = isHidden ? 'block' : 'none';
    if (isHidden) document.getElementById('programName').focus();
  });
  document.getElementById('cancelAddProgramBtn').addEventListener('click', () => {
    addPanel.style.display = 'none';
    addForm.reset();
    addErrorBox.style.display = 'none';
  });

  addForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    addErrorBox.style.display = 'none';
    addSubmitBtn.disabled = true;

    const body = {
      name: document.getElementById('programName').value.trim(),
      code: document.getElementById('programCode').value.trim() || null,
      category: document.getElementById('programCategory').value.trim() || null,
      duration: document.getElementById('programDuration').value.trim() || null,
      mode: document.getElementById('programMode').value,
      max_capacity: document.getElementById('programCapacity').value ? Number(document.getElementById('programCapacity').value) : null,
      description: document.getElementById('programDescription').value.trim() || null,
      eligibility: document.getElementById('programEligibility').value.trim() || null,
      objectives: document.getElementById('programObjectives').value.trim() || null,
    };

    try {
      await ArckAPI.request('/admin/programs', { method: 'POST', body });
      addPanel.style.display = 'none';
      addForm.reset();
      await Promise.all([loadPrograms(), loadStats()]);
    } catch (err) {
      addErrorBox.textContent = err.detail || 'Could not create program.';
      addErrorBox.style.display = 'block';
    } finally {
      addSubmitBtn.disabled = false;
    }
  });

  /* ---------- Edit program ---------- */
  const editPanel = document.getElementById('editProgramPanel');
  const editForm = document.getElementById('editProgramForm');
  const editErrorBox = document.getElementById('editProgramError');
  const editSubmitBtn = document.getElementById('editProgramSubmitBtn');

  const openEditPanel = (program) => {
    addPanel.style.display = 'none';
    document.getElementById('editProgramId').value = program.id;
    document.getElementById('editProgramName').value = program.name;
    document.getElementById('editProgramCode').value = program.code || '';
    document.getElementById('editProgramCategory').value = program.category || '';
    document.getElementById('editProgramDuration').value = program.duration || '';
    document.getElementById('editProgramMode').value = program.mode;
    document.getElementById('editProgramCapacity').value = program.max_capacity || '';
    document.getElementById('editProgramDescription').value = program.description || '';
    document.getElementById('editProgramEligibility').value = program.eligibility || '';
    document.getElementById('editProgramObjectives').value = program.objectives || '';
    editErrorBox.style.display = 'none';
    editPanel.style.display = 'block';
    editPanel.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  document.getElementById('cancelEditProgramBtn').addEventListener('click', () => { editPanel.style.display = 'none'; });

  editForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    editErrorBox.style.display = 'none';
    editSubmitBtn.disabled = true;
    const id = document.getElementById('editProgramId').value;

    const body = {
      name: document.getElementById('editProgramName').value.trim(),
      code: document.getElementById('editProgramCode').value.trim() || null,
      category: document.getElementById('editProgramCategory').value.trim() || null,
      duration: document.getElementById('editProgramDuration').value.trim() || null,
      mode: document.getElementById('editProgramMode').value,
      max_capacity: document.getElementById('editProgramCapacity').value ? Number(document.getElementById('editProgramCapacity').value) : null,
      description: document.getElementById('editProgramDescription').value.trim() || null,
      eligibility: document.getElementById('editProgramEligibility').value.trim() || null,
      objectives: document.getElementById('editProgramObjectives').value.trim() || null,
    };

    try {
      await ArckAPI.request(`/admin/programs/${id}`, { method: 'PATCH', body });
      editPanel.style.display = 'none';
      await loadPrograms();
    } catch (err) {
      editErrorBox.textContent = err.detail || 'Could not update program.';
      editErrorBox.style.display = 'block';
    } finally {
      editSubmitBtn.disabled = false;
    }
  });

  /* ---------- Card actions: edit / activate-deactivate ---------- */
  grid.addEventListener('click', async (e) => {
    const editBtn = e.target.closest('[data-action="edit"]');
    if (editBtn) {
      const program = programs.find((p) => p.id === Number(editBtn.dataset.id));
      if (program) openEditPanel(program);
      return;
    }

    const toggleBtn = e.target.closest('[data-action="toggle"]');
    if (toggleBtn) {
      const program = programs.find((p) => p.id === Number(toggleBtn.dataset.id));
      if (!program) return;
      const newStatus = program.status === 'active' ? 'inactive' : 'active';
      toggleBtn.disabled = true;
      try {
        await ArckAPI.request(`/admin/programs/${program.id}`, { method: 'PATCH', body: { status: newStatus } });
        await Promise.all([loadPrograms(), loadStats()]);
      } catch (err) {
        window.alert(err.detail || 'Could not update program status.');
        toggleBtn.disabled = false;
      }
    }
  });

  await Promise.all([loadStats(), loadPrograms()]);

});
