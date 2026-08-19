/* ============================================================
   ARCKENITES PORTAL — API CLIENT
   Thin fetch wrapper shared by every login/dashboard page.
   ============================================================ */

// Local dev talks to the backend on localhost:8000; anywhere else (Vercel,
// custom domain, etc.) talks to the deployed Render backend. Update the
// production URL below once the Render service is live.
const API_BASE = (['localhost', '127.0.0.1'].includes(window.location.hostname))
  ? 'http://localhost:8000/api'
  : 'https://arckenites-backend.onrender.com/api';
const TOKEN_KEY = 'ak_token';
const USER_KEY = 'ak_user';

class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

const ArckAPI = {
  onUnauthorized: null, // set by auth.js

  getToken() {
    return sessionStorage.getItem(TOKEN_KEY);
  },

  setSession(token, user) {
    sessionStorage.setItem(TOKEN_KEY, token);
    sessionStorage.setItem(USER_KEY, JSON.stringify(user));
  },

  getStoredUser() {
    const raw = sessionStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  },

  clearSession() {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
  },

  async request(path, { method = 'GET', body, auth = true } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (auth) {
      const token = this.getToken();
      if (token) headers['Authorization'] = `Bearer ${token}`;
    }

    let res;
    try {
      res = await fetch(`${API_BASE}${path}`, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (_) {
      // fetch() throws on network failure, CORS rejection, DNS failure, server
      // down, etc. — distinct from a real 4xx/5xx API response, and must never
      // be shown to the user as a wrong-username/password error.
      throw new ApiError(0, 'Cannot reach the Arckenites server. Check that the backend is running and try again.');
    }

    if (res.status === 401 && auth) {
      this.clearSession();
      if (this.onUnauthorized) this.onUnauthorized();
      throw new ApiError(401, 'Session expired. Please log in again.');
    }

    if (res.status === 204) return null;

    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      // no body
    }

    if (!res.ok) {
      throw new ApiError(res.status, (data && data.detail) || 'Something went wrong.');
    }

    return data;
  },
};
