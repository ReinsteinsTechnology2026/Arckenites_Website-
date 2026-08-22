const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';

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

  /* ---------- Table rendering ---------- */
  const tbody = document.getElementById('enquiriesTableBody');
  let enquiries = [];

  const rowHtml = (e) => `
    <tr class="${e.is_read ? '' : 'is-unread'}">
      <td>${escapeHtml(e.full_name)}</td>
      <td>${escapeHtml(e.phone)}</td>
      <td><a href="mailto:${escapeHtml(e.email)}">${escapeHtml(e.email)}</a></td>
      <td>${e.subject ? escapeHtml(e.subject) : '—'}</td>
      <td style="max-width:280px; white-space:normal;">${escapeHtml(e.message)}</td>
      <td>${formatDateTime(e.created_at)}</td>
      <td>
        <span class="soon-badge" style="background:${e.is_read ? 'var(--muted-bg, #eee)' : 'var(--accent, #ff7a00)'}; color:${e.is_read ? 'var(--muted, #666)' : '#fff'};">${e.is_read ? 'Read' : 'New'}</span>
      </td>
      <td>
        <button type="button" class="table-action-btn" data-toggle-read="${e.id}" data-current="${e.is_read}" title="${e.is_read ? 'Mark as unread' : 'Mark as read'}">
          <i class="fa-solid ${e.is_read ? 'fa-envelope' : 'fa-envelope-open'}"></i>
        </button>
        <button type="button" class="table-action-btn is-danger" data-delete="${e.id}" title="Delete"><i class="fa-solid fa-trash"></i></button>
      </td>
    </tr>
  `;

  const renderEnquiries = () => {
    tbody.innerHTML = enquiries.length
      ? enquiries.map(rowHtml).join('')
      : '<tr><td colspan="8" class="admin-panel-empty">No enquiries yet.</td></tr>';
  };

  const loadEnquiries = async () => {
    try {
      enquiries = await ArckAPI.request('/admin/contact-enquiries');
      renderEnquiries();
    } catch (err) {
      if (err.status === 0) showServerBanner();
      tbody.innerHTML = '<tr><td colspan="8" class="admin-panel-empty">Couldn\'t load enquiries.</td></tr>';
    }
  };

  tbody.addEventListener('click', async (e) => {
    const toggleBtn = e.target.closest('[data-toggle-read]');
    if (toggleBtn) {
      toggleBtn.disabled = true;
      const nextIsRead = toggleBtn.dataset.current !== 'true';
      try {
        await ArckAPI.request(`/admin/contact-enquiries/${toggleBtn.dataset.toggleRead}`, { method: 'PATCH', body: { is_read: nextIsRead } });
        await loadEnquiries();
      } catch (err) {
        window.alert(err.detail || 'Could not update this enquiry.');
        toggleBtn.disabled = false;
      }
      return;
    }

    const deleteBtn = e.target.closest('[data-delete]');
    if (deleteBtn) {
      if (!window.confirm('Delete this enquiry? This cannot be undone.')) return;
      deleteBtn.disabled = true;
      try {
        await ArckAPI.request(`/admin/contact-enquiries/${deleteBtn.dataset.delete}`, { method: 'DELETE' });
        await loadEnquiries();
      } catch (err) {
        window.alert(err.detail || 'Could not delete this enquiry.');
        deleteBtn.disabled = false;
      }
    }
  });

  await loadEnquiries();

});
