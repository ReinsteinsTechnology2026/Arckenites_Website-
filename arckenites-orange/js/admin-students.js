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
  const cancelBtn = document.getElementById('cancelAddStudentBtn');
  const form = document.getElementById('addStudentForm');
  const nameInput = document.getElementById('newStudentName');
  const passwordInput = document.getElementById('newStudentPassword');
  const errorBox = document.getElementById('addStudentError');
  const submitBtn = document.getElementById('addStudentSubmitBtn');
  const notice = document.getElementById('newStudentNotice');

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
  const tbody = document.getElementById('studentsTableBody');
  const serverBanner = document.getElementById('adminServerBanner');
  const showServerBanner = () => serverBanner.classList.add('is-visible');

  const statusBadge = (student) => {
    if (!student.is_active) return '<span class="admin-activity-badge is-danger">Inactive</span>';
    if (student.must_change_password) return '<span class="admin-activity-badge is-info">Awaiting First Login</span>';
    return '<span class="admin-activity-badge is-success">Active</span>';
  };

  const programBadge = (program) => program
    ? `<span class="admin-activity-badge is-muted">${PROGRAM_LABELS[program] || program}</span>`
    : '<span class="admin-activity-badge is-pending">Not selected yet</span>';

  const rowHtml = (student) => `
    <tr>
      <td>${student.username}</td>
      <td>${student.full_name}</td>
      <td>${programBadge(student.program)}</td>
      <td>${statusBadge(student)}</td>
      <td title="${new Date(student.created_at).toLocaleString()}">${new Date(student.created_at).toLocaleDateString()}</td>
      <td>
        <div class="admin-row-actions">
          <button type="button" class="table-action-btn" data-action="edit" data-id="${student.id}" title="Edit ${student.full_name}"><i class="fa-solid fa-pen"></i></button>
          <button type="button" class="table-action-btn is-danger" data-action="delete" data-id="${student.id}" title="Delete ${student.full_name}"><i class="fa-solid fa-trash"></i></button>
        </div>
      </td>
    </tr>
  `;

  const renderStudents = (list) => {
    tbody.innerHTML = list.length
      ? list.map(rowHtml).join('')
      : '<tr><td colspan="6" class="admin-panel-empty">No students yet. Click "Add New Student" to create the first one.</td></tr>';
  };

  const loadStudents = async () => {
    try {
      const list = await ArckAPI.request('/admin/students');
      renderStudents(list);
      return list;
    } catch (err) {
      if (err.status === 0) showServerBanner();
      tbody.innerHTML = '<tr><td colspan="6" class="admin-panel-empty">Couldn\'t load students.</td></tr>';
      return [];
    }
  };

  let students = await loadStudents();

  /* ---------- Create student ---------- */
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';
    notice.style.display = 'none';
    submitBtn.disabled = true;

    try {
      const created = await ArckAPI.request('/admin/students', {
        method: 'POST',
        body: { full_name: nameInput.value.trim(), temp_password: passwordInput.value },
      });
      students = [created, ...students];
      renderStudents(students);
      closeAddPanel();

      notice.innerHTML = `Created <code>${created.username}</code> for ${created.full_name}. Share the username and temporary password with the student — they'll be asked to set their own password on first login.`;
      notice.style.display = 'flex';
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not create student.';
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
  const editProgramSelect = document.getElementById('editStudentProgram');
  const editActiveSelect = document.getElementById('editStudentActive');
  const editResetPasswordInput = document.getElementById('editStudentResetPassword');
  const editErrorBox = document.getElementById('editStudentError');
  const editSubmitBtn = document.getElementById('editStudentSubmitBtn');
  const cancelEditBtn = document.getElementById('cancelEditStudentBtn');

  Object.entries(PROGRAM_LABELS).forEach(([value, label]) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    editProgramSelect.appendChild(opt);
  });

  const closeEditPanel = () => {
    editPanel.style.display = 'none';
    editForm.reset();
    editErrorBox.style.display = 'none';
  };
  const openEditPanel = (student) => {
    closeAddPanel();
    editIdInput.value = student.id;
    editNameInput.value = student.full_name;
    editProgramSelect.value = student.program || '';
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
      program: editProgramSelect.value || null,
    };
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

  /* ---------- Row actions (edit / delete) ---------- */
  tbody.addEventListener('click', async (e) => {
    const btn = e.target.closest('.table-action-btn');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const student = students.find((s) => s.id === id);
    if (!student) return;

    if (btn.dataset.action === 'edit') {
      openEditPanel(student);
      return;
    }

    if (btn.dataset.action === 'delete') {
      const confirmed = window.confirm(`Delete ${student.full_name} (${student.username})? This permanently removes their login and cannot be undone.`);
      if (!confirmed) return;

      btn.disabled = true;
      try {
        await ArckAPI.request(`/admin/students/${id}`, { method: 'DELETE' });
        students = students.filter((s) => s.id !== id);
        renderStudents(students);
        if (editIdInput.value === String(id)) closeEditPanel();
      } catch (err) {
        window.alert(err.detail || 'Could not delete student.');
        btn.disabled = false;
      }
    }
  });

});
