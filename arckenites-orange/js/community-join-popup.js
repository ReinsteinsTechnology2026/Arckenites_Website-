/* Join-the-Community popup — shown when a logged-out visitor tries a
   member-only action. Uses its own community-join-* classes (not the admin
   .modal system, which clashes with Bootstrap's .modal on public pages) and
   never redirects: closing it leaves the visitor on the same page.
   Mark a trigger with data-requires-membership to wire it up automatically. */
(function () {
  const ICON_POINTS = [
    'fa-arrows-rotate|Community updates',
    'fa-bullhorn|Important announcements',
    'fa-bell|Notifications',
    'fa-briefcase|Community opportunities',
    'fa-id-badge|Exclusive member information',
  ];

  let backdrop = null;

  const build = () => {
    backdrop = document.createElement('div');
    backdrop.className = 'community-join-backdrop';
    backdrop.innerHTML = `
      <div class="community-join-modal" role="dialog" aria-modal="true" aria-labelledby="communityJoinTitle">
        <div class="community-join-head">
          <button type="button" class="icon-btn" data-join-close aria-label="Close">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <div class="community-join-body">
          <div class="community-join-icon"><i class="fa-solid fa-rocket"></i></div>
          <h3 id="communityJoinTitle" class="community-join-title">Join Arckenites Community</h3>
          <p style="margin-bottom:18px;">Stay connected with Arckenites.</p>
          <ul class="community-join-benefits">
            ${ICON_POINTS.map((p) => {
              const [icon, label] = p.split('|');
              return `<li><i class="fa-solid ${icon}"></i><span>${label}</span></li>`;
            }).join('')}
          </ul>
        </div>
        <div class="community-join-footer">
          <a href="community-register.html" class="btn btn-accent community-join-cta">Register Now</a>
          <a href="login.html" class="btn btn-primary-outline">Login</a>
          <p class="community-join-already">Already a member? <a href="login.html">Log in</a></p>
          <button type="button" class="btn btn-link community-join-continue" data-join-close>Continue Exploring</button>
        </div>
      </div>`;
    document.body.appendChild(backdrop);

    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop || e.target.closest('[data-join-close]')) close();
    });
  };

  const open = () => {
    if (!backdrop) build();
    backdrop.classList.add('is-open');
    const first = backdrop.querySelector('.community-join-cta');
    if (first) first.focus();
  };

  const close = () => {
    if (backdrop) backdrop.classList.remove('is-open');
  };

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') close();
  });

  // Logged-in users are never shown the popup — they already have access.
  // ArckAPI is a top-level const in api.js, so it is NOT a window property —
  // reference it directly.
  const isLoggedIn = () => typeof ArckAPI !== 'undefined' && !!ArckAPI.getToken();

  document.addEventListener('click', (e) => {
    const trigger = e.target.closest('[data-requires-membership]');
    if (!trigger) return;
    if (isLoggedIn()) return;
    e.preventDefault();
    open();
  });

  window.CommunityJoin = { open, close, isLoggedIn };
})();
