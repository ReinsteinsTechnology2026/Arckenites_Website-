/* ============================================================
   ARCKENITES PORTAL — LIGHT/DARK THEME TOGGLE
   The actual theme (which CSS variables apply) is set instantly by the
   inline snippet at the top of <head> on every portal page, to avoid a
   flash of the wrong theme. This file only wires up the visible button(s)
   once the DOM is ready.
   ============================================================ */

(function () {
  const STORAGE_KEY = 'ak_theme';
  const root = document.documentElement;

  function applyButtons(theme) {
    document.querySelectorAll('.theme-toggle-btn').forEach((btn) => {
      const icon = btn.querySelector('i');
      if (icon) icon.className = theme === 'light' ? 'fa-solid fa-moon' : 'fa-solid fa-sun';
      const label = theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme';
      btn.setAttribute('aria-label', label);
      btn.title = label;
      const textLabel = btn.querySelector('.label');
      if (textLabel) textLabel.textContent = theme === 'light' ? 'Dark Mode' : 'Light Mode';
    });
  }

  function setTheme(theme) {
    root.setAttribute('data-theme', theme);
    try { localStorage.setItem(STORAGE_KEY, theme); } catch (_) {}
    applyButtons(theme);
  }

  function toggleTheme() {
    setTheme(root.getAttribute('data-theme') === 'light' ? 'dark' : 'light');
  }

  document.addEventListener('DOMContentLoaded', () => {
    applyButtons(root.getAttribute('data-theme') || 'dark');
    document.querySelectorAll('.theme-toggle-btn').forEach((btn) => {
      btn.addEventListener('click', toggleTheme);
    });
  });

  window.ArckTheme = { setTheme, toggleTheme };
})();
