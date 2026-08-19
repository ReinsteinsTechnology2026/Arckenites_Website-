const BATCH_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const BATCH_STATUS_BADGE = { upcoming: 'is-info', active: 'is-success', paused: 'is-pending', completed: 'is-muted', cancelled: 'is-danger' };
const BATCH_STATUS_LABEL = { upcoming: 'Upcoming', active: 'Active', paused: 'Paused', completed: 'Completed', cancelled: 'Cancelled' };
const BATCH_TYPE_LABEL = { online: 'Online', offline: 'Offline', hybrid: 'Hybrid' };

const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const formatDate = (iso) => iso ? new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const formatTime = (t) => t ? new Date(`1970-01-01T${t}`).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';

const scheduleSummary = (batch) => {
  const days = batch.class_days.length ? batch.class_days.join('/') : '';
  const time = (batch.start_time && batch.end_time) ? `${formatTime(batch.start_time)}–${formatTime(batch.end_time)}` : '';
  return [days, time].filter(Boolean).join(' &middot; ') || '&mdash;';
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
      const stats = await ArckAPI.request('/admin/batches/stats');
      document.getElementById('kpiTotalBatches').textContent = stats.total;
      document.getElementById('kpiActiveBatches').textContent = stats.active;
      document.getElementById('kpiUpcomingBatches').textContent = stats.upcoming;
      document.getElementById('kpiCompletedBatches').textContent = stats.completed;
    } catch (err) {
      if (err.status === 0) showServerBanner();
    }
  };

  /* ---------- Trainers dropdown ---------- */
  const trainerSelect = document.getElementById('batchTrainer');
  const loadTrainers = async () => {
    try {
      const trainers = await ArckAPI.request('/admin/staff');
      trainers.forEach((t) => {
        const opt = document.createElement('option');
        opt.value = t.id;
        opt.textContent = t.full_name;
        trainerSelect.appendChild(opt);
      });
    } catch (_) { /* trainer dropdown just stays at "No trainer assigned yet" */ }
  };

  /* ---------- Programs dropdown ---------- */
  const programSelect = document.getElementById('batchProgram');
  const preselectProgramId = new URLSearchParams(window.location.search).get('program_id');
  const loadPrograms = async () => {
    try {
      const programs = await ArckAPI.request('/admin/programs');
      programs.forEach((p) => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        programSelect.appendChild(opt);
      });
      if (preselectProgramId) {
        programSelect.value = preselectProgramId;
        document.getElementById('toggleAddBatchBtn').click();
      }
    } catch (_) { /* program dropdown just stays at "No program linked" */ }
  };

  /* ---------- Class days chips ---------- */
  const selectedDays = new Set();
  const daysRow = document.getElementById('batchDaysRow');
  BATCH_DAYS.forEach((day) => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'batch-day-chip';
    chip.textContent = day;
    chip.addEventListener('click', () => {
      if (selectedDays.has(day)) { selectedDays.delete(day); chip.classList.remove('is-selected'); }
      else { selectedDays.add(day); chip.classList.add('is-selected'); }
    });
    daysRow.appendChild(chip);
  });

  /* ---------- Student allocation picker ---------- */
  const selectedStudents = new Map();
  const studentSearchInput = document.getElementById('batchStudentSearch');
  const studentResultsEl = document.getElementById('batchStudentResults');
  const studentSelectedEl = document.getElementById('batchStudentSelected');
  let studentSearchDebounce = null;

  const renderSelectedStudents = () => {
    studentSelectedEl.innerHTML = [...selectedStudents.values()].map((s) => `
      <div class="batch-student-row">
        <span>${escapeHtml(s.full_name)} &middot; @${escapeHtml(s.username)}</span>
        <button type="button" class="btn btn-primary-outline is-danger" data-remove="${s.id}">Remove</button>
      </div>
    `).join('');
  };

  studentSelectedEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-remove]');
    if (!btn) return;
    selectedStudents.delete(Number(btn.dataset.remove));
    renderSelectedStudents();
  });

  studentSearchInput.addEventListener('input', () => {
    const q = studentSearchInput.value.trim();
    clearTimeout(studentSearchDebounce);
    if (!q) { studentResultsEl.innerHTML = ''; return; }
    studentSearchDebounce = setTimeout(async () => {
      try {
        const results = await ArckAPI.request(`/admin/batches/lookup/available-students?q=${encodeURIComponent(q)}`);
        studentResultsEl.innerHTML = results.length
          ? results.map((s) => `
              <div class="batch-student-row">
                <span>${escapeHtml(s.full_name)} &middot; @${escapeHtml(s.username)}</span>
                <button type="button" class="btn btn-accent" data-add='${JSON.stringify(s)}' ${selectedStudents.has(s.id) ? 'disabled' : ''}>${selectedStudents.has(s.id) ? 'Added' : 'Add'}</button>
              </div>
            `).join('')
          : '<div class="batch-student-row">No matching students.</div>';
      } catch (_) {
        studentResultsEl.innerHTML = '<div class="batch-student-row">Search failed.</div>';
      }
    }, 300);
  });

  studentResultsEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-add]');
    if (!btn) return;
    const s = JSON.parse(btn.dataset.add);
    selectedStudents.set(s.id, s);
    renderSelectedStudents();
    btn.textContent = 'Added';
    btn.disabled = true;
  });

  /* ---------- Add-batch panel toggle ---------- */
  const addPanel = document.getElementById('addBatchPanel');
  const toggleBtn = document.getElementById('toggleAddBatchBtn');
  const cancelBtn = document.getElementById('cancelAddBatchBtn');
  const form = document.getElementById('addBatchForm');
  const errorBox = document.getElementById('addBatchError');
  const submitBtn = document.getElementById('addBatchSubmitBtn');

  const resetForm = () => {
    form.reset();
    selectedDays.clear();
    daysRow.querySelectorAll('.batch-day-chip').forEach((c) => c.classList.remove('is-selected'));
    selectedStudents.clear();
    renderSelectedStudents();
    studentResultsEl.innerHTML = '';
    errorBox.style.display = 'none';
  };

  toggleBtn.addEventListener('click', () => {
    const isHidden = addPanel.style.display === 'none';
    addPanel.style.display = isHidden ? 'block' : 'none';
    if (isHidden) document.getElementById('batchName').focus();
  });
  cancelBtn.addEventListener('click', () => { addPanel.style.display = 'none'; resetForm(); });

  /* ---------- Table rendering ---------- */
  const tbody = document.getElementById('batchesTableBody');

  const rowHtml = (batch) => `
    <tr>
      <td><a href="admin-batch-detail.html?id=${batch.id}">${escapeHtml(batch.name)}</a></td>
      <td>${batch.program ? escapeHtml(batch.program.name) : '&mdash;'}</td>
      <td>${escapeHtml(batch.course) || '&mdash;'}</td>
      <td>${batch.trainer ? escapeHtml(batch.trainer.full_name) : '<span class="admin-activity-badge is-pending">Unassigned</span>'}</td>
      <td>${batch.student_count}${batch.max_capacity ? ` / ${batch.max_capacity}` : ''}</td>
      <td>${formatDate(batch.start_date)}</td>
      <td>${formatDate(batch.end_date)}</td>
      <td>${scheduleSummary(batch)}</td>
      <td><span class="admin-activity-badge ${BATCH_STATUS_BADGE[batch.status]}">${BATCH_STATUS_LABEL[batch.status]}</span></td>
      <td>
        <div class="admin-row-actions">
          <a class="table-action-btn" href="admin-batch-detail.html?id=${batch.id}" title="View ${escapeHtml(batch.name)}"><i class="fa-solid fa-eye"></i></a>
          <button type="button" class="table-action-btn is-danger" data-action="delete" data-id="${batch.id}" title="Delete ${escapeHtml(batch.name)}"><i class="fa-solid fa-trash"></i></button>
        </div>
      </td>
    </tr>
  `;

  let batches = [];
  const renderBatches = () => {
    tbody.innerHTML = batches.length
      ? batches.map(rowHtml).join('')
      : '<tr><td colspan="10" class="admin-panel-empty">No batches yet. Click "Create New Batch" to add the first one.</td></tr>';
  };

  const loadBatches = async () => {
    try {
      batches = await ArckAPI.request('/admin/batches');
      renderBatches();
    } catch (err) {
      if (err.status === 0) showServerBanner();
      tbody.innerHTML = '<tr><td colspan="10" class="admin-panel-empty">Couldn\'t load batches.</td></tr>';
    }
  };

  tbody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action="delete"]');
    if (!btn) return;
    const batch = batches.find((b) => b.id === Number(btn.dataset.id));
    if (!batch) return;
    const confirmed = window.confirm(`Delete "${batch.name}"? This removes the batch and every student's allocation to it. This cannot be undone.`);
    if (!confirmed) return;

    btn.disabled = true;
    try {
      await ArckAPI.request(`/admin/batches/${batch.id}`, { method: 'DELETE' });
      batches = batches.filter((b) => b.id !== batch.id);
      renderBatches();
      loadStats();
    } catch (err) {
      window.alert(err.detail || 'Could not delete batch.');
      btn.disabled = false;
    }
  });

  /* ---------- Create batch ---------- */
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';
    submitBtn.disabled = true;

    const body = {
      name: document.getElementById('batchName').value.trim(),
      course: document.getElementById('batchCourse').value.trim() || null,
      batch_type: document.getElementById('batchType').value,
      status: document.getElementById('batchStatus').value,
      trainer_id: trainerSelect.value ? Number(trainerSelect.value) : null,
      program_id: programSelect.value ? Number(programSelect.value) : null,
      max_capacity: document.getElementById('batchCapacity').value ? Number(document.getElementById('batchCapacity').value) : null,
      start_date: document.getElementById('batchStartDate').value || null,
      end_date: document.getElementById('batchEndDate').value || null,
      start_time: document.getElementById('batchStartTime').value || null,
      end_time: document.getElementById('batchEndTime').value || null,
      class_days: [...selectedDays],
      student_ids: [...selectedStudents.keys()],
    };

    try {
      await ArckAPI.request('/admin/batches', { method: 'POST', body });
      addPanel.style.display = 'none';
      resetForm();
      await Promise.all([loadBatches(), loadStats()]);
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not create batch.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

  await Promise.all([loadStats(), loadTrainers(), loadPrograms(), loadBatches()]);

});
