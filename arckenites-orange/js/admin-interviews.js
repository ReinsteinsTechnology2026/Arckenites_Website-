const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDate = (iso) => iso ? new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const formatTime = (t) => t ? new Date(`1970-01-01T${t}`).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—';
const INTERVIEW_STATUS_BADGE = { scheduled: 'is-info', completed: 'is-success', cancelled: 'is-danger' };
const INTERVIEW_STATUS_LABEL = { scheduled: 'Scheduled', completed: 'Completed', cancelled: 'Cancelled' };

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

  /* ---------- Student picker ---------- */
  const studentSearchInput = document.getElementById('interviewStudentSearch');
  const studentResultsEl = document.getElementById('interviewStudentResults');
  const studentSelectedEl = document.getElementById('interviewStudentSelected');
  let studentSearchDebounce = null;
  let selectedStudent = null;

  const renderSelectedStudent = () => {
    studentSelectedEl.innerHTML = selectedStudent ? `
      <div class="batch-student-row">
        <span>${escapeHtml(selectedStudent.full_name)} &middot; @${escapeHtml(selectedStudent.username)}</span>
        <button type="button" class="btn btn-primary-outline is-danger" id="clearInterviewStudent">Remove</button>
      </div>
    ` : '';
  };

  studentSelectedEl.addEventListener('click', (e) => {
    if (e.target.closest('#clearInterviewStudent')) { selectedStudent = null; renderSelectedStudent(); }
  });

  studentSearchInput.addEventListener('input', () => {
    const q = studentSearchInput.value.trim();
    clearTimeout(studentSearchDebounce);
    if (!q) { studentResultsEl.innerHTML = ''; return; }
    studentSearchDebounce = setTimeout(async () => {
      try {
        const results = await ArckAPI.request(`/admin/interviews/lookup/students?q=${encodeURIComponent(q)}`);
        studentResultsEl.innerHTML = results.length
          ? results.map((s) => `
              <div class="batch-student-row">
                <span>${escapeHtml(s.full_name)} &middot; @${escapeHtml(s.username)}</span>
                <button type="button" class="btn btn-accent" data-pick='${JSON.stringify(s)}'>Select</button>
              </div>
            `).join('')
          : '<div class="batch-student-row">No matching students.</div>';
      } catch (_) {
        studentResultsEl.innerHTML = '<div class="batch-student-row">Search failed.</div>';
      }
    }, 300);
  });

  studentResultsEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-pick]');
    if (!btn) return;
    selectedStudent = JSON.parse(btn.dataset.pick);
    renderSelectedStudent();
    studentResultsEl.innerHTML = '';
    studentSearchInput.value = '';
  });

  /* ---------- Add-interview panel toggle ---------- */
  const addPanel = document.getElementById('addInterviewPanel');
  const form = document.getElementById('addInterviewForm');
  const errorBox = document.getElementById('addInterviewError');
  const submitBtn = document.getElementById('addInterviewSubmitBtn');

  const resetForm = () => {
    form.reset();
    selectedStudent = null;
    renderSelectedStudent();
    studentResultsEl.innerHTML = '';
    errorBox.style.display = 'none';
  };

  if (!ArckAuth.hasPermission('placement.create')) document.getElementById('toggleAddInterviewBtn').style.display = 'none';

  document.getElementById('toggleAddInterviewBtn').addEventListener('click', () => {
    const isHidden = addPanel.style.display === 'none';
    addPanel.style.display = isHidden ? 'block' : 'none';
    if (isHidden) studentSearchInput.focus();
  });
  document.getElementById('cancelAddInterviewBtn').addEventListener('click', () => {
    addPanel.style.display = 'none';
    resetForm();
  });

  /* ---------- Table rendering ---------- */
  const tbody = document.getElementById('interviewsTableBody');
  let interviews = [];
  const canDeleteInterviews = ArckAuth.hasPermission('placement.delete');

  const rowHtml = (i) => `
    <tr>
      <td>${escapeHtml(i.student_name)}<br><span style="color:var(--muted); font-size:.78rem;">@${escapeHtml(i.student_username)}</span></td>
      <td>${escapeHtml(i.company_name)}</td>
      <td>${i.role ? escapeHtml(i.role) : '—'}</td>
      <td>${formatDate(i.interview_date)}</td>
      <td>${formatTime(i.interview_time)}</td>
      <td>${i.mode === 'online' ? 'Online' : 'Offline'}</td>
      <td>${i.location_or_link ? (i.mode === 'online' ? `<a href="${escapeHtml(i.location_or_link)}" target="_blank" rel="noopener">Link</a>` : escapeHtml(i.location_or_link)) : '—'}</td>
      <td>
        <select class="form-select" data-status-select="${i.id}" style="min-width:120px;">
          <option value="scheduled" ${i.status === 'scheduled' ? 'selected' : ''}>Scheduled</option>
          <option value="completed" ${i.status === 'completed' ? 'selected' : ''}>Completed</option>
          <option value="cancelled" ${i.status === 'cancelled' ? 'selected' : ''}>Cancelled</option>
        </select>
      </td>
      <td>
        ${canDeleteInterviews ? `<button type="button" class="table-action-btn is-danger" data-delete="${i.id}" title="Delete"><i class="fa-solid fa-trash"></i></button>` : ''}
      </td>
    </tr>
  `;

  const renderInterviews = () => {
    tbody.innerHTML = interviews.length
      ? interviews.map(rowHtml).join('')
      : '<tr><td colspan="9" class="admin-panel-empty">No interviews scheduled yet.</td></tr>';
  };

  const loadInterviews = async () => {
    try {
      interviews = await ArckAPI.request('/admin/interviews');
      renderInterviews();
    } catch (err) {
      if (err.status === 0) showServerBanner();
      tbody.innerHTML = '<tr><td colspan="9" class="admin-panel-empty">Couldn\'t load interviews.</td></tr>';
    }
  };

  tbody.addEventListener('change', async (e) => {
    const select = e.target.closest('[data-status-select]');
    if (!select) return;
    select.disabled = true;
    try {
      await ArckAPI.request(`/admin/interviews/${select.dataset.statusSelect}`, { method: 'PATCH', body: { status: select.value } });
      await loadInterviews();
    } catch (err) {
      window.alert(err.detail || 'Could not update status.');
      select.disabled = false;
    }
  });

  tbody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-delete]');
    if (!btn) return;
    if (!window.confirm('Delete this interview? This cannot be undone.')) return;
    btn.disabled = true;
    try {
      await ArckAPI.request(`/admin/interviews/${btn.dataset.delete}`, { method: 'DELETE' });
      await loadInterviews();
    } catch (err) {
      window.alert(err.detail || 'Could not delete this interview.');
      btn.disabled = false;
    }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';

    if (!selectedStudent) {
      errorBox.textContent = 'Pick a student first.';
      errorBox.style.display = 'block';
      return;
    }

    submitBtn.disabled = true;
    const body = {
      student_id: selectedStudent.id,
      company_name: document.getElementById('interviewCompany').value.trim(),
      role: document.getElementById('interviewRole').value.trim() || null,
      interview_date: document.getElementById('interviewDate').value,
      interview_time: document.getElementById('interviewTime').value || null,
      mode: document.getElementById('interviewMode').value,
      location_or_link: document.getElementById('interviewLocation').value.trim() || null,
      notes: document.getElementById('interviewNotes').value.trim() || null,
    };

    try {
      await ArckAPI.request('/admin/interviews', { method: 'POST', body });
      addPanel.style.display = 'none';
      resetForm();
      await loadInterviews();
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not schedule interview.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

  await loadInterviews();

});
