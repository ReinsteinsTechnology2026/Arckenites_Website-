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

  const canEdit = ArckAuth.hasPermission('community.edit');

  /* ---------- Table ---------- */
  const tableBody = document.getElementById('communityTableBody');
  const searchInput = document.getElementById('communitySearchInput');
  const statusFilter = document.getElementById('communityStatusFilter');
  let members = [];

  const renderRow = (m) => `
    <tr data-member-id="${m.id}">
      <td>${m.id}</td>
      <td>${escapeHtml(m.full_name)}</td>
      <td>${m.mobile_number ? escapeHtml(m.mobile_number) : '—'}</td>
      <td>${m.email ? escapeHtml(m.email) : '—'}</td>
      <td><span class="admin-activity-badge ${m.email_verified ? 'is-success' : 'is-muted'}">${m.email_verified ? 'Verified' : 'Unverified'}</span></td>
      <td><span class="admin-activity-badge ${m.is_active ? 'is-success' : 'is-danger'}">${m.is_active ? 'Active' : 'Inactive'}</span></td>
      <td>${formatDateTime(m.created_at)}</td>
      <td>
        ${canEdit ? `<button type="button" class="btn btn-primary-outline ${m.is_active ? 'is-danger' : ''}" style="padding:4px 12px;" data-action="toggle-active">${m.is_active ? 'Disable' : 'Enable'}</button>` : ''}
      </td>
    </tr>
  `;

  const applyFiltersAndRender = () => {
    const term = searchInput.value.trim().toLowerCase();
    const status = statusFilter.value;

    const filtered = members.filter((m) => {
      if (status === 'active' && !m.is_active) return false;
      if (status === 'inactive' && m.is_active) return false;
      if (!term) return true;
      return (
        (m.full_name || '').toLowerCase().includes(term) ||
        (m.email || '').toLowerCase().includes(term) ||
        (m.mobile_number || '').toLowerCase().includes(term)
      );
    });

    tableBody.innerHTML = filtered.length
      ? filtered.map(renderRow).join('')
      : '<tr><td colspan="8" class="admin-panel-empty">No community members match this search.</td></tr>';
  };

  const loadMembers = async () => {
    try {
      members = await ArckAPI.request('/admin/community');
      applyFiltersAndRender();
    } catch (_) {
      tableBody.innerHTML = '<tr><td colspan="8" class="admin-panel-empty">Couldn\'t load the community database.</td></tr>';
      showServerBanner();
    }
  };

  searchInput.addEventListener('input', applyFiltersAndRender);
  statusFilter.addEventListener('change', applyFiltersAndRender);

  tableBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action="toggle-active"]');
    if (!btn) return;
    const row = btn.closest('tr');
    const memberId = row.dataset.memberId;
    const member = members.find((m) => String(m.id) === String(memberId));
    if (!member) return;

    const nextActive = !member.is_active;
    const label = nextActive ? 'enable' : 'disable';
    if (!window.confirm(`Are you sure you want to ${label} this community member's account?`)) return;

    btn.disabled = true;
    try {
      const updated = await ArckAPI.request(`/admin/community/${memberId}`, {
        method: 'PATCH',
        body: { is_active: nextActive },
      });
      const idx = members.findIndex((m) => m.id === updated.id);
      if (idx !== -1) members[idx] = updated;
      applyFiltersAndRender();
    } catch (err) {
      window.alert(err.detail || `Could not ${label} this member.`);
      btn.disabled = false;
    }
  });

  /* ---------- Export ---------- */
  const exportBtn = document.getElementById('exportCommunityBtn');
  if (!ArckAuth.hasPermission('community.export')) exportBtn.style.display = 'none';
  exportBtn.addEventListener('click', async () => {
    exportBtn.disabled = true;
    try {
      const res = await fetch(`${API_BASE}/admin/community/export`, {
        headers: { Authorization: `Bearer ${ArckAPI.getToken()}` },
      });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `arckenites-community-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (_) {
      window.alert('Could not export community members. Check that the server is running and try again.');
    } finally {
      exportBtn.disabled = false;
    }
  });

  await loadMembers();

});
