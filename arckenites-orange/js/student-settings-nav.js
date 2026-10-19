/* Client-side Settings navigation. Existing feature panels remain mounted so
   forms, MFA controls and student data loaders keep their original behavior. */
const StudentSettingsNav = (() => {
  const groups = {
    profile: ['personal-info', 'profile-photo', 'student-id-card', 'my-enrollments', 'my-certificates'],
    security: ['mfa-settings', 'active-sessions', 'login-history',
      'privacy-settings', 'data-export', 'deactivate-account'],
  };
  const categoryNames = {profile: 'Personal Profile', security: 'Privacy & Security', contact: 'Contact & Email'};
  const detailNames = {
    'personal-info': 'Personal Information', 'profile-photo': 'Profile Photo',
    'student-id-card': 'Student ID Card', 'my-enrollments': 'My Enrollments',
    'my-certificates': 'Certificates & Achievements',
    'mfa-settings': 'Two-Factor Authentication', 'active-sessions': 'Active Devices & Sessions',
    'login-history': 'Login History', 'privacy-settings': 'Privacy Settings',
    'data-export': 'Download My Data', 'deactivate-account': 'Deactivate Account',
  };
  let root;
  let callbacks;
  let currentView = null;
  let previousPanel = 'overview';

  const categoryFor = (detail) => Object.keys(groups).find((key) => groups[key].includes(detail));
  const viewFromHash = () => {
    const parts = window.location.hash.slice(1).split('/');
    if (parts[0] !== 'settings') return null;
    if (parts.length === 1) return 'menu';
    if (parts.length === 2 && categoryNames[parts[1]]) return parts[1];
    if (parts.length === 3 && categoryFor(parts[2]) === parts[1]) return `detail:${parts[2]}`;
    return 'menu';
  };
  const routeFor = (view) => {
    if (view === 'menu') return '#settings';
    if (categoryNames[view]) return `#settings/${view}`;
    const detail = view.slice('detail:'.length);
    return `#settings/${categoryFor(detail)}/${detail}`;
  };
  const urlWithHash = (hash) => {
    const url = new URL(window.location.href);
    url.hash = hash;
    return url.pathname + url.search + url.hash;
  };

  const render = (view, push = true) => {
    const detail = view.startsWith('detail:') ? view.slice('detail:'.length) : null;
    const category = detail ? categoryFor(detail) : (view === 'menu' ? null : view);
    if ((detail && !category) || (!detail && view !== 'menu' && !categoryNames[view])) return;
    root.querySelectorAll('[data-settings-view]').forEach((element) => {
      element.hidden = element.dataset.settingsView !== view;
    });
    const title = detail ? detailNames[detail] : (category ? categoryNames[category] : 'Settings');
    const heading = root.querySelector('#settingsPageTitle');
    heading.textContent = title;
    root.querySelector('#settingsPageDescription').textContent = detail
      ? `Manage ${title.toLowerCase()} in your ${categoryNames[category]} settings.`
      : category ? `Choose a ${categoryNames[category].toLowerCase()} option.`
        : 'Choose what you want to manage.';
    const back = root.querySelector('#settingsBackBtn');
    const home = root.querySelector('#settingsHomeBtn');
    const active = root.querySelector('#settingsActiveCategory');
    back.hidden = view === 'menu';
    back.querySelector('span').textContent = detail ? `Back to ${categoryNames[category]}` : 'Back to Settings';
    home.hidden = !detail;
    active.hidden = !category;
    active.textContent = category ? categoryNames[category] : '';
    root.dataset.activeSettingsView = view;
    currentView = view;
    callbacks.showSettingsPanel();
    if (push) {
      window.history.pushState({studentSettingsView: view, previousPanel}, '', urlWithHash(routeFor(view)));
    }
    root.scrollIntoView({block: 'start'});
    heading.focus({preventScroll: true});
  };

  const openMenu = () => {
    if (currentView === null) previousPanel = callbacks.getDashboardPanel();
    if (currentView === 'menu' && window.location.hash === '#settings') return;
    render('menu');
  };
  const openCategory = (category) => {
    if (!categoryNames[category]) return;
    if (currentView === null) previousPanel = callbacks.getDashboardPanel();
    render(category);
  };
  const openDetail = (detail) => {
    if (!categoryFor(detail)) return;
    render(`detail:${detail}`);
  };
  const leaveToPanel = (panel) => {
    if (currentView !== null || viewFromHash()) {
      window.history.pushState({studentPanel: panel}, '', urlWithHash(''));
      currentView = null;
      root.querySelectorAll('[data-settings-view]').forEach((element) => { element.hidden = true; });
    }
  };
  const back = () => {
    if (window.history.state?.studentSettingsView === currentView) {
      window.history.back();
    } else if (currentView?.startsWith('detail:')) {
      render(categoryFor(currentView.slice('detail:'.length)));
    } else {
      render('menu');
    }
  };

  const init = (options) => {
    callbacks = options;
    root = document.querySelector('section[data-panel="settings"]');
    const notice = window.sessionStorage?.getItem('ak_student_settings_notice');
    if (notice) {
      root.querySelector('#settingsNotice').textContent = notice;
      root.querySelector('#settingsNotice').hidden = false;
      window.sessionStorage.removeItem('ak_student_settings_notice');
    }
    root.addEventListener('click', (event) => {
      const category = event.target.closest('[data-settings-category]');
      if (category) { openCategory(category.dataset.settingsCategory); return; }
      const detail = event.target.closest('[data-settings-detail]');
      if (detail) { openDetail(detail.dataset.settingsDetail); return; }
      if (event.target.closest('[data-settings-action="logout"]')) { callbacks.logout(); return; }
      if (event.target.closest('#settingsBackBtn')) { back(); return; }
      if (event.target.closest('#settingsHomeBtn')) openMenu();
    });
    window.addEventListener('popstate', (event) => {
      const view = viewFromHash();
      if (view) {
        previousPanel = event.state?.previousPanel || previousPanel;
        render(view, false);
      } else {
        currentView = null;
        root.querySelectorAll('[data-settings-view]').forEach((element) => { element.hidden = true; });
        callbacks.restoreDashboardPanel(event.state?.studentPanel || previousPanel);
      }
    });
    const initial = viewFromHash();
    if (initial) {
      previousPanel = window.history.state?.previousPanel || previousPanel;
      render(initial, false);
    }
  };

  return {init, openMenu, openCategory, openDetail, leaveToPanel, viewFromHash};
})();
