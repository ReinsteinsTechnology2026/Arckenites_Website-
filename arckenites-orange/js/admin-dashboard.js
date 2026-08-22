document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('admin');
  if (!user) return; // requireRole already redirected

  /* ---------- Greeting + profile ---------- */
  const hour = new Date().getHours();
  const timeOfDay = hour < 12 ? 'Morning' : hour < 17 ? 'Afternoon' : 'Evening';
  document.getElementById('adminGreeting').textContent = `Good ${timeOfDay}, ${user.full_name}`;

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

  /* ---------- Dropdowns (notifications + profile) ---------- */
  const dropdowns = [
    { trigger: document.getElementById('adminNotifTrigger'), panel: document.getElementById('adminNotifPanel') },
    { trigger: document.getElementById('adminProfileTrigger'), panel: document.getElementById('adminProfilePanel') },
  ];
  const closeAllDropdowns = () => dropdowns.forEach((d) => d.panel.classList.remove('is-open'));
  dropdowns.forEach(({ trigger, panel }) => {
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = !panel.classList.contains('is-open');
      closeAllDropdowns();
      if (willOpen) panel.classList.add('is-open');
    });
  });
  document.addEventListener('click', closeAllDropdowns);

  /* ---------- Logout (sidebar + profile menu) ---------- */
  document.getElementById('adminSidebarLogout').addEventListener('click', () => ArckAuth.logout());
  document.getElementById('adminProfileLogout').addEventListener('click', () => ArckAuth.logout());

  /* ---------- Data loading ---------- */
  const serverBanner = document.getElementById('adminServerBanner');
  const showServerBanner = () => serverBanner.classList.add('is-visible');

  const relativeTime = (isoString) => {
    const diffMs = Date.now() - new Date(isoString).getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
  };

  const BADGE_CLASS = {
    login_success: 'is-success',
    login_failure: 'is-danger',
    account_locked: 'is-danger',
    rate_limited: 'is-danger',
    logout: 'is-muted',
    password_change: 'is-info',
  };
  const EVENT_LABELS = {
    login_success: 'Logged In',
    login_failure: 'Login Failed',
    logout: 'Logged Out',
    password_change: 'Password Changed',
    account_locked: 'Account Locked',
    rate_limited: 'Rate Limited',
  };
  const SECURITY_EVENTS = new Set(['login_failure', 'account_locked', 'rate_limited']);

  const loadStats = ArckAPI.request('/dashboard/stats').then((stats) => {
    document.getElementById('kpiTotalUsers').textContent = stats.total_users.toLocaleString();
    const byRole = Object.fromEntries(stats.users_by_role.map((r) => [r.role, r.count]));
    document.getElementById('kpiStudents').textContent = (byRole.student || 0).toLocaleString();
    document.getElementById('kpiStaff').textContent = (byRole.staff || 0).toLocaleString();
    document.getElementById('kpiActive').textContent = stats.active_users.toLocaleString();
    document.getElementById('kpiLoginsToday').textContent = stats.logins_today.toLocaleString();
    document.getElementById('kpiFailedToday').textContent = stats.failed_logins_today.toLocaleString();

    const securitySignal = stats.failed_logins_today + stats.locked_accounts;
    document.getElementById('kpiFailedCard').classList.toggle('is-warning', securitySignal > 0);

    const badge = document.getElementById('adminNotifBadge');
    if (securitySignal > 0) {
      badge.textContent = securitySignal > 99 ? '99+' : String(securitySignal);
      badge.style.display = '';
    }
    return stats;
  }).catch((err) => {
    if (err.status === 0) showServerBanner();
    document.getElementById('kpiFailedCard').closest('.admin-kpi-grid').querySelectorAll('h3').forEach((el) => { el.textContent = '—'; });
    return null;
  });

  const loadActivity = ArckAPI.request('/dashboard/activity?limit=10').then((data) => {
    const tbody = document.getElementById('adminActivityBody');
    if (!data.items.length) {
      tbody.innerHTML = '<tr><td colspan="3" class="admin-panel-empty">No activity yet.</td></tr>';
      return data;
    }
    tbody.innerHTML = data.items.map((item) => `
      <tr>
        <td>${item.actor_name}</td>
        <td><span class="admin-activity-badge ${BADGE_CLASS[item.event_type] || 'is-muted'}" title="${item.message}">${EVENT_LABELS[item.event_type] || item.event_type}</span></td>
        <td title="${new Date(item.created_at).toLocaleString()}">${relativeTime(item.created_at)}</td>
      </tr>
    `).join('');
    return data;
  }).catch((err) => {
    if (err.status === 0) showServerBanner();
    document.getElementById('adminActivityBody').innerHTML =
      '<tr><td colspan="3" class="admin-panel-empty">Couldn\'t load recent activity.</td></tr>';
    return null;
  });

  const loadSystemHealth = ArckAPI.request('/admin/system/health').then((health) => {
    const badge = (v) => v === 'healthy'
      ? '<span class="admin-activity-badge is-success">Healthy</span>'
      : `<span class="admin-activity-badge is-danger">${v}</span>`;
    document.getElementById('dashboardSystemHealth').innerHTML = `
      <span>Database ${badge(health.database)}</span>
      <span>Authentication ${badge(health.authentication)}</span>
      <span>API ${badge(health.api)}</span>
    `;
  }).catch(() => {
    document.getElementById('dashboardSystemHealth').textContent = "Couldn't load system health.";
  });

  const [, activityData] = await Promise.all([loadStats, loadActivity, loadSystemHealth]);

  const notifList = document.getElementById('adminNotifList');
  const securityItems = (activityData?.items || []).filter((i) => SECURITY_EVENTS.has(i.event_type)).slice(0, 5);
  notifList.innerHTML = securityItems.length
    ? securityItems.map((i) => `
        <div class="admin-notif-item">
          <p>${i.message}</p>
          <time>${relativeTime(i.created_at)}</time>
        </div>
      `).join('')
    : '<div class="admin-dropdown-empty">No security alerts.</div>';

});
