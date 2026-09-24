document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('community');
  if (!user) return; // requireRole already redirected

  /* ---------- Profile ---------- */
  const initials = user.full_name.split(' ').filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || 'C';
  document.getElementById('communityAvatarInitials').textContent = initials;
  document.getElementById('communityProfileName').textContent = user.full_name;
  document.getElementById('communityWelcomeName').textContent = user.full_name;

  /* ---------- Sidebar: mobile off-canvas ---------- */
  const sidebar = document.getElementById('communitySidebar');
  const backdrop = document.getElementById('adminSidebarBackdrop');
  const openMobileSidebar = () => { sidebar.classList.add('is-mobile-open'); backdrop.classList.add('is-visible'); };
  const closeMobileSidebar = () => { sidebar.classList.remove('is-mobile-open'); backdrop.classList.remove('is-visible'); };
  document.getElementById('adminMobileToggle').addEventListener('click', openMobileSidebar);
  backdrop.addEventListener('click', closeMobileSidebar);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMobileSidebar(); });

  /* ---------- Profile dropdown ---------- */
  const profileTrigger = document.getElementById('communityProfileTrigger');
  const profilePanel = document.getElementById('communityProfilePanel');
  profileTrigger.addEventListener('click', (e) => { e.stopPropagation(); profilePanel.classList.toggle('is-open'); });
  document.addEventListener('click', () => profilePanel.classList.remove('is-open'));

  /* ---------- Logout ---------- */
  document.getElementById('communitySidebarLogout').addEventListener('click', () => ArckAuth.logout());
  document.getElementById('communityProfileLogout').addEventListener('click', () => ArckAuth.logout());

  /* ---------- Panel switching ---------- */
  const navButtons = sidebar.querySelectorAll('.admin-sidebar-link[data-panel]');
  const panels = document.querySelectorAll('main.admin-main > section[data-panel]');
  navButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      navButtons.forEach((b) => b.classList.toggle('is-active', b === btn));
      panels.forEach((p) => { p.style.display = p.dataset.panel === btn.dataset.panel ? 'block' : 'none'; });
      closeMobileSidebar();
    });
  });

});
