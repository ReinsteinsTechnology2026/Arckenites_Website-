const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDate = (iso) => iso ? new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const formatTime = (t) => t ? new Date(`1970-01-01T${t}`).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—';

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

  const tbody = document.getElementById('allSessionsTableBody');
  try {
    const sessions = await ArckAPI.request('/admin/class-schedule');
    tbody.innerHTML = sessions.length
      ? sessions.map((s) => `
          <tr>
            <td>${escapeHtml(s.title)}</td>
            <td><a href="admin-batch-detail.html?id=${s.batch_id}">${escapeHtml(s.batch_name)}</a></td>
            <td>${s.program_name ? escapeHtml(s.program_name) : '—'}</td>
            <td>${s.trainer_name ? escapeHtml(s.trainer_name) : '<span class="admin-activity-badge is-pending">Unassigned</span>'}</td>
            <td>${formatDate(s.session_date)}</td>
            <td>${(s.start_time && s.end_time) ? `${formatTime(s.start_time)} – ${formatTime(s.end_time)}` : '—'}</td>
            <td><a class="btn btn-accent" style="padding:4px 12px;" href="meeting-room.html?batch=${s.batch_id}"><i class="fa-solid fa-video"></i> Join</a></td>
            <td>${s.notes ? escapeHtml(s.notes) : '—'}</td>
          </tr>
        `).join('')
      : '<tr><td colspan="8" class="admin-panel-empty">No classes scheduled yet across any batch.</td></tr>';
  } catch (err) {
    if (err.status === 0) showServerBanner();
    tbody.innerHTML = '<tr><td colspan="8" class="admin-panel-empty">Couldn\'t load the class schedule.</td></tr>';
  }

});
