const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

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
  const sidebarBackdrop = document.getElementById('adminSidebarBackdrop');
  const openMobileSidebar = () => { sidebar.classList.add('is-mobile-open'); sidebarBackdrop.classList.add('is-visible'); };
  const closeMobileSidebar = () => { sidebar.classList.remove('is-mobile-open'); sidebarBackdrop.classList.remove('is-visible'); };
  document.getElementById('adminMobileToggle').addEventListener('click', openMobileSidebar);
  sidebarBackdrop.addEventListener('click', closeMobileSidebar);
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
  const errorBox = document.getElementById('settingsError');
  const notice = document.getElementById('settingsNotice');

  /* ---------- Tabs ---------- */
  document.querySelectorAll('.settings-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.settings-tab').forEach((t) => t.classList.remove('is-active'));
      document.querySelectorAll('.settings-tab-panel').forEach((p) => p.classList.remove('is-active'));
      tab.classList.add('is-active');
      document.querySelector(`.settings-tab-panel[data-panel="${tab.dataset.tab}"]`).classList.add('is-active');
    });
  });

  /* ---------- Load settings ---------- */
  const fields = {
    institute_name: document.getElementById('settingInstituteName'),
    logo_url: document.getElementById('settingLogoUrl'),
    contact_email: document.getElementById('settingContactEmail'),
    timezone: document.getElementById('settingTimezone'),
    date_format: document.getElementById('settingDateFormat'),
    session_timeout_minutes: document.getElementById('settingSessionTimeout'),
    max_login_attempts: document.getElementById('settingMaxLoginAttempts'),
    lockout_duration_minutes: document.getElementById('settingLockoutDuration'),
    require_strong_passwords: document.getElementById('settingStrongPasswords'),
    notify_new_account: document.getElementById('settingNotifyNewAccount'),
    notify_password_reset: document.getElementById('settingNotifyPasswordReset'),
    notify_security_alert: document.getElementById('settingNotifySecurityAlert'),
    maintenance_mode: document.getElementById('settingMaintenanceMode'),
  };

  const TAB_FIELDS = {
    general: ['institute_name', 'logo_url', 'contact_email', 'timezone', 'date_format'],
    security: ['session_timeout_minutes', 'max_login_attempts', 'lockout_duration_minutes', 'require_strong_passwords'],
    notifications: ['notify_new_account', 'notify_password_reset', 'notify_security_alert'],
    system: ['maintenance_mode'],
  };

  const applySettings = (data) => {
    Object.entries(fields).forEach(([key, el]) => {
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!data[key];
      else el.value = data[key] ?? '';
    });
  };

  const loadSettings = async () => {
    try {
      const data = await ArckAPI.request('/admin/settings');
      applySettings(data);
    } catch (err) {
      if (err.status === 0) showServerBanner();
      if (err.status !== 403) {
        errorBox.textContent = 'Could not load settings.';
        errorBox.style.display = 'block';
      }
    }
  };
  await loadSettings();

  const saveTab = async (tabName) => {
    errorBox.style.display = 'none';
    notice.style.display = 'none';
    const body = {};
    TAB_FIELDS[tabName].forEach((key) => {
      const el = fields[key];
      if (!el) return;
      body[key] = el.type === 'checkbox' ? el.checked : (el.type === 'number' ? Number(el.value) : el.value);
    });
    try {
      const updated = await ArckAPI.request('/admin/settings', { method: 'PATCH', body });
      applySettings(updated);
      notice.textContent = 'Settings saved.';
      notice.style.display = 'flex';
      setTimeout(() => { notice.style.display = 'none'; }, 3000);
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not save settings.';
      errorBox.style.display = 'block';
    }
  };

  document.querySelectorAll('[data-save-tab]').forEach((btn) => {
    btn.addEventListener('click', () => saveTab(btn.dataset.saveTab));
  });

  if (!ArckAuth.hasPermission('settings.edit')) {
    document.querySelectorAll('[data-save-tab]').forEach((btn) => { btn.disabled = true; btn.title = 'You do not have permission to edit settings.'; });
    document.querySelectorAll('#settingsTabs ~ div input, #settingsTabs ~ div select').forEach((el) => { el.disabled = true; });
  }

  /* ---------- Security overview ---------- */
  const loadSecurityOverview = async () => {
    const el = document.getElementById('securityOverview');
    try {
      const stats = await ArckAPI.request('/dashboard/stats');
      el.innerHTML = `
        <div class="admin-kpi-grid" style="margin:0;">
          <div class="admin-kpi-card"><i class="fa-solid fa-triangle-exclamation"></i><h3>${stats.failed_logins_today}</h3><p>Failed Logins Today</p></div>
          <div class="admin-kpi-card"><i class="fa-solid fa-lock"></i><h3>${stats.locked_accounts}</h3><p>Locked Accounts</p></div>
          <div class="admin-kpi-card"><i class="fa-solid fa-right-to-bracket"></i><h3>${stats.logins_today}</h3><p>Logins Today</p></div>
        </div>
      `;
    } catch (_) {
      el.innerHTML = 'Could not load login security data.';
    }
  };
  await loadSecurityOverview();

  /* ---------- Active sessions ---------- */
  const sessionsList = document.getElementById('activeSessionsList');
  const loadSessions = async () => {
    try {
      const sessions = await ArckAPI.request('/me/sessions');
      sessionsList.innerHTML = sessions.length ? sessions.map((s) => `
        <div class="session-row">
          <div class="session-row-meta">
            <strong>${escapeHtml(s.device_label)}${s.is_current ? ' <span style="color:var(--primary);">(This device)</span>' : ''}</strong>
            <span>${s.ip_address ? escapeHtml(s.ip_address) + ' &middot; ' : ''}Since ${new Date(s.issued_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</span>
          </div>
          ${s.is_current ? '' : `<button type="button" class="btn btn-primary-outline is-danger" data-revoke="${s.id}">Revoke</button>`}
        </div>
      `).join('') : '<div class="admin-panel-empty">No active sessions.</div>';
    } catch (_) {
      sessionsList.innerHTML = '<div class="admin-panel-empty">Could not load sessions.</div>';
    }
  };
  await loadSessions();

  sessionsList.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-revoke]');
    if (!btn) return;
    btn.disabled = true;
    try {
      await ArckAPI.request(`/me/sessions/${btn.dataset.revoke}`, { method: 'DELETE' });
      await loadSessions();
    } catch (err) {
      window.alert(err.detail || 'Could not revoke session.');
      btn.disabled = false;
    }
  });

  document.getElementById('revokeOtherSessionsBtn').addEventListener('click', async () => {
    if (!window.confirm('Sign out of every other device?')) return;
    try {
      await ArckAPI.request('/me/sessions/revoke-others', { method: 'POST' });
      await loadSessions();
    } catch (err) {
      window.alert(err.detail || 'Could not revoke sessions.');
    }
  });

  /* ---------- System health ---------- */
  const loadHealth = async () => {
    const el = document.getElementById('systemHealth');
    try {
      const health = await ArckAPI.request('/admin/system/health');
      const badge = (v) => v === 'healthy'
        ? '<span class="admin-activity-badge is-success">Healthy</span>'
        : `<span class="admin-activity-badge is-danger">${escapeHtml(v)}</span>`;
      el.innerHTML = `
        <div class="settings-toggle-row"><div class="settings-toggle-label"><strong>Database</strong></div>${badge(health.database)}</div>
        <div class="settings-toggle-row"><div class="settings-toggle-label"><strong>Authentication</strong></div>${badge(health.authentication)}</div>
        <div class="settings-toggle-row"><div class="settings-toggle-label"><strong>API</strong></div>${badge(health.api)}</div>
      `;
    } catch (_) {
      el.innerHTML = 'Could not load system health.';
    }
  };
  await loadHealth();

});
