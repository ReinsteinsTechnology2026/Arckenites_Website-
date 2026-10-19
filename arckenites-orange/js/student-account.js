/* Student-only account views. Every data request is scoped by the backend token. */
document.addEventListener('DOMContentLoaded', async () => {
  const root = document.getElementById('student-id-card');
  if (!root) return;
  let user;
  try { user = await ArckAuth.requireRole('student'); } catch (_) { return; }
  if (!user) return;

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date = (value) => value ? new Date(value).toLocaleString() : 'Unknown';
  const error = (err, fallback) => ArckAPI.errorMessage(err, fallback);
  const contact = document.getElementById('contactDetailsBody');
  const card = document.getElementById('studentIdCardBody');
  const renderPersonal = () => {
    contact.innerHTML = `<div class="detail-row"><span class="form-label">Registered email</span><span>${esc(user.email || 'Not recorded')}</span></div>
      <div class="detail-row"><span class="form-label">Phone</span><span>${esc(user.phone || 'Not recorded')}</span></div>
      <p>These contact details have not been verified by this screen.</p>`;
    if (user.student_id) {
      card.innerHTML = `<div class="student-identity-card">
      <div class="student-identity-brand">Arckenites · Student</div>
      <div class="student-identity-person">${ArckAPI.avatarHtml(user.full_name, user.photo_url, 72)}<div><strong>${esc(user.full_name)}</strong><div>${esc(user.student_id)}</div></div></div>
      ${user.program_name ? `<div>Program: ${esc(user.program_name)}</div>` : ''}
    </div><button type="button" class="btn btn-primary-outline student-print-card">Print card</button>`;
      card.querySelector('.student-print-card').addEventListener('click', () => window.print());
    } else {
      card.textContent = 'An official student ID has not been assigned yet.';
    }
  };
  renderPersonal();
  window.addEventListener('student-account-updated', (event) => { user = event.detail; renderPersonal(); });

  const enrollments = document.getElementById('accountEnrollmentsBody');
  try {
    const data = await ArckAPI.request('/students/me/enrollments');
    const programRows = data.programs.map((row) => `<li><strong>${esc(row.name)}</strong> · ${esc(row.status)}${row.domain ? ` · ${esc(row.domain)}` : ''}<br><small>Enrolled ${esc(date(row.enrolled_at))}</small></li>`).join('');
    const batchRows = data.batches.map((row) => `<li><strong>${esc(row.name)}</strong>${row.course ? ` · ${esc(row.course)}` : ''} · ${esc(row.status)}${row.program_name ? ` · ${esc(row.program_name)}` : ''}<br><small>Joined ${esc(date(row.joined_at))}</small></li>`).join('');
    enrollments.innerHTML = `<h3>Programs</h3>${programRows ? `<ul>${programRows}</ul>` : '<p>No program enrollments recorded.</p>'}
      <h3>Batches and courses</h3>${batchRows ? `<ul>${batchRows}</ul>` : '<p>No batch enrollments recorded.</p>'}`;
  } catch (err) { enrollments.textContent = error(err, 'Could not load enrollments.'); }

  const sessions = document.getElementById('accountSessionsBody');
  const loadSessions = async () => {
    sessions.textContent = 'Loading sessions…';
    try {
      const rows = await ArckAPI.request('/me/sessions');
      sessions.innerHTML = rows.length ? rows.map((row) => `<div class="student-account-row">
        <div><strong>${esc(row.device_label)}</strong>${row.is_current ? ' · Current session' : ''}<br>
        <small>Signed in ${esc(date(row.issued_at))}${row.ip_address ? ` · IP ${esc(row.ip_address)}` : ''}</small></div>
        ${row.is_current ? '' : `<button type="button" class="btn btn-primary-outline" data-revoke-session="${esc(row.id)}">Revoke</button>`}
      </div>`).join('') : '<p>No active sessions found.</p>';
    } catch (err) { sessions.textContent = error(err, 'Could not load sessions.'); }
  };
  sessions.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-revoke-session]');
    if (!button || button.disabled) return;
    const password = document.getElementById('sessionReauthPassword');
    const status = document.getElementById('accountSessionsStatus');
    if (!password.value) { status.textContent = 'Enter your current password first.'; password.focus(); return; }
    if (!window.confirm('End this session?')) return;
    button.disabled = true;
    status.textContent = 'Ending session…';
    try {
      await ArckAPI.request(`/me/sessions/${encodeURIComponent(button.dataset.revokeSession)}`, {method:'DELETE', body:{current_password: password.value}});
      password.value = '';
      await loadSessions();
      status.textContent = 'Session ended.';
    } catch (err) { status.textContent = error(err, 'Could not revoke session.'); button.disabled = false; }
  });
  await loadSessions();

  const history = document.getElementById('accountLoginHistoryBody');
  const more = document.getElementById('loginHistoryMoreBtn');
  let page = 1;
  const loadHistory = async () => {
    more.disabled = true;
    if (page === 1) history.textContent = 'Loading history…';
    try {
      const result = await ArckAPI.request(`/students/me/login-history?page=${page}`);
      const html = result.items.map((row) => `<div class="student-account-row"><span><strong>${row.result === 'success' ? 'Successful sign-in' : 'Failed sign-in'}</strong><br><small>${esc(date(row.at))}${row.ip_address ? ` · IP ${esc(row.ip_address)}` : ''}</small></span></div>`).join('');
      if (page === 1) history.innerHTML = html || '<p>No login events recorded.</p>';
      else history.insertAdjacentHTML('beforeend', html);
      more.style.display = result.has_more ? 'inline-block' : 'none';
      page += 1;
    } catch (err) { history.textContent = error(err, 'Could not load login history.'); }
    finally { more.disabled = false; }
  };
  more.addEventListener('click', loadHistory);
  await loadHistory();

  const exportButton = document.getElementById('downloadMyDataBtn');
  const exportStatus = document.getElementById('dataExportStatus');
  exportButton.addEventListener('click', async () => {
    exportButton.disabled = true;
    exportStatus.textContent = 'Preparing your download…';
    try {
      const response = await fetch(`${API_BASE}/students/me/data-export`, {headers: {Authorization: `Bearer ${ArckAPI.getToken()}`}});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new ApiError(response.status, data.detail);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'arckenites-my-data.json';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      exportStatus.textContent = 'Download ready.';
    } catch (err) { exportStatus.textContent = error(err, 'Could not download your data.'); }
    finally { exportButton.disabled = false; }
  });
});
