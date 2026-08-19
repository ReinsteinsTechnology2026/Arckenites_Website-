const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDate = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const MODE_LABEL = { online: 'Online', offline: 'Offline', hybrid: 'Hybrid' };
const BATCH_STATUS_BADGE = { upcoming: 'is-info', active: 'is-success', paused: 'is-pending', completed: 'is-muted', cancelled: 'is-danger' };
const BATCH_STATUS_LABEL = { upcoming: 'Upcoming', active: 'Active', paused: 'Paused', completed: 'Completed', cancelled: 'Cancelled' };
const ENROLLMENT_STATUS_BADGE = {
  pending: 'is-pending', approved: 'is-info', allocated: 'is-info', active: 'is-success',
  completed: 'is-muted', withdrawn: 'is-muted', rejected: 'is-danger',
};
const ENROLLMENT_STATUS_LABEL = {
  pending: 'Pending', approved: 'Approved', allocated: 'Allocated', active: 'Active',
  completed: 'Completed', withdrawn: 'Withdrawn', rejected: 'Rejected',
};
const ENROLLMENT_STATUSES = ['pending', 'approved', 'allocated', 'active', 'completed', 'withdrawn', 'rejected'];

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('admin');
  if (!user) return; // requireRole already redirected

  const programId = Number(new URLSearchParams(window.location.search).get('id'));

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

  if (!programId) {
    document.getElementById('programDetailName').textContent = 'Program not found';
    document.getElementById('programNotFound').style.display = 'block';
    return;
  }

  let program = null;
  let enrollmentFilter = { search: '', status: '' };

  /* ---------- Render ---------- */
  const renderProgram = () => {
    document.getElementById('programDetailName').textContent = program.name;
    document.getElementById('programBreadcrumbName').textContent = program.name;
    document.getElementById('createBatchLink').href = `admin-batches.html?program_id=${program.id}`;

    document.getElementById('infoStatus').textContent = program.status === 'active' ? 'Active' : 'Inactive';
    document.getElementById('infoTotalEnrolled').textContent = program.enrollment_stats.total;
    document.getElementById('infoActiveBatches').textContent = program.batch_stats.active;
    document.getElementById('infoUpcomingBatches').textContent = program.batch_stats.upcoming;

    document.getElementById('toggleStatusLabel').textContent = program.status === 'active' ? 'Deactivate' : 'Activate';

    document.getElementById('detailCode').textContent = program.code || '—';
    document.getElementById('detailCategory').textContent = program.category || '—';
    document.getElementById('detailDuration').textContent = program.duration || '—';
    document.getElementById('detailMode').textContent = MODE_LABEL[program.mode];
    document.getElementById('detailCapacity').textContent = program.max_capacity || 'No limit set';
    document.getElementById('detailCreated').textContent = formatDate(program.created_at);
    document.getElementById('detailDescription').textContent = program.description || 'No description yet.';
    document.getElementById('detailEligibility').textContent = program.eligibility || 'Not specified.';
    document.getElementById('detailObjectives').textContent = program.objectives || 'Not specified.';

    renderBatches();
    renderEnrollments();
  };

  const renderBatches = () => {
    const tbody = document.getElementById('programBatchesTableBody');
    tbody.innerHTML = program.batches.length
      ? program.batches.map((b) => `
          <tr>
            <td><a href="admin-batch-detail.html?id=${b.id}">${escapeHtml(b.name)}</a></td>
            <td>${b.trainer_name ? escapeHtml(b.trainer_name) : '<span class="admin-activity-badge is-pending">Unassigned</span>'}</td>
            <td>${b.student_count}</td>
            <td><span class="admin-activity-badge ${BATCH_STATUS_BADGE[b.status]}">${BATCH_STATUS_LABEL[b.status]}</span></td>
            <td><a class="table-action-btn" href="admin-batch-detail.html?id=${b.id}" title="View ${escapeHtml(b.name)}"><i class="fa-solid fa-eye"></i></a></td>
          </tr>
        `).join('')
      : '<tr><td colspan="5" class="admin-panel-empty">No batches under this program yet.</td></tr>';
  };

  const filteredEnrollments = () => program.enrollments.filter((e) => {
    if (enrollmentFilter.status && e.status !== enrollmentFilter.status) return false;
    if (enrollmentFilter.search) {
      const q = enrollmentFilter.search.toLowerCase();
      if (!e.student_name.toLowerCase().includes(q) && !e.student_username.toLowerCase().includes(q)) return false;
    }
    return true;
  });

  const batchOptionsHtml = (currentBatchId) => {
    const opts = [`<option value="" ${!currentBatchId ? 'selected' : ''}>Not allocated</option>`];
    program.batches.forEach((b) => {
      opts.push(`<option value="${b.id}" ${b.id === currentBatchId ? 'selected' : ''}>${escapeHtml(b.name)}</option>`);
    });
    return opts.join('');
  };

  const renderEnrollments = () => {
    const tbody = document.getElementById('enrollmentsTableBody');
    const list = filteredEnrollments();
    tbody.innerHTML = list.length
      ? list.map((e) => `
          <tr data-enrollment-id="${e.id}">
            <td>${escapeHtml(e.student_name)}<br><span style="color:var(--muted); font-size:.78rem;">@${escapeHtml(e.student_username)}</span></td>
            <td>${formatDate(e.enrolled_at)}</td>
            <td>
              <select class="form-select" data-status-select="${e.id}" style="min-width:130px;">
                ${ENROLLMENT_STATUSES.map((s) => `<option value="${s}" ${s === e.status ? 'selected' : ''}>${ENROLLMENT_STATUS_LABEL[s]}</option>`).join('')}
              </select>
            </td>
            <td>
              <select class="form-select" data-batch-select="${e.id}" data-student-id="${e.student_id}" style="min-width:150px;" ${program.batches.length === 0 ? 'disabled' : ''}>
                ${batchOptionsHtml(e.current_batch ? e.current_batch.id : null)}
              </select>
            </td>
            <td>
              <button type="button" class="table-action-btn is-danger" data-remove-enrollment="${e.id}" title="Remove ${escapeHtml(e.student_name)} from this program">
                <i class="fa-solid fa-user-minus"></i>
              </button>
            </td>
          </tr>
        `).join('')
      : '<tr><td colspan="5" class="admin-panel-empty">No enrollments match.</td></tr>';
  };

  const loadProgram = async () => {
    try {
      program = await ArckAPI.request(`/admin/programs/${programId}`);
      document.getElementById('programDetailContent').style.display = 'block';
      renderProgram();
    } catch (err) {
      if (err.status === 0) { showServerBanner(); return; }
      document.getElementById('programDetailName').textContent = 'Program not found';
      document.getElementById('programNotFound').style.display = 'block';
    }
  };

  /* ---------- Toggle status ---------- */
  document.getElementById('toggleStatusBtn').addEventListener('click', async () => {
    const newStatus = program.status === 'active' ? 'inactive' : 'active';
    try {
      program = await ArckAPI.request(`/admin/programs/${programId}`, { method: 'PATCH', body: { status: newStatus } });
      renderProgram();
    } catch (err) {
      window.alert(err.detail || 'Could not update program status.');
    }
  });

  /* ---------- Edit program ---------- */
  const editPanel = document.getElementById('editProgramPanel');
  const editForm = document.getElementById('editProgramForm');
  const editErrorBox = document.getElementById('editProgramError');
  const editSubmitBtn = document.getElementById('editProgramSubmitBtn');

  document.getElementById('editProgramBtn').addEventListener('click', () => {
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
  });
  document.getElementById('cancelEditProgramBtn').addEventListener('click', () => { editPanel.style.display = 'none'; });

  editForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    editErrorBox.style.display = 'none';
    editSubmitBtn.disabled = true;

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
      program = await ArckAPI.request(`/admin/programs/${programId}`, { method: 'PATCH', body });
      renderProgram();
      editPanel.style.display = 'none';
    } catch (err) {
      editErrorBox.textContent = err.detail || 'Could not update program.';
      editErrorBox.style.display = 'block';
    } finally {
      editSubmitBtn.disabled = false;
    }
  });

  /* ---------- Enroll students panel ---------- */
  const enrollPanel = document.getElementById('enrollStudentsPanel');
  const enrollSearchInput = document.getElementById('enrollStudentSearch');
  const enrollResultsEl = document.getElementById('enrollStudentResults');
  const enrollSelectedEl = document.getElementById('enrollStudentSelected');
  const enrollErrorBox = document.getElementById('enrollStudentsError');
  let enrollSearchDebounce = null;
  const pendingEnrollStudents = new Map();

  const renderPendingEnroll = () => {
    enrollSelectedEl.innerHTML = [...pendingEnrollStudents.values()].map((s) => `
      <div class="batch-student-row">
        <span>${escapeHtml(s.full_name)} &middot; @${escapeHtml(s.username)}</span>
        <button type="button" class="btn btn-primary-outline is-danger" data-unpick="${s.id}">Remove</button>
      </div>
    `).join('');
  };

  enrollSelectedEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-unpick]');
    if (!btn) return;
    pendingEnrollStudents.delete(Number(btn.dataset.unpick));
    renderPendingEnroll();
  });

  const closeEnrollPanel = () => {
    enrollPanel.style.display = 'none';
    enrollSearchInput.value = '';
    enrollResultsEl.innerHTML = '';
    pendingEnrollStudents.clear();
    renderPendingEnroll();
    enrollErrorBox.style.display = 'none';
  };

  document.getElementById('toggleEnrollBtn').addEventListener('click', () => {
    const isHidden = enrollPanel.style.display === 'none';
    enrollPanel.style.display = isHidden ? 'block' : 'none';
    if (isHidden) enrollSearchInput.focus();
  });
  document.getElementById('cancelEnrollStudentsBtn').addEventListener('click', closeEnrollPanel);

  enrollSearchInput.addEventListener('input', () => {
    const q = enrollSearchInput.value.trim();
    clearTimeout(enrollSearchDebounce);
    if (!q) { enrollResultsEl.innerHTML = ''; return; }
    enrollSearchDebounce = setTimeout(async () => {
      try {
        const results = await ArckAPI.request(`/admin/programs/lookup/available-students?q=${encodeURIComponent(q)}`);
        const currentIds = new Set(program.enrollments.map((e) => e.student_id));
        enrollResultsEl.innerHTML = results.length
          ? results.map((s) => {
              const already = currentIds.has(s.id);
              const picked = pendingEnrollStudents.has(s.id);
              return `
                <div class="batch-student-row">
                  <span>${escapeHtml(s.full_name)} &middot; @${escapeHtml(s.username)}</span>
                  <button type="button" class="btn btn-accent" data-pick='${JSON.stringify(s)}' ${(already || picked) ? 'disabled' : ''}>
                    ${already ? 'Already enrolled' : picked ? 'Added' : 'Add'}
                  </button>
                </div>
              `;
            }).join('')
          : '<div class="batch-student-row">No matching students.</div>';
      } catch (_) {
        enrollResultsEl.innerHTML = '<div class="batch-student-row">Search failed.</div>';
      }
    }, 300);
  });

  enrollResultsEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-pick]');
    if (!btn) return;
    const s = JSON.parse(btn.dataset.pick);
    pendingEnrollStudents.set(s.id, s);
    renderPendingEnroll();
    btn.textContent = 'Added';
    btn.disabled = true;
  });

  document.getElementById('enrollStudentsSubmitBtn').addEventListener('click', async () => {
    if (pendingEnrollStudents.size === 0) {
      enrollErrorBox.textContent = 'Pick at least one student first.';
      enrollErrorBox.style.display = 'block';
      return;
    }
    enrollErrorBox.style.display = 'none';
    try {
      program = await ArckAPI.request(`/admin/programs/${programId}/enrollments`, {
        method: 'POST',
        body: { student_ids: [...pendingEnrollStudents.keys()] },
      });
      renderProgram();
      closeEnrollPanel();
    } catch (err) {
      enrollErrorBox.textContent = err.detail || 'Could not enroll students.';
      enrollErrorBox.style.display = 'block';
    }
  });

  /* ---------- Enrollment filters ---------- */
  document.getElementById('enrollmentSearchInput').addEventListener('input', (e) => {
    enrollmentFilter.search = e.target.value.trim();
    renderEnrollments();
  });
  document.getElementById('enrollmentStatusFilter').addEventListener('change', (e) => {
    enrollmentFilter.status = e.target.value;
    renderEnrollments();
  });

  /* ---------- Enrollment row actions: status change / batch allocate-transfer / remove ---------- */
  document.getElementById('enrollmentsTableBody').addEventListener('change', async (e) => {
    const statusSelect = e.target.closest('[data-status-select]');
    if (statusSelect) {
      const enrollmentId = Number(statusSelect.dataset.statusSelect);
      statusSelect.disabled = true;
      try {
        program = await ArckAPI.request(`/admin/programs/${programId}/enrollments/${enrollmentId}`, {
          method: 'PATCH', body: { status: statusSelect.value },
        });
        renderProgram();
      } catch (err) {
        window.alert(err.detail || 'Could not update enrollment status.');
        statusSelect.disabled = false;
      }
      return;
    }

    const batchSelect = e.target.closest('[data-batch-select]');
    if (batchSelect) {
      const studentId = Number(batchSelect.dataset.studentId);
      const newBatchId = batchSelect.value ? Number(batchSelect.value) : null;
      const enrollment = program.enrollments.find((en) => en.student_id === studentId);
      const oldBatchId = enrollment?.current_batch ? enrollment.current_batch.id : null;
      if (newBatchId === oldBatchId) return;

      batchSelect.disabled = true;
      try {
        if (oldBatchId) {
          await ArckAPI.request(`/admin/batches/${oldBatchId}/students/${studentId}`, { method: 'DELETE' });
        }
        if (newBatchId) {
          await ArckAPI.request(`/admin/batches/${newBatchId}/students`, { method: 'POST', body: { student_ids: [studentId] } });
        }
        program = await ArckAPI.request(`/admin/programs/${programId}`);
        renderProgram();
      } catch (err) {
        window.alert(err.detail || 'Could not update batch allocation.');
        batchSelect.disabled = false;
      }
    }
  });

  document.getElementById('enrollmentsTableBody').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-remove-enrollment]');
    if (!btn) return;
    const enrollmentId = Number(btn.dataset.removeEnrollment);
    const enrollment = program.enrollments.find((en) => en.id === enrollmentId);
    if (!enrollment) return;
    if (!window.confirm(`Remove ${enrollment.student_name} from "${program.name}"? This does not remove them from any batch.`)) return;

    btn.disabled = true;
    try {
      program = await ArckAPI.request(`/admin/programs/${programId}/enrollments/${enrollmentId}`, { method: 'DELETE' });
      renderProgram();
    } catch (err) {
      window.alert(err.detail || 'Could not remove enrollment.');
      btn.disabled = false;
    }
  });

  await loadProgram();

});
