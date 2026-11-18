/* ============================================================
   ARCKENITES PORTAL — SHARED SHELL BEHAVIOUR
   One implementation of the sidebar, mobile drawer and topbar
   dropdown behaviour used by every admin page, the student
   dashboard and the community dashboard. Page scripts must NOT
   re-implement any of this (it used to be copied into 22 files,
   which risks double-execution).

   - Desktop (>= 992px): sidebar can collapse to an icon rail.
   - Below 992px: sidebar is an off-canvas drawer, always expanded
     (collapse state is ignored by CSS), hidden from the keyboard
     and assistive tech while closed, focus-trapped while open,
     closes on Escape/backdrop/navigation and returns focus.
   - Dropdowns (.admin-dropdown): aria-expanded, Escape, outside
     click, focus return.
   ============================================================ */
(function () {
  'use strict';

  var DRAWER_QUERY = '(max-width: 991.98px)';
  var COLLAPSE_KEY = 'ak_sidebar_collapsed';
  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  var mq = window.matchMedia(DRAWER_QUERY);
  var sidebar, backdrop, toggle, collapseBtn;
  var drawerOpener = null;
  var uid = 0;

  function store(op, key, val) {
    try { return op === 'get' ? window.localStorage.getItem(key) : window.localStorage.setItem(key, val); } catch (_) { return null; }
  }

  function isDrawerOpen() {
    return !!sidebar && sidebar.classList.contains('is-mobile-open');
  }

  function visibleFocusables() {
    return Array.prototype.filter.call(sidebar.querySelectorAll(FOCUSABLE), function (el) {
      return el.offsetParent !== null || el === document.activeElement;
    });
  }

  /* Keep inert/aria in step with the viewport and the drawer state. */
  function syncDrawerState() {
    if (!sidebar) return;
    var open = isDrawerOpen();
    if (mq.matches) {
      // Closed drawer: not focusable, not announced.
      if (open) { sidebar.removeAttribute('inert'); sidebar.setAttribute('role', 'dialog'); sidebar.setAttribute('aria-modal', 'true'); }
      else { sidebar.setAttribute('inert', ''); sidebar.removeAttribute('role'); sidebar.removeAttribute('aria-modal'); }
      if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    } else {
      // Desktop: sidebar is a normal landmark; drawer state must not linger.
      sidebar.classList.remove('is-mobile-open');
      if (backdrop) backdrop.classList.remove('is-visible');
      sidebar.removeAttribute('inert');
      sidebar.removeAttribute('role');
      sidebar.removeAttribute('aria-modal');
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
      document.documentElement.classList.remove('drawer-open');
    }
  }

  function openDrawer() {
    if (!sidebar || !mq.matches || isDrawerOpen()) return;
    drawerOpener = document.activeElement;
    sidebar.classList.add('is-mobile-open');
    if (backdrop) backdrop.classList.add('is-visible');
    document.documentElement.classList.add('drawer-open');
    syncDrawerState();
    var first = sidebar.querySelector('.admin-sidebar-link.is-active, .admin-sidebar-link[aria-current="page"]') || visibleFocusables()[0];
    if (first && first.focus) first.focus();
  }

  function closeDrawer(opts) {
    if (!sidebar) return;
    var wasOpen = isDrawerOpen();
    sidebar.classList.remove('is-mobile-open');
    if (backdrop) backdrop.classList.remove('is-visible');
    document.documentElement.classList.remove('drawer-open');
    var focusWasInside = sidebar.contains(document.activeElement);
    syncDrawerState();
    if (wasOpen && (!opts || opts.returnFocus !== false) && (focusWasInside || (opts && opts.forceReturn))) {
      var target = drawerOpener && document.contains(drawerOpener) ? drawerOpener : toggle;
      if (target && target.focus) target.focus();
    }
    drawerOpener = null;
  }

  function trapTab(e) {
    if (e.key !== 'Tab' || !isDrawerOpen()) return;
    var items = visibleFocusables();
    if (!items.length) return;
    var first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    else if (!sidebar.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  }

  /* ---------------- Desktop collapse (icon rail) ---------------- */
  function applyCollapsed(collapsed) {
    if (!sidebar) return;
    sidebar.classList.toggle('is-collapsed', collapsed);
    if (collapseBtn) {
      collapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      var label = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
      collapseBtn.setAttribute('aria-label', label);
      collapseBtn.setAttribute('title', label);
    }
  }

  /* Icon-only rail needs real accessible names on every nav item. */
  function nameNavItems() {
    sidebar.querySelectorAll('.admin-sidebar-link').forEach(function (el) {
      if (el.hasAttribute('aria-label')) return;
      var label = el.querySelector('.label');
      var text = label ? label.textContent.trim() : '';
      if (!text) return;
      var title = el.getAttribute('title') || '';
      el.setAttribute('aria-label', /coming soon/i.test(title) ? title : text);
    });
    sidebar.querySelectorAll('.admin-profile-trigger').forEach(function (el) {
      if (!el.hasAttribute('aria-label')) {
        var l = el.querySelector('.label');
        if (l) el.setAttribute('aria-label', l.textContent.trim());
      }
    });
  }

  function initGroups() {
    sidebar.querySelectorAll('.admin-sidebar-group').forEach(function (group) {
      var header = group.querySelector('.admin-sidebar-group-header');
      var list = group.querySelector('.admin-sidebar-group-list');
      if (!header) return;
      if (list && !list.id) { list.id = 'sidebar-group-' + (++uid); }
      if (list) header.setAttribute('aria-controls', list.id);
      header.setAttribute('aria-expanded', group.classList.contains('is-open') ? 'true' : 'false');
      header.addEventListener('click', function () {
        var isOpen = group.classList.toggle('is-open');
        header.setAttribute('aria-expanded', String(isOpen));
      });
    });
  }

  /* ---------------- Topbar / sidebar dropdowns ---------------- */
  function initDropdowns() {
    var dropdowns = [];
    document.querySelectorAll('.admin-dropdown').forEach(function (root) {
      var panel = root.querySelector('.admin-dropdown-panel');
      var trigger = root.querySelector('button');
      if (!panel || !trigger) return;
      if (!panel.id) panel.id = 'dropdown-panel-' + (++uid);
      trigger.setAttribute('aria-haspopup', 'true');
      trigger.setAttribute('aria-expanded', 'false');
      trigger.setAttribute('aria-controls', panel.id);
      dropdowns.push({ root: root, trigger: trigger, panel: panel });
    });
    if (!dropdowns.length) return;

    function close(d, returnFocus) {
      d.panel.classList.remove('is-open');
      d.trigger.setAttribute('aria-expanded', 'false');
      if (returnFocus) d.trigger.focus();
    }
    function closeAll() { dropdowns.forEach(function (d) { close(d, false); }); }

    dropdowns.forEach(function (d) {
      d.trigger.addEventListener('click', function (e) {
        e.stopPropagation();
        var willOpen = !d.panel.classList.contains('is-open');
        closeAll();
        if (willOpen) { d.panel.classList.add('is-open'); d.trigger.setAttribute('aria-expanded', 'true'); }
      });
    });
    document.addEventListener('click', function (e) {
      dropdowns.forEach(function (d) { if (!d.panel.contains(e.target)) close(d, false); });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      dropdowns.forEach(function (d) {
        if (d.panel.classList.contains('is-open')) {
          var inside = d.panel.contains(document.activeElement) || d.root.contains(document.activeElement);
          close(d, inside);
        }
      });
    });
    // Tabbing out of an open panel closes it.
    document.addEventListener('focusin', function (e) {
      dropdowns.forEach(function (d) {
        if (d.panel.classList.contains('is-open') && !d.root.contains(e.target)) close(d, false);
      });
    });
  }

  function init() {
    sidebar = document.querySelector('.admin-sidebar');
    backdrop = document.getElementById('adminSidebarBackdrop');
    toggle = document.getElementById('adminMobileToggle');
    collapseBtn = document.getElementById('adminSidebarCollapseBtn');

    initDropdowns();
    if (!sidebar) return;

    if (!sidebar.id) sidebar.id = 'portalSidebar';
    nameNavItems();
    initGroups();

    if (toggle) {
      toggle.setAttribute('aria-controls', sidebar.id);
      toggle.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-haspopup', 'dialog');
      toggle.addEventListener('click', function () { isDrawerOpen() ? closeDrawer() : openDrawer(); });
    }
    if (backdrop) backdrop.addEventListener('click', function () { closeDrawer({ returnFocus: true, forceReturn: true }); });

    if (collapseBtn) {
      collapseBtn.setAttribute('aria-controls', sidebar.id);
      applyCollapsed(store('get', COLLAPSE_KEY) === '1');
      collapseBtn.addEventListener('click', function () {
        var next = !sidebar.classList.contains('is-collapsed');
        applyCollapsed(next);
        store('set', COLLAPSE_KEY, next ? '1' : '0');
      });
    }

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && isDrawerOpen()) { e.preventDefault(); closeDrawer({ returnFocus: true, forceReturn: true }); return; }
      trapTab(e);
    });

    // Navigating via a drawer link (or an in-page panel switch) closes the drawer.
    sidebar.addEventListener('click', function (e) {
      var link = e.target.closest('a.admin-sidebar-link, button.admin-sidebar-link[data-panel], button.admin-sidebar-link[data-anchor]');
      if (link && isDrawerOpen()) closeDrawer();
    });

    var onChange = function () { if (!mq.matches) closeDrawer({ returnFocus: false }); syncDrawerState(); };
    if (mq.addEventListener) mq.addEventListener('change', onChange); else mq.addListener(onChange);
    syncDrawerState();
  }

  window.PortalShell = { openDrawer: openDrawer, closeDrawer: closeDrawer, isDrawerOpen: isDrawerOpen };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
