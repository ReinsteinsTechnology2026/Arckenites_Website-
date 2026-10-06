/* Fills the Announcements and Updates cards with real published posts from
   the backend. Logged-in community members get the member feed (which also
   includes members-only posts); everyone else gets the public feed.
   Empty states stay as they are in the HTML when there is nothing to show,
   and a clear error replaces them if the request fails — never a fake
   "no announcements" when the truth is "we could not load them". All post
   text is inserted with textContent, never as markup. */
(function () {
  const SECTIONS = { announcement: 'announcements', update: 'updates' };

  const formatDate = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  };

  const renderList = (card, items) => {
    const empty = card.querySelector('.community-empty');
    if (!empty) return;
    const list = document.createElement('div');
    list.className = 'cc-post-list';
    items.forEach((item) => {
      const article = document.createElement('article');
      article.className = 'cc-post';

      const title = document.createElement('h3');
      title.textContent = item.title;
      article.appendChild(title);

      const body = document.createElement('p');
      body.textContent = item.body;
      article.appendChild(body);

      const meta = document.createElement('div');
      meta.className = 'cc-post-meta';
      const date = document.createElement('span');
      date.textContent = formatDate(item.published_at);
      meta.appendChild(date);
      if (item.visibility === 'members_only') {
        const badge = document.createElement('span');
        badge.className = 'cc-badge';
        badge.textContent = 'Members only';
        meta.appendChild(badge);
      }
      article.appendChild(meta);
      list.appendChild(article);
    });
    empty.replaceWith(list);
  };

  const renderError = (card) => {
    const empty = card.querySelector('.community-empty');
    if (!empty) return;
    const box = document.createElement('div');
    box.className = 'community-empty';
    box.innerHTML = '<strong>We couldn’t load this right now</strong><p>Please refresh the page to try again.</p>';
    empty.replaceWith(box);
  };

  document.addEventListener('DOMContentLoaded', async () => {
    // ArckAPI is a top-level const in api.js, so it is NOT a window property.
    const apiLoaded = typeof ArckAPI !== 'undefined';
    const user = apiLoaded ? ArckAPI.getStoredUser() : null;
    const isMember = apiLoaded && !!ArckAPI.getToken() && !!user && user.role === 'community';
    const path = isMember ? '/community/member/posts' : '/community/posts';

    const cards = Object.values(SECTIONS).map((id) => document.getElementById(id)).filter(Boolean);
    if (!apiLoaded || cards.length === 0) return;

    let items;
    try {
      items = await ArckAPI.request(path, { auth: isMember });
    } catch (_) {
      cards.forEach(renderError);
      return;
    }

    Object.entries(SECTIONS).forEach(([kind, id]) => {
      const card = document.getElementById(id);
      if (!card) return;
      const matching = (items || []).filter((p) => p.kind === kind);
      if (matching.length) renderList(card, matching);
    });
  });
})();
