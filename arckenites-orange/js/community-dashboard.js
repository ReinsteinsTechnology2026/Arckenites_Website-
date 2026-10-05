document.addEventListener('DOMContentLoaded', async () => {

  // Gates the page on the server-confirmed 'community' role before anything
  // becomes visible (see auth.js requireRole / the auth-pending class).
  const user = await ArckAuth.requireRole('community');
  if (!user) return;

  const firstName = (user.full_name || '').trim().split(/\s+/)[0] || 'there';
  const initials = (user.full_name || '').split(' ').filter(Boolean).slice(0, 2)
    .map((w) => w[0].toUpperCase()).join('') || 'C';

  document.getElementById('communityAvatarInitials').textContent = initials;
  document.getElementById('communityProfileName').textContent = user.full_name;
  document.getElementById('communityWelcome').innerHTML =
    `Welcome back, ${escapeText(firstName)}! <span aria-hidden="true">👋</span>`;

  // Real account data only — no counts exist for announcements/updates/notifications yet.
  document.getElementById('communityStatus').textContent = user.is_active ? 'Active' : 'Inactive';
  // Never render the address itself on the dashboard; only whether one is on file.
  document.getElementById('communityEmailStatus').textContent = user.email ? 'Verified' : 'Not on file';
  document.getElementById('communityMembership').textContent = 'Community Member';

  /* ---------- Sidebar: mobile off-canvas ---------- */
  const sidebar = document.getElementById('communitySidebar');
  const backdrop = document.getElementById('adminSidebarBackdrop');
  const openMobileSidebar = () => { sidebar.classList.add('is-mobile-open'); backdrop.classList.add('is-visible'); };
  const closeMobileSidebar = () => { sidebar.classList.remove('is-mobile-open'); backdrop.classList.remove('is-visible'); };
  document.getElementById('adminMobileToggle').addEventListener('click', openMobileSidebar);
  backdrop.addEventListener('click', closeMobileSidebar);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMobileSidebar(); });

  /* ---------- Sidebar: in-page section links ---------- */
  const anchorLinks = sidebar.querySelectorAll('[data-anchor]');
  const setActive = (name) => {
    anchorLinks.forEach((a) => a.classList.toggle('is-active', a.dataset.anchor === name));
  };
  // A clicked link stays highlighted until the visitor scrolls themselves.
  // Without this, a section that cannot reach the reading band (e.g. the
  // last row when the page is at its bottom) is never highlighted after click.
  let lockedAnchor = null;
  const releaseLock = () => { lockedAnchor = null; };
  ['wheel', 'touchmove', 'keydown'].forEach((evt) => window.addEventListener(evt, releaseLock, { passive: true }));

  anchorLinks.forEach((link) => {
    link.addEventListener('click', () => {
      lockedAnchor = link.dataset.anchor;
      setActive(lockedAnchor);
      closeMobileSidebar();
    });
  });

  // Keep the highlighted nav item in step with the section being read.
  const sections = ['overview', 'announcements', 'updates', 'notifications']
    .map((id) => document.getElementById(id))
    .filter(Boolean);
  if ('IntersectionObserver' in window && sections.length) {
    const observer = new IntersectionObserver((entries) => {
      if (lockedAnchor) return;
      entries.forEach((entry) => { if (entry.isIntersecting) setActive(entry.target.id); });
    }, { rootMargin: '-40% 0px -55% 0px' });
    sections.forEach((s) => observer.observe(s));
  }

  /* ---------- Chats (the widget itself is driven by js/chat.js) ---------- */
  document.querySelectorAll('[data-open-chat]').forEach((btn) => {
    btn.addEventListener('click', () => {
      closeMobileSidebar();
      document.getElementById('chatFloatBtn')?.click();
    });
  });

  /* ---------- Profile menu ---------- */
  const profileTrigger = document.getElementById('communityProfileTrigger');
  const profilePanel = document.getElementById('communityProfilePanel');
  profileTrigger.addEventListener('click', (e) => { e.stopPropagation(); profilePanel.classList.toggle('is-open'); });
  document.addEventListener('click', () => profilePanel.classList.remove('is-open'));

  /* ---------- Logout ---------- */
  document.getElementById('communitySidebarLogout').addEventListener('click', () => ArckAuth.logout());
  document.getElementById('communityProfileLogout').addEventListener('click', () => ArckAuth.logout());
});

function escapeText(str) {
  return String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
