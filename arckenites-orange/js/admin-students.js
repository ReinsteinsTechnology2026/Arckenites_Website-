const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* Keep in sync with PROGRAM_LABELS in backend/app/models/student.py */
const PROGRAM_LABELS = {
  official_certification: 'Official Certification Program',
  corporate_training: 'Corporate Training Program',
  institutional: 'Institutional Program',
  placement_training: 'Placement Training Program',
  trainers_program: 'Arckenites Trainers Program',
  internship: 'Internship Program',
  interview_crack: "Interview 'n' Crack Program",
  job_assist: 'Arckenites Job Assist Program',
  college_projects: 'College Projects Program',
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
  const exportBtn = document.getElementById('exportStudentsBtn');
  exportBtn.addEventListener('click', async () => {
    exportBtn.disabled = true;
    try {
      const res = await fetch(`${API_BASE}/admin/students/export`, {
        headers: { Authorization: `Bearer ${ArckAPI.getToken()}` },
      });
      if (!res.ok) throw new Error('Export failed');

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `arckenites-students-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (_) {
      window.alert('Could not export students. Check that the server is running and try again.');
    } finally {
      exportBtn.disabled = false;
    }
  });

  /* ---------- Add-student panel toggle ---------- */
  const addPanel = document.getElementById('addStudentPanel');
  const toggleBtn = document.getElementById('toggleAddStudentBtn');
  toggleBtn.disabled = true;
  if (!ArckAuth.hasPermission('students.create')) toggleBtn.style.display = 'none';
  const cancelBtn = document.getElementById('cancelAddStudentBtn');
  const form = document.getElementById('addStudentForm');
  const nameInput = document.getElementById('newStudentName');
  const emailInput = document.getElementById('newStudentEmail');
  const passwordInput = document.getElementById('newStudentPassword');
  const newProgramSelect = document.getElementById('newStudentProgram');
  const errorBox = document.getElementById('addStudentError');

  let programOptions = [];
  const populateProgramSelect = (select) => {
    programOptions.forEach((program) => {
      const opt = document.createElement('option');
      opt.value = program.id;
      opt.textContent = program.name;
      select.appendChild(opt);
    });
  };
  if (ArckAuth.hasPermission('students.create') || ArckAuth.hasPermission('students.edit')) {
    try {
      programOptions = await ArckAPI.request('/admin/students/program-options');
      populateProgramSelect(newProgramSelect);
    } catch (_) {
      errorBox.textContent = 'Could not load programs. Refresh the page before creating a student.';
      errorBox.style.display = 'block';
    }
  }
  const submitBtn = document.getElementById('addStudentSubmitBtn');
  const notice = document.getElementById('newStudentNotice');

  const openAddPanel = () => { closeEditPanel(); closeResendPanel(); addPanel.style.display = 'block'; nameInput.focus(); };
  const closeAddPanel = () => {
    addPanel.style.display = 'none';
    form.reset();
    errorBox.style.display = 'none';
  };

  /* ---------- Table rendering ---------- */
  const tbody = document.getElementById('studentsTableBody');
  const serverBanner = document.getElementById('adminServerBanner');
  const showServerBanner = () => serverBanner.classList.add('is-visible');

  // Same page, two sidebar entries: "Students" (Management) shows the plain
  // list; "Student Database" (Database) links here with ?view=database and
  // additionally shows Mobile/Login ID — see the .col-database CSS rule.
  const isDatabaseView = new URLSearchParams(location.search).get('view') === 'database';
  document.body.classList.toggle('is-database-view', isDatabaseView);
  const tableColspan = isDatabaseView ? 8 : 6;
  const panelTitle = document.getElementById('studentsPanelTitle');
  if (isDatabaseView && panelTitle) panelTitle.textContent = 'Student Database';

  const statusBadge = (student) => {
    if (!student.is_active) return '<span class="admin-activity-badge is-danger">Inactive</span>';
    if (student.must_change_password) return '<span class="admin-activity-badge is-info">Awaiting First Login</span>';
    return '<span class="admin-activity-badge is-success">Active</span>';
  };

  const programBadge = (student) => (student.program_name || student.program)
    ? `<span class="admin-activity-badge is-muted">${escapeHtml(student.program_name || PROGRAM_LABELS[student.program] || student.program)}</span>`
    : '<span class="admin-activity-badge is-pending">Not selected yet</span>';

  const canDeleteStudents = ArckAuth.hasPermission('students.delete');
  const canCreateStudents = ArckAuth.hasPermission('students.create');
  const canEditStudents = ArckAuth.hasPermission('students.edit');
  const canResend = (student) => canCreateStudents && student.is_active && student.must_change_password
    && student.email && student.username === student.email.trim().toLowerCase();

  const rowHtml = (student) => `
    <tr>
      <td>${escapeHtml(student.full_name)}</td>
      <td>${escapeHtml(student.email || student.username)}</td>
      <td class="col-database">${student.phone ? escapeHtml(student.phone) : '—'}</td>
      <td class="col-database">${escapeHtml(student.username)}</td>
      <td>${programBadge(student)}</td>
      <td>${statusBadge(student)}</td>
      <td title="${new Date(student.created_at).toLocaleString()}">${new Date(student.created_at).toLocaleDateString()}</td>
      <td>
        <div class="admin-row-actions">
          ${canEditStudents ? `<button type="button" class="table-action-btn" data-action="edit" data-id="${student.id}" title="Edit ${escapeHtml(student.full_name)}"><i class="fa-solid fa-pen"></i></button>` : ''}
          ${canResend(student) ? `<button type="button" class="table-action-btn" data-action="resend" data-id="${student.id}" title="Resend Credentials" aria-label="Resend Credentials"><i class="fa-solid fa-envelope"></i></button>` : ''}
          ${canDeleteStudents ? `<button type="button" class="table-action-btn is-danger" data-action="delete" data-id="${student.id}" title="Delete ${escapeHtml(student.full_name)}"><i class="fa-solid fa-trash"></i></button>` : ''}
        </div>
      </td>
    </tr>
  `;

  const renderStudents = (list) => {
    tbody.innerHTML = list.length
      ? list.map(rowHtml).join('')
      : `<tr><td colspan="${tableColspan}" class="admin-panel-empty">No students yet. Click "Add New Student" to create the first one.</td></tr>`;
  };

  const loadStudents = async () => {
    try {
      const list = await ArckAPI.request('/admin/students');
      renderStudents(list);
      return list;
    } catch (err) {
      if (err.status === 0) showServerBanner();
      tbody.innerHTML = `<tr><td colspan="${tableColspan}" class="admin-panel-empty">Couldn't load students.</td></tr>`;
      return [];
    }
  };

  let students = await loadStudents();

  /* ---------- Create student ---------- */
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';
    notice.style.display = 'none';
    emailInput.value = emailInput.value.trim().toLowerCase();
    const validationError = !nameInput.value.trim() ? 'Full name is required.'
      : !emailInput.value || !emailInput.validity.valid ? 'Please enter a valid email address.'
      : !passwordInput.value ? 'Temporary password is required.'
      : passwordInput.value.length < 8 ? 'Temporary password must contain at least 8 characters.'
      : new TextEncoder().encode(passwordInput.value).length > 72 ? 'Temporary password must be at most 72 UTF-8 bytes.'
      : !newProgramSelect.value ? 'Please select a program.' : '';
    if (validationError) {
      errorBox.textContent = validationError;
      errorBox.style.display = 'block';
      return;
    }
    submitBtn.disabled = true;

    try {
      const created = await ArckAPI.request('/admin/students', {
        method: 'POST',
        timeoutMs: 60000,
        body: { full_name: nameInput.value.trim(), email: emailInput.value, temp_password: passwordInput.value, program_id: Number(newProgramSelect.value) },
      });
      students = [created, ...students];
      renderStudents(students);
      closeAddPanel();

      notice.textContent = created.detail;
      notice.style.display = 'flex';
    } catch (err) {
      errorBox.textContent = typeof err.detail === 'string' ? err.detail : 'Unable to create student. Please try again.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

  /* ---------- Edit student ---------- */
  const editPanel = document.getElementById('editStudentPanel');
  const editForm = document.getElementById('editStudentForm');
  const editIdInput = document.getElementById('editStudentId');
  const editNameInput = document.getElementById('editStudentName');
  const editLoginInput = document.getElementById('editStudentLogin');
  const editProgramSelect = document.getElementById('editStudentProgram');
  const editActiveSelect = document.getElementById('editStudentActive');
  const editResetPasswordInput = document.getElementById('editStudentResetPassword');
  const editErrorBox = document.getElementById('editStudentError');
  const editSubmitBtn = document.getElementById('editStudentSubmitBtn');
  const cancelEditBtn = document.getElementById('cancelEditStudentBtn');

  let editOriginalProgramId = '';

  const closeEditPanel = () => {
    editPanel.style.display = 'none';
    editForm.reset();
    editErrorBox.style.display = 'none';
  };
  const openEditPanel = (student) => {
    closeAddPanel();
    closeResendPanel();
    editIdInput.value = student.id;
    editNameInput.value = student.full_name;
    editLoginInput.value = student.username;
    editProgramSelect.replaceChildren(new Option('Keep current program', ''));
    populateProgramSelect(editProgramSelect);
    const existing = programOptions.find((p) => p.id === student.program_id || (!student.program_id && p.slug === student.program));
    if (existing) editProgramSelect.value = existing.id;
    else if (student.program_id) {
      editProgramSelect.add(new Option(student.program_name || 'Current program (inactive)', student.program_id));
      editProgramSelect.value = student.program_id;
    }
    editOriginalProgramId = editProgramSelect.value;
    editActiveSelect.value = String(student.is_active);
    editResetPasswordInput.value = '';
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
      is_active: editActiveSelect.value === 'true',
    };
    if (editProgramSelect.value && editProgramSelect.value !== editOriginalProgramId) body.program_id = Number(editProgramSelect.value);
    if (editResetPasswordInput.value) body.reset_temp_password = editResetPasswordInput.value;

    try {
      const updated = await ArckAPI.request(`/admin/students/${editIdInput.value}`, { method: 'PATCH', body });
      students = students.map((s) => (s.id === updated.id ? updated : s));
      renderStudents(students);
      closeEditPanel();
    } catch (err) {
      editErrorBox.textContent = err.detail || 'Could not update student.';
      editErrorBox.style.display = 'block';
    } finally {
      editSubmitBtn.disabled = false;
    }
  });

  /* ---------- Resend without storing or resetting the password ---------- */
  const resendPanel = document.getElementById('resendStudentPanel');
  const resendForm = document.getElementById('resendStudentForm');
  const resendPassword = document.getElementById('resendStudentPassword');
  const resendError = document.getElementById('resendStudentError');
  const resendSubmit = document.getElementById('resendStudentSubmitBtn');
  let resendStudentId = null;
  function closeResendPanel() {
    resendPanel.style.display = 'none';
    resendForm.reset();
    resendStudentId = null;
    resendError.style.display = 'none';
  }
  document.getElementById('cancelResendStudentBtn').addEventListener('click', closeResendPanel);
  resendForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    resendError.style.display = 'none';
    if (!resendPassword.value || resendPassword.value.length < 8) {
      resendError.textContent = 'Enter the original temporary password (at least 8 characters).';
      resendError.style.display = 'block';
      return;
    }
    resendSubmit.disabled = true;
    try {
      const result = await ArckAPI.request(`/admin/students/${resendStudentId}/credentials-email`, {
        method: 'POST', timeoutMs: 60000, body: { temp_password: resendPassword.value },
      });
      notice.textContent = result.detail;
      notice.style.display = 'flex';
      resendPassword.value = '';
      if (result.credential_email_sent) closeResendPanel();
    } catch (err) {
      resendError.textContent = typeof err.detail === 'string' ? err.detail : 'Could not resend credentials. Please try again.';
      resendError.style.display = 'block';
      resendPassword.value = '';
    } finally {
      resendSubmit.disabled = false;
    }
  });

  /* ---------- Row actions (edit / delete) ---------- */
  tbody.addEventListener('click', async (e) => {
    const btn = e.target.closest('.table-action-btn');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const student = students.find((s) => s.id === id);
    if (!student) return;

    if (btn.dataset.action === 'resend') {
      closeAddPanel();
      closeEditPanel();
      closeResendPanel();
      resendStudentId = student.id;
      document.getElementById('resendStudentRecipient').textContent = `Send credentials to ${student.username}`;
      resendPanel.style.display = 'block';
      resendPassword.focus();
      return;
    }
    if (btn.dataset.action === 'edit') {
      openEditPanel(student);
      return;
    }

    if (btn.dataset.action === 'delete') {
      const confirmed = window.confirm(
        `Delete ${student.full_name} (${student.username})? If this student has no enrollment, support, or activity history, their account is permanently removed. If they do have history, the account is deactivated instead (they can no longer log in) so that history is preserved.`
      );
      if (!confirmed) return;

      // Disabled for the whole round trip (re-enabled only on error) so a
      // double-click or a slow response can't fire this request twice.
      btn.disabled = true;
      try {
        const result = await ArckAPI.request(`/admin/students/${id}`, { method: 'DELETE' });
        if (result && result.action === 'deactivated' && result.student) {
          students = students.map((s) => (s.id === id ? result.student : s));
          if (editIdInput.value === String(id)) closeEditPanel();
          renderStudents(students);
          window.alert(result.detail || 'Student had related records, so the account was deactivated instead of deleted.');
        } else {
          students = students.filter((s) => s.id !== id);
          if (editIdInput.value === String(id)) closeEditPanel();
          renderStudents(students);
        }
      } catch (err) {
        window.alert(err.detail || 'Could not delete student.');
        btn.disabled = false;
      }
    }
  });

  toggleBtn.addEventListener('click', () => {
    if (addPanel.style.display === 'none') openAddPanel(); else closeAddPanel();
  });
  cancelBtn.addEventListener('click', closeAddPanel);
  toggleBtn.disabled = false;
});
