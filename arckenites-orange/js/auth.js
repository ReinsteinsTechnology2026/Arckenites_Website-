/* ============================================================
   ARCKENITES PORTAL — AUTH HELPERS
   Single source of truth for login/logout/session-guard logic,
   shared by all three roles instead of duplicating it per page.
   ============================================================ */

const ROLE_DASHBOARD = {
  admin: 'admin-dashboard.html',
  staff: 'staff-dashboard.html',
  student: 'student-dashboard.html',
};

/**
 * Single source of truth mapping each admin page to the permission key
 * required to view it — reused for both hiding the sidebar link (cosmetic)
 * and gating the page itself (functional; see enforcePagePermission below).
 * Keep in sync with PERMISSION_CATALOG in backend/app/crud/permissions.py —
 * only real, existing keys are used here, nothing invented. Pages not
 * listed (admin-dashboard.html, admin-contact-enquiries.html) have no
 * matching permission in the catalog yet and stay visible to any admin,
 * matching their current backend routes (require_role("admin") only, no
 * require_permission check) — hiding them here would be cosmetic-only and
 * inconsistent with what the backend actually allows.
 */
const ADMIN_PAGE_PERMISSIONS = {
  'admin-students.html': 'students.view',
  'admin-staff.html': 'trainers.view',
  'admin-programs.html': 'programs.view',
  'admin-program-detail.html': 'programs.view',
  'admin-batches.html': 'batches.view',
  'admin-batch-detail.html': 'batches.view',
  'admin-class-schedule.html': 'batches.view',
  'admin-meetings.html': 'meetings.view',
  'admin-support.html': 'support.view',
  'admin-support-detail.html': 'support.view',
  'admin-lab-access.html': 'lab_access.view',
  'admin-leads.html': 'leads.view',
  'admin-interviews.html': 'placement.view',
  'admin-users.html': 'admin_users.view',
  'admin-roles.html': 'roles.view',
  'admin-activity-logs.html': 'activity_logs.view',
  'admin-settings.html': 'settings.view',
};

// Every link in this app is a bare relative filename, optionally with a
// query string (e.g. "admin-students.html?view=database" for the Student
// Database sidebar entry) — strip both the query and any path prefix so
// lookups in ADMIN_PAGE_PERMISSIONS work the same whether given a sidebar
// link's href or the current page's own location.
function _adminPagePathname(href) {
  return href.split('?')[0].split('/').pop();
}

/**
 * Hides each sidebar link the current admin lacks the permission for, then
 * hides the whole category (group heading + list) if nothing under it is
 * left visible. Cosmetic only — see hasPermission's docstring; the backend
 * independently rejects any request this doesn't manage to hide.
 */
function applySidebarPermissions(user) {
  const permissions = Array.isArray(user.permissions) ? user.permissions : [];

  document.querySelectorAll('.admin-sidebar-link[href]').forEach((link) => {
    const required = ADMIN_PAGE_PERMISSIONS[_adminPagePathname(link.getAttribute('href'))];
    if (!required || permissions.includes(required)) return;
    const item = link.closest('li') || link;
    item.style.display = 'none';
  });

  document.querySelectorAll('.admin-sidebar-group').forEach((group) => {
    const list = group.querySelector('.admin-sidebar-group-list');
    if (!list) return;
    const anyVisible = Array.from(list.children).some((li) => li.style.display !== 'none');
    group.style.display = anyVisible ? '' : 'none';
  });
}

/**
 * The functional counterpart to applySidebarPermissions — reuses the same
 * map so a restricted admin who navigates straight to a hidden page's URL
 * (bookmark, typed address, old link) gets a clear "Access Denied" panel
 * instead of a confusing half-loaded page full of "couldn't load" errors.
 * Returns false (and renders the notice) when the current page is off
 * limits; true otherwise. This is UX only — every underlying API call the
 * page would have made is already independently rejected by the backend's
 * own require_permission check regardless of whether this runs.
 */
function enforcePagePermission(user) {
  const required = ADMIN_PAGE_PERMISSIONS[_adminPagePathname(location.pathname)];
  const permissions = Array.isArray(user.permissions) ? user.permissions : [];
  if (!required || permissions.includes(required)) return true;

  const main = document.querySelector('.admin-main') || document.body;
  main.innerHTML = `
    <div class="admin-page-header">
      <div>
        <h1>Access Denied</h1>
        <p>You don't have permission to view this page. Contact an administrator if you believe this is a mistake.</p>
      </div>
    </div>
  `;
  return false;
}

// Any API 401 (expired/revoked token) bounces to the generic portal chooser —
// simplest option that doesn't require guessing which role's page to send them to.
ArckAPI.onUnauthorized = () => {
  window.location.href = 'login.html';
};

const ArckAuth = {
  async login(username, password) {
    const data = await ArckAPI.request('/auth/login', {
      method: 'POST',
      auth: false,
      body: { username, password },
    });
    ArckAPI.setSession(data.access_token, data.user);
    return data.user;
  },

  async logout() {
    try {
      await ArckAPI.request('/auth/logout', { method: 'POST' });
    } catch (_) {
      // best-effort — clear local session regardless
    }
    ArckAPI.clearSession();
    window.location.href = 'login.html';
  },

  async getCurrentUser() {
    return ArckAPI.request('/auth/me');
  },

  /**
   * Client-side check only — pure UX polish (hide/disable a button the
   * user isn't allowed to use). The real enforcement is server-side on
   * every endpoint via require_permission; this never gates anything the
   * backend wouldn't independently reject.
   */
  hasPermission(key) {
    const user = ArckAPI.getStoredUser();
    return !!(user && Array.isArray(user.permissions) && user.permissions.includes(key));
  },

  /**
   * Call at the top of every dashboard page. Redirects away (and returns
   * null) if the user isn't authenticated, isn't the expected role, or
   * still has a temporary password. Returns the user object otherwise.
   */
  async requireRole(expectedRole) {
    let user;
    try {
      user = await this.getCurrentUser();
    } catch (_) {
      return null; // onUnauthorized already redirected
    }

    // Refresh the cached session with this fresh /auth/me response — without
    // this, a role/permission change a Super Admin makes never shows up for
    // the affected admin until they log out and back in, because
    // hasPermission() reads the stale user object cached at login time, not
    // this fresh one. Every dashboard page calls requireRole() on load, so
    // this keeps permissions current as of the last navigation/reload.
    ArckAPI.setSession(ArckAPI.getToken(), user);

    if (user.role !== expectedRole) {
      ArckAPI.clearSession();
      window.location.href = 'login.html';
      return null;
    }

    if (user.must_change_password) {
      window.location.href = 'change-password.html';
      return null;
    }

    if (user.role === 'student' && !user.profile_completed) {
      window.location.href = 'complete-profile.html';
      return null;
    }

    if (user.role === 'admin' && document.querySelector('.admin-sidebar-nav')) {
      applySidebarPermissions(user);
      if (!enforcePagePermission(user)) return null;
    }

    return user;
  },

  redirectToRoleEntryPoint(user) {
    if (user.must_change_password) {
      window.location.href = 'change-password.html';
    } else if (user.role === 'student' && !user.program) {
      window.location.href = 'complete-profile.html';
    } else {
      window.location.href = ROLE_DASHBOARD[user.role];
    }
  },
};
