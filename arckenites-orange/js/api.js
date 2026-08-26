/* ============================================================
   ARCKENITES PORTAL — API CLIENT
   Thin fetch wrapper shared by every login/dashboard page.
   ============================================================ */

// Local dev talks to the backend directly on localhost:8000; production
// (arckenites.com) talks to the same origin, since Caddy reverse-proxies
// /api/* to the backend on 127.0.0.1:8000 (see deploy notes / Caddyfile).
const API_BASE = (['localhost', '127.0.0.1'].includes(window.location.hostname))
  ? 'http://localhost:8000/api'
  : '/api';
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

  // Multipart upload — deliberately separate from request() since a file
  // body must never be JSON.stringify'd and must NOT set its own
  // Content-Type (the browser needs to add the multipart boundary itself).
  async uploadFile(path, file, { method = 'POST' } = {}) {
    const headers = {};
    const token = this.getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const formData = new FormData();
    formData.append('file', file);

    let res;
    try {
      res = await fetch(`${API_BASE}${path}`, { method, headers, body: formData });
    } catch (_) {
      throw new ApiError(0, 'Cannot reach the Arckenites server. Check that the backend is running and try again.');
    }

    if (res.status === 401) {
      this.clearSession();
      if (this.onUnauthorized) this.onUnauthorized();
      throw new ApiError(401, 'Session expired. Please log in again.');
    }

    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      // no body
    }

    if (!res.ok) {
      throw new ApiError(res.status, (data && data.detail) || 'Upload failed.');
    }

    return data;
  },

  // Public identity everywhere else in the portal is photo + name only —
  // this is the one place that turns {full_name, photo_url} into markup, so
  // every dashboard/chat/batch view renders it the same way. photo_url (from
  // the backend) never carries a token; it's appended here because a plain
  // <img src> can't send an Authorization header the way fetch() can.
  avatarHtml(fullName, photoUrl, sizePx = 36) {
    const esc = (s) => String(s || '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const style = `width:${sizePx}px;height:${sizePx}px;`;
    if (photoUrl) {
      const token = this.getToken();
      const src = `${API_BASE}${photoUrl}${token ? `?token=${encodeURIComponent(token)}` : ''}`;
      return `<img class="ak-avatar" style="${style}" src="${esc(src)}" alt="${esc(fullName)}">`;
    }
    const initials = String(fullName || '')
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((w) => w.charAt(0).toUpperCase())
      .join('') || '?';
    return `<span class="ak-avatar ak-avatar--initials" style="${style}">${esc(initials)}</span>`;
  },
};
