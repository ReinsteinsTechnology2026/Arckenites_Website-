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
