const BATCH_STATUS_BADGE = { upcoming: 'is-info', active: 'is-success', paused: 'is-pending', completed: 'is-muted', cancelled: 'is-danger' };
const BATCH_STATUS_LABEL = { upcoming: 'Upcoming', active: 'Active', paused: 'Paused', completed: 'Completed', cancelled: 'Cancelled' };
const BATCH_TYPE_LABEL = { online: 'Online', offline: 'Offline', hybrid: 'Hybrid' };

const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDate = (iso) => iso ? new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const formatTime = (t) => t ? new Date(`1970-01-01T${t}`).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('admin');
  if (!user) return; // requireRole already redirected

  const batchId = Number(new URLSearchParams(window.location.search).get('id'));

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

  if (!batchId) {
    document.getElementById('batchDetailName').textContent = 'Batch not found';
    document.getElementById('batchNotFound').style.display = 'block';
    return;
  }

  /* ---------- Trainers dropdown (for edit form) ---------- */
  const editTrainerSelect = document.getElementById('editBatchTrainer');
  const loadTrainers = async () => {
    try {
      const trainers = await ArckAPI.request('/admin/staff');
      trainers.forEach((t) => {
        const opt = document.createElement('option');
        opt.value = t.id;
        opt.textContent = t.full_name;
        editTrainerSelect.appendChild(opt);
      });
    } catch (_) { /* dropdown just stays at "No trainer assigned" */ }
  };

  /* ---------- Programs dropdown (for edit form) ---------- */
  const editProgramSelect = document.getElementById('editBatchProgram');
  const loadPrograms = async () => {
    try {
      const programs = await ArckAPI.request('/admin/programs');
      programs.forEach((p) => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        editProgramSelect.appendChild(opt);
      });
    } catch (_) { /* dropdown just stays at "No program linked" */ }
  };

  let batch = null;

  /* ---------- Render batch info ---------- */
  const renderBatch = () => {
    document.getElementById('batchDetailName').textContent = batch.name;
    document.getElementById('batchBreadcrumbName').textContent = batch.name;

    document.getElementById('infoCourse').textContent = batch.course || '—';
    document.getElementById('infoStatus').textContent = BATCH_STATUS_LABEL[batch.status];
    document.getElementById('infoStudentCount').textContent = batch.max_capacity ? `${batch.student_count} / ${batch.max_capacity}` : batch.student_count;
    document.getElementById('infoTrainer').innerHTML = batch.trainer
      ? `<span class="ak-identity">${ArckAPI.avatarHtml(batch.trainer.full_name, batch.trainer.photo_url, 28)}<span>${escapeHtml(batch.trainer.full_name)}</span></span>`
      : 'Unassigned';

    document.getElementById('detailProgram').innerHTML = batch.program
      ? `<a href="admin-program-detail.html?id=${batch.program.id}">${escapeHtml(batch.program.name)}</a>` : 'Not linked';
    document.getElementById('detailType').textContent = BATCH_TYPE_LABEL[batch.batch_type];
    document.getElementById('detailStartDate').textContent = formatDate(batch.start_date);
    document.getElementById('detailCapacity').textContent = batch.max_capacity || 'No limit set';

    const tbody = document.getElementById('batchStudentsTableBody');
    tbody.innerHTML = batch.students.length
      ? batch.students.map((s) => `
          <tr>
            <td>${escapeHtml(s.username)}</td>
            <td><span class="ak-identity">${ArckAPI.avatarHtml(s.full_name, s.photo_url, 28)}<span>${escapeHtml(s.full_name)}</span></span></td>
            <td>${s.joined_at ? formatDate(s.joined_at.slice(0, 10)) : '—'}</td>
            <td>
              <button type="button" class="table-action-btn is-danger" data-remove-student="${s.id}" title="Remove ${escapeHtml(s.full_name)} from this batch">
                <i class="fa-solid fa-user-minus"></i>
              </button>
            </td>
          </tr>
        `).join('')
      : '<tr><td colspan="4" class="admin-panel-empty">No students allocated yet.</td></tr>';
  };

  const loadBatch = async () => {
    try {
      batch = await ArckAPI.request(`/admin/batches/${batchId}`);
      document.getElementById('batchDetailContent').style.display = 'block';
      renderBatch();
    } catch (err) {
      if (err.status === 0) { showServerBanner(); return; }
      document.getElementById('batchDetailName').textContent = 'Batch not found';
      document.getElementById('batchNotFound').style.display = 'block';
    }
  };

  /* ---------- Remove student ---------- */
  document.getElementById('batchStudentsTableBody').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-remove-student]');
    if (!btn) return;
    const studentId = Number(btn.dataset.removeStudent);
    const student = batch.students.find((s) => s.id === studentId);
    if (!student) return;
    if (!window.confirm(`Remove ${student.full_name} from "${batch.name}"?`)) return;

    btn.disabled = true;
    try {
      batch = await ArckAPI.request(`/admin/batches/${batchId}/students/${studentId}`, { method: 'DELETE' });
      renderBatch();
    } catch (err) {
      window.alert(err.detail || 'Could not remove student.');
      btn.disabled = false;
    }
  });

  /* ---------- Edit batch ---------- */
  const editPanel = document.getElementById('editBatchPanel');
  const editForm = document.getElementById('editBatchForm');
  const editErrorBox = document.getElementById('editBatchError');
  const editSubmitBtn = document.getElementById('editBatchSubmitBtn');

  const openEditPanel = () => {
    document.getElementById('editBatchName').value = batch.name;
    document.getElementById('editBatchCourse').value = batch.course || '';
    document.getElementById('editBatchType').value = batch.batch_type;
    document.getElementById('editBatchStatus').value = batch.status;
    editTrainerSelect.value = batch.trainer ? String(batch.trainer.id) : '';
    editProgramSelect.value = batch.program ? String(batch.program.id) : '';
    document.getElementById('editBatchCapacity').value = batch.max_capacity || '';
    document.getElementById('editBatchStartDate').value = batch.start_date || '';

    editErrorBox.style.display = 'none';
    editPanel.style.display = 'block';
    editPanel.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  document.getElementById('joinBatchVideoBtn').addEventListener('click', () => {
    window.location.href = `meeting-room.html?batch=${batchId}`;
  });

  document.getElementById('editBatchBtn').addEventListener('click', openEditPanel);
  document.getElementById('cancelEditBatchBtn').addEventListener('click', () => { editPanel.style.display = 'none'; });

  editForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    editErrorBox.style.display = 'none';
    editSubmitBtn.disabled = true;

    const trainerValue = editTrainerSelect.value;
    const programValue = editProgramSelect.value;
    const body = {
      name: document.getElementById('editBatchName').value.trim(),
      course: document.getElementById('editBatchCourse').value.trim() || null,
      batch_type: document.getElementById('editBatchType').value,
      status: document.getElementById('editBatchStatus').value,
      trainer_id: trainerValue ? Number(trainerValue) : null,
      clear_trainer: !trainerValue,
      program_id: programValue ? Number(programValue) : null,
      clear_program: !programValue,
      max_capacity: document.getElementById('editBatchCapacity').value ? Number(document.getElementById('editBatchCapacity').value) : null,
      start_date: document.getElementById('editBatchStartDate').value || null,
    };

    try {
      batch = await ArckAPI.request(`/admin/batches/${batchId}`, { method: 'PATCH', body });
      renderBatch();
      editPanel.style.display = 'none';
    } catch (err) {
      editErrorBox.textContent = err.detail || 'Could not update batch.';
      editErrorBox.style.display = 'block';
    } finally {
      editSubmitBtn.disabled = false;
    }
  });

  /* ---------- Delete batch ---------- */
  document.getElementById('deleteBatchBtn').addEventListener('click', async () => {
    if (!window.confirm(`Delete "${batch.name}"? This removes the batch and every student's allocation to it. This cannot be undone.`)) return;
    try {
      await ArckAPI.request(`/admin/batches/${batchId}`, { method: 'DELETE' });
      window.location.href = 'admin-batches.html';
    } catch (err) {
      window.alert(err.detail || 'Could not delete batch.');
    }
  });

  /* ---------- Add students panel ---------- */
  const addStudentsPanel = document.getElementById('addStudentsPanel');
  const allocateSearchInput = document.getElementById('allocateStudentSearch');
  const allocateResultsEl = document.getElementById('allocateStudentResults');
  const allocateSelectedEl = document.getElementById('allocateStudentSelected');
  const allocateErrorBox = document.getElementById('allocateStudentsError');
  let allocateSearchDebounce = null;
  const pendingStudents = new Map();

  const renderPendingStudents = () => {
    allocateSelectedEl.innerHTML = [...pendingStudents.values()].map((s) => `
      <div class="batch-student-row">
        <span>${escapeHtml(s.full_name)} &middot; @${escapeHtml(s.username)}</span>
        <button type="button" class="btn btn-primary-outline is-danger" data-unpick="${s.id}">Remove</button>
      </div>
    `).join('');
  };

  allocateSelectedEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-unpick]');
    if (!btn) return;
    pendingStudents.delete(Number(btn.dataset.unpick));
    renderPendingStudents();
  });

  const closeAddStudentsPanel = () => {
    addStudentsPanel.style.display = 'none';
    allocateSearchInput.value = '';
    allocateResultsEl.innerHTML = '';
    pendingStudents.clear();
    renderPendingStudents();
    allocateErrorBox.style.display = 'none';
  };

  document.getElementById('toggleAddStudentsBtn').addEventListener('click', () => {
    const isHidden = addStudentsPanel.style.display === 'none';
    addStudentsPanel.style.display = isHidden ? 'block' : 'none';
    if (isHidden) allocateSearchInput.focus();
  });
  document.getElementById('cancelAddStudentsBtn').addEventListener('click', closeAddStudentsPanel);

  allocateSearchInput.addEventListener('input', () => {
    const q = allocateSearchInput.value.trim();
    clearTimeout(allocateSearchDebounce);
    if (!q) { allocateResultsEl.innerHTML = ''; return; }
    allocateSearchDebounce = setTimeout(async () => {
      try {
        const results = await ArckAPI.request(`/admin/batches/lookup/available-students?q=${encodeURIComponent(q)}`);
        const currentIds = new Set(batch.students.map((s) => s.id));
        allocateResultsEl.innerHTML = results.length
          ? results.map((s) => {
              const already = currentIds.has(s.id);
              const picked = pendingStudents.has(s.id);
              return `
                <div class="batch-student-row">
                  <span>${escapeHtml(s.full_name)} &middot; @${escapeHtml(s.username)}</span>
                  <button type="button" class="btn btn-accent" data-pick='${JSON.stringify(s)}' ${(already || picked) ? 'disabled' : ''}>
                    ${already ? 'Already in batch' : picked ? 'Added' : 'Add'}
                  </button>
                </div>
              `;
            }).join('')
          : '<div class="batch-student-row">No matching students.</div>';
      } catch (_) {
        allocateResultsEl.innerHTML = '<div class="batch-student-row">Search failed.</div>';
      }
    }, 300);
  });

  allocateResultsEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-pick]');
    if (!btn) return;
    const s = JSON.parse(btn.dataset.pick);
    pendingStudents.set(s.id, s);
    renderPendingStudents();
    btn.textContent = 'Added';
    btn.disabled = true;
  });

  document.getElementById('allocateStudentsSubmitBtn').addEventListener('click', async () => {
    if (pendingStudents.size === 0) {
      allocateErrorBox.textContent = 'Pick at least one student first.';
      allocateErrorBox.style.display = 'block';
      return;
    }
    allocateErrorBox.style.display = 'none';
    try {
      batch = await ArckAPI.request(`/admin/batches/${batchId}/students`, {
        method: 'POST',
        body: { student_ids: [...pendingStudents.keys()] },
      });
      renderBatch();
      closeAddStudentsPanel();
    } catch (err) {
      allocateErrorBox.textContent = err.detail || 'Could not add students.';
      allocateErrorBox.style.display = 'block';
    }
  });

  /* ---------- Class sessions ---------- */
  const sessionsTableBody = document.getElementById('sessionsTableBody');
  const addSessionPanel = document.getElementById('addSessionPanel');
  const addSessionForm = document.getElementById('addSessionForm');
  const addSessionErrorBox = document.getElementById('addSessionError');
  const addSessionSubmitBtn = document.getElementById('addSessionSubmitBtn');

  const renderSessions = (sessions) => {
    sessionsTableBody.innerHTML = sessions.length
      ? sessions.map((s) => `
          <tr>
            <td>${escapeHtml(s.title)}</td>
            <td>${formatDate(s.session_date)}</td>
            <td>${(s.start_time && s.end_time) ? `${formatTime(s.start_time)} – ${formatTime(s.end_time)}` : '—'}</td>
            <td>${s.meeting_link ? `<a href="${escapeHtml(s.meeting_link)}" target="_blank" rel="noopener">Join</a>` : '—'}</td>
            <td>${s.notes ? escapeHtml(s.notes) : '—'}</td>
            <td>
              <button type="button" class="table-action-btn is-danger" data-remove-session="${s.id}" title="Delete ${escapeHtml(s.title)}">
                <i class="fa-solid fa-trash"></i>
              </button>
            </td>
          </tr>
        `).join('')
      : '<tr><td colspan="6" class="admin-panel-empty">No class sessions scheduled yet.</td></tr>';
  };

  const loadSessions = async () => {
    try {
      const sessions = await ArckAPI.request(`/admin/batches/${batchId}/sessions`);
      renderSessions(sessions);
    } catch (err) {
      if (err.status === 0) showServerBanner();
      sessionsTableBody.innerHTML = '<tr><td colspan="6" class="admin-panel-empty">Couldn\'t load the class schedule.</td></tr>';
    }
  };

  document.getElementById('toggleAddSessionBtn').addEventListener('click', () => {
    const isHidden = addSessionPanel.style.display === 'none';
    addSessionPanel.style.display = isHidden ? 'block' : 'none';
    if (isHidden) document.getElementById('sessionTitle').focus();
  });
  document.getElementById('cancelAddSessionBtn').addEventListener('click', () => {
    addSessionPanel.style.display = 'none';
    addSessionForm.reset();
    addSessionErrorBox.style.display = 'none';
  });

  addSessionForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    addSessionErrorBox.style.display = 'none';
    addSessionSubmitBtn.disabled = true;

    const body = {
      title: document.getElementById('sessionTitle').value.trim(),
      session_date: document.getElementById('sessionDate').value,
      start_time: document.getElementById('sessionStartTime').value || null,
      end_time: document.getElementById('sessionEndTime').value || null,
      meeting_link: document.getElementById('sessionMeetingLink').value.trim() || null,
      notes: document.getElementById('sessionNotes').value.trim() || null,
    };

    try {
      await ArckAPI.request(`/admin/batches/${batchId}/sessions`, { method: 'POST', body });
      addSessionPanel.style.display = 'none';
      addSessionForm.reset();
      await loadSessions();
    } catch (err) {
      addSessionErrorBox.textContent = err.detail || 'Could not add the class session.';
      addSessionErrorBox.style.display = 'block';
    } finally {
      addSessionSubmitBtn.disabled = false;
    }
  });

  sessionsTableBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-remove-session]');
    if (!btn) return;
    if (!window.confirm('Delete this class session? This cannot be undone.')) return;

    btn.disabled = true;
    try {
      await ArckAPI.request(`/admin/batches/${batchId}/sessions/${btn.dataset.removeSession}`, { method: 'DELETE' });
      await loadSessions();
    } catch (err) {
      window.alert(err.detail || 'Could not delete the class session.');
      btn.disabled = false;
    }
  });

  /* ---------- Lab Access / Class Videos / Study Materials (same shape, generic wiring) ---------- */
  const setupSimpleResourcePanel = ({ endpoint, toggleBtnId, panelId, formId, errorBoxId, submitBtnId, cancelBtnId, tableBodyId, focusFieldId, buildBody, renderRow, colspan, emptyText }) => {
    const panel = document.getElementById(panelId);
    const form = document.getElementById(formId);
    const errorBox = document.getElementById(errorBoxId);
    const submitBtn = document.getElementById(submitBtnId);
    const tableBody = document.getElementById(tableBodyId);

    const load = async () => {
      try {
        const items = await ArckAPI.request(`/admin/batches/${batchId}/${endpoint}`);
        tableBody.innerHTML = items.length
          ? items.map(renderRow).join('')
          : `<tr><td colspan="${colspan}" class="admin-panel-empty">${emptyText}</td></tr>`;
      } catch (err) {
        if (err.status === 0) showServerBanner();
        tableBody.innerHTML = `<tr><td colspan="${colspan}" class="admin-panel-empty">Couldn't load this.</td></tr>`;
      }
    };

    document.getElementById(toggleBtnId).addEventListener('click', () => {
      const isHidden = panel.style.display === 'none';
      panel.style.display = isHidden ? 'block' : 'none';
      if (isHidden) document.getElementById(focusFieldId).focus();
    });
    document.getElementById(cancelBtnId).addEventListener('click', () => {
      panel.style.display = 'none';
      form.reset();
      errorBox.style.display = 'none';
    });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      errorBox.style.display = 'none';
      submitBtn.disabled = true;
      try {
        await ArckAPI.request(`/admin/batches/${batchId}/${endpoint}`, { method: 'POST', body: buildBody() });
        panel.style.display = 'none';
        form.reset();
        await load();
      } catch (err) {
        errorBox.textContent = err.detail || 'Could not save.';
        errorBox.style.display = 'block';
      } finally {
        submitBtn.disabled = false;
      }
    });

    tableBody.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-remove-id]');
      if (!btn) return;
      if (!window.confirm('Delete this entry? This cannot be undone.')) return;
      btn.disabled = true;
      try {
        await ArckAPI.request(`/admin/batches/${batchId}/${endpoint}/${btn.dataset.removeId}`, { method: 'DELETE' });
        await load();
      } catch (err) {
        window.alert(err.detail || 'Could not delete this entry.');
        btn.disabled = false;
      }
    });

    return load;
  };

  const loadLabAccess = setupSimpleResourcePanel({
    endpoint: 'lab-access', toggleBtnId: 'toggleAddLabAccessBtn', panelId: 'addLabAccessPanel',
    formId: 'addLabAccessForm', errorBoxId: 'addLabAccessError', submitBtnId: 'addLabAccessSubmitBtn',
    cancelBtnId: 'cancelAddLabAccessBtn', tableBodyId: 'labAccessTableBody', focusFieldId: 'labTitle',
    colspan: 6, emptyText: 'No lab access posted yet.',
    buildBody: () => ({
      title: document.getElementById('labTitle').value.trim(),
      access_url: document.getElementById('labUrl').value.trim(),
      username: document.getElementById('labUsername').value.trim() || null,
      password: document.getElementById('labPassword').value.trim() || null,
      notes: document.getElementById('labNotes').value.trim() || null,
    }),
    renderRow: (r) => `
      <tr>
        <td>${escapeHtml(r.title)}</td>
        <td><a href="${escapeHtml(r.access_url)}" target="_blank" rel="noopener">Open</a></td>
        <td>${r.username ? escapeHtml(r.username) : '—'}</td>
        <td>${r.password ? escapeHtml(r.password) : '—'}</td>
        <td>${r.notes ? escapeHtml(r.notes) : '—'}</td>
        <td><button type="button" class="table-action-btn is-danger" data-remove-id="${r.id}" title="Delete"><i class="fa-solid fa-trash"></i></button></td>
      </tr>
    `,
  });

  const loadVideos = setupSimpleResourcePanel({
    endpoint: 'videos', toggleBtnId: 'toggleAddVideoBtn', panelId: 'addVideoPanel',
    formId: 'addVideoForm', errorBoxId: 'addVideoError', submitBtnId: 'addVideoSubmitBtn',
    cancelBtnId: 'cancelAddVideoBtn', tableBodyId: 'videosTableBody', focusFieldId: 'videoTitle',
    colspan: 4, emptyText: 'No videos posted yet.',
    buildBody: () => ({
      title: document.getElementById('videoTitle').value.trim(),
      video_url: document.getElementById('videoUrl').value.trim(),
      description: document.getElementById('videoDescription').value.trim() || null,
    }),
    renderRow: (r) => `
      <tr>
        <td>${escapeHtml(r.title)}</td>
        <td><a href="${escapeHtml(r.video_url)}" target="_blank" rel="noopener">Watch</a></td>
        <td>${r.description ? escapeHtml(r.description) : '—'}</td>
        <td><button type="button" class="table-action-btn is-danger" data-remove-id="${r.id}" title="Delete"><i class="fa-solid fa-trash"></i></button></td>
      </tr>
    `,
  });

  const loadMaterials = setupSimpleResourcePanel({
    endpoint: 'materials', toggleBtnId: 'toggleAddMaterialBtn', panelId: 'addMaterialPanel',
    formId: 'addMaterialForm', errorBoxId: 'addMaterialError', submitBtnId: 'addMaterialSubmitBtn',
    cancelBtnId: 'cancelAddMaterialBtn', tableBodyId: 'materialsTableBody', focusFieldId: 'materialTitle',
    colspan: 4, emptyText: 'No study materials posted yet.',
    buildBody: () => ({
      title: document.getElementById('materialTitle').value.trim(),
      file_url: document.getElementById('materialUrl').value.trim(),
      description: document.getElementById('materialDescription').value.trim() || null,
    }),
    renderRow: (r) => `
      <tr>
        <td>${escapeHtml(r.title)}</td>
        <td><a href="${escapeHtml(r.file_url)}" target="_blank" rel="noopener">Open</a></td>
        <td>${r.description ? escapeHtml(r.description) : '—'}</td>
        <td><button type="button" class="table-action-btn is-danger" data-remove-id="${r.id}" title="Delete"><i class="fa-solid fa-trash"></i></button></td>
      </tr>
    `,
  });

  await Promise.all([loadTrainers(), loadPrograms(), loadBatch(), loadSessions(), loadLabAccess(), loadVideos(), loadMaterials()]);

});
