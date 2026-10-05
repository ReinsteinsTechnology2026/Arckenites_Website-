/* Public Community landing — adapts the public nav/CTA to whoever is
   viewing it. The page itself never calls a protected API; there is no
   public announcement/update data source yet, so its sections render
   honest empty states. Member-only actions are handled by
   community-join-popup.js via data-requires-membership. */
document.addEventListener('DOMContentLoaded', () => {
  // ArckAPI is a top-level const in api.js, so it is NOT a window property.
  const apiLoaded = typeof ArckAPI !== 'undefined';
  const user = apiLoaded ? ArckAPI.getStoredUser() : null;
  const hasSession = apiLoaded && !!ArckAPI.getToken();

  if (hasSession && user && user.role === 'community') {
    const actions = document.getElementById('communityNavActions');
    if (actions) {
      actions.innerHTML = '<a href="community-dashboard.html" class="btn btn-accent">Go to my dashboard</a>';
    }
    const hero = document.getElementById('communityHeroCta');
    if (hero) {
      hero.textContent = 'Go to my dashboard';
      hero.setAttribute('href', 'community-dashboard.html');
    }
  }
});
