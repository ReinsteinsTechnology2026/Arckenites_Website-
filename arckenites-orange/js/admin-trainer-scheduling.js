const { esc, time12, fmtDate, fmtDateTime, newKey, toast, modal, confirmDialog, conflictList } = SchedUI;

const ACTION_LABEL = { assigned: 'Assigned', changed: 'Changed', unassigned: 'Removed' };
const SLOT_BADGE = { free: ['is-success', 'Free in slot'], booked: ['is-info', 'Booked in slot'], unavailable: ['is-danger', 'Unavailable in slot'], not_declared: ['is-muted', 'Not declared'] };
const NOTICE_LABEL = { no_suitable_trainer: 'No suitable trainer', availability_review: 'Availability change needs review' };

document.addEventListener('DOMContentLoaded', async () => {
  const user = await ArckAuth.requireRole('admin');
  if (!user) return; // requireRole already redirected or rendered Access Denied

  /* ---------- Shell: profile, sidebar (identical to the other admin pages) ---------- */
  const initials = user.full_name.split(' ').filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || 'A';
  document.getElementById('adminAvatarInitials').textContent = initials;
  document.getElementById('adminProfileName').textContent = user.full_name;
  document.getElementById('adminProfileRole').textContent = user.role;
  document.querySelectorAll('.admin-sidebar-group-header').forEach((header) => header.addEventListener('click', () => {
    const open = header.closest('.admin-sidebar-group').classList.toggle('is-open');
    header.setAttribute('aria-expanded', String(open));
  }));
  const sidebar = document.getElementById('adminSidebar');
  document.getElementById('adminSidebarCollapseBtn').addEventListener('click', () => sidebar.classList.toggle('is-collapsed'));
  const backdrop = document.getElementById('adminSidebarBackdrop');
  document.getElementById('adminMobileToggle').addEventListener('click', () => { sidebar.classList.add('is-mobile-open'); backdrop.classList.add('is-visible'); });
  const closeSide = () => { sidebar.classList.remove('is-mobile-open'); backdrop.classList.remove('is-visible'); };
  backdrop.addEventListener('click', closeSide);
  const profilePanel = document.getElementById('adminProfilePanel');
  document.getElementById('adminProfileTrigger').addEventListener('click', (e) => { e.stopPropagation(); profilePanel.classList.toggle('is-open'); });
  document.addEventListener('click', () => profilePanel.classList.remove('is-open'));
  document.getElementById('adminSidebarLogout').addEventListener('click', () => ArckAuth.logout());
  document.getElementById('adminProfileLogout').addEventListener('click', () => ArckAuth.logout());

  const canManage = ArckAuth.hasPermission('scheduling.manage');
  const canOverride = ArckAuth.hasPermission('scheduling.override');
  const banner = document.getElementById('adminServerBanner');
  const API = '/admin/trainer-scheduling';
  const $ = (id) => document.getElementById(id);
  const fail = (err) => { if (err && err.status === 0) banner.classList.add('is-visible'); return ArckAPI.errorMessage(err); };
  const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== '' && v !== null && v !== undefined)).toString();
  $('legend').innerHTML = SchedCal.legendHtml();
  $('cmpLegend').innerHTML = SchedCal.legendHtml();

  /* ---------- Tabs ---------- */
  const tabs = { tabCalendars: 'paneCalendars', tabBatches: 'paneBatches', tabHistory: 'paneHistory' };
  const showTab = (id) => {
    Object.entries(tabs).forEach(([t, p]) => { $(t).setAttribute('aria-selected', String(t === id)); $(p).hidden = t !== id; });
    if (id === 'tabBatches' && !batchState.loaded) loadBatches();
    if (id === 'tabHistory') loadHistory();
  };
  Object.keys(tabs).forEach((t) => $(t).addEventListener('click', () => showTab(t)));
  document.querySelector('.sched-tabs').addEventListener('keydown', (e) => {
    const ids = Object.keys(tabs); const i = ids.indexOf(document.activeElement.id);
    if (i < 0 || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const next = ids[(i + (e.key === 'ArrowRight' ? 1 : ids.length - 1)) % ids.length]; $(next).focus(); showTab(next);
  });

  /* ---------- Alerts ---------- */
  async function loadAlerts() {
    try {
      const rows = await ArckAPI.request(`${API}/notices?status=open`);
      $('alertsPanel').style.display = rows.length ? 'block' : 'none';
      $('alertsList').innerHTML = rows.map((n) => `<li><b>${esc(NOTICE_LABEL[n.kind] || n.kind)}</b>${n.trainer_name ? ` · ${esc(n.trainer_name)}` : ''}${n.batch_name ? ` · ${esc(n.batch_name)}` : ''}<br>
        ${esc(n.message)}<br><span class="muted">${fmtDateTime(n.created_at)}</span>
        <div class="sched-row-actions">
          ${n.batch_id ? `<button type="button" class="sched-btn" data-open-batch="${n.batch_id}">Open batch</button>` : ''}
          ${n.trainer_id ? `<button type="button" class="sched-btn" data-open-trainer="${n.trainer_id}">View calendar</button>` : ''}
          ${canManage ? `<button type="button" class="sched-btn" data-resolve="${n.id}">Mark resolved</button>` : ''}</div></li>`).join('');
    } catch (err) { toast(fail(err), true); }
  }
  $('alertsList').addEventListener('click', async (e) => {
    const resolve = e.target.closest('[data-resolve]'); const ob = e.target.closest('[data-open-batch]'); const ot = e.target.closest('[data-open-trainer]');
    if (resolve) { try { await ArckAPI.request(`${API}/notices/${resolve.dataset.resolve}/resolve`, { method: 'POST' }); toast('Marked as resolved.'); loadAlerts(); } catch (err) { toast(fail(err), true); } }
    if (ob) { showTab('tabBatches'); selectBatch(Number(ob.dataset.openBatch)); }
    if (ot) { showTab('tabCalendars'); selectTrainer(Number(ot.dataset.openTrainer)); }
  });

  /* ---------- Trainer list ---------- */
  const tState = { page: 1, total: 0, pageSize: 20, items: [], selected: null, compare: new Set(), programs: [] };
  const filters = () => ({
    search: $('fSearch').value.trim(), program_id: $('fProgram').value, start: $('fFrom').value, end: $('fTo').value,
    slot_date: $('fSlotDate').value, slot_start: $('fSlotStart').value, slot_end: $('fSlotEnd').value,
    availability: $('fAvail').value, min_free_hours: $('fMinFree').value,
  });

  async function loadTrainers() {
    $('trainerList').innerHTML = '<li class="muted">Loading…</li>';
    try {
      const f = filters();
      if (f.availability === 'any') { delete f.slot_date; delete f.slot_start; delete f.slot_end; }
      if (f.availability !== 'any' && !(f.slot_date && f.slot_start && f.slot_end)) { toast('Choose a date, start and end time to filter by availability.', true); $('trainerList').innerHTML = ''; return; }
      const data = await ArckAPI.request(`${API}/trainers?${qs({ ...f, page: tState.page, page_size: tState.pageSize })}`);
      tState.items = data.items; tState.total = data.total;
      $('tzNote').textContent = `Times are shown in ${data.timezone}.`;
      if (!$('fFrom').value) { $('fFrom').value = data.period_start; $('fTo').value = data.period_end; }
      $('trainerList').innerHTML = data.items.length ? data.items.map((t) => {
        const [badge, label] = SLOT_BADGE[t.slot_status] || [];
        return `<li><div style="display:flex;gap:10px;align-items:flex-start;">
          <input type="checkbox" data-cmp="${t.id}" ${tState.compare.has(t.id) ? 'checked' : ''} aria-label="Compare ${esc(t.full_name)}" style="margin-top:6px;accent-color:var(--primary);">
          <button type="button" class="sched-trainer" data-pick="${t.id}" ${tState.selected === t.id ? 'aria-current="true"' : ''}>
            <span>${ArckAPI.avatarHtml(t.full_name, t.photo_url, 34)}</span>
            <span><b>${esc(t.full_name)}</b><br>
              <span class="muted">${t.assigned_hours} h booked · ${t.free_hours} h free · ${t.batch_count} batch${t.batch_count === 1 ? '' : 'es'}</span><br>
              ${t.programs.length ? t.programs.map((p) => `<span class="admin-activity-badge is-muted">${esc(p.name)}</span>`).join(' ') : '<span class="muted">No course expertise recorded</span>'}
              ${t.has_availability ? '' : '<br><span class="admin-activity-badge is-danger">No availability declared</span>'}
              ${label ? `<br><span class="admin-activity-badge ${badge}">${label}</span>` : ''}</span></button></div></li>`;
      }).join('') : '<li class="muted">No trainers match these filters.</li>';
      const pages = Math.max(1, Math.ceil(data.total / data.page_size));
      $('trPage').textContent = `Page ${data.page} of ${pages} · ${data.total} trainer${data.total === 1 ? '' : 's'}`;
      $('trPrev').disabled = data.page <= 1; $('trNext').disabled = data.page >= pages;
    } catch (err) { $('trainerList').innerHTML = '<li class="muted">Couldn\'t load trainers.</li>'; toast(fail(err), true); }
  }
  const reloadTrainers = debounce(() => { tState.page = 1; loadTrainers(); });
  ['fSearch', 'fMinFree'].forEach((id) => $(id).addEventListener('input', reloadTrainers));
  ['fProgram', 'fFrom', 'fTo', 'fSlotDate', 'fSlotStart', 'fSlotEnd', 'fAvail'].forEach((id) => $(id).addEventListener('change', reloadTrainers));
  $('trPrev').addEventListener('click', () => { tState.page -= 1; loadTrainers(); });
  $('trNext').addEventListener('click', () => { tState.page += 1; loadTrainers(); });

  $('trainerList').addEventListener('click', (e) => { const p = e.target.closest('[data-pick]'); if (p) selectTrainer(Number(p.dataset.pick)); });
  $('trainerList').addEventListener('change', (e) => {
    const c = e.target.closest('[data-cmp]'); if (!c) return;
    const id = Number(c.dataset.cmp);
    if (c.checked) { if (tState.compare.size >= 6) { c.checked = false; toast('You can compare up to 6 trainers.', true); return; } tState.compare.add(id); } else tState.compare.delete(id);
    $('compareBtn').textContent = `Compare selected (${tState.compare.size})`; $('compareBtn').disabled = tState.compare.size < 2;
  });

  /* ---------- Single trainer calendar ---------- */
  let calendar = null;
  async function selectTrainer(id) {
    tState.selected = id; closeCompare();
    $('trainerPanelEmpty').style.display = 'none'; $('trainerPanel').style.display = 'block';
    document.querySelectorAll('[data-pick]').forEach((b) => b.toggleAttribute('aria-current', Number(b.dataset.pick) === id));
    const row = tState.items.find((t) => t.id === id);
    $('tpName').textContent = row ? row.full_name : 'Trainer';
    $('tpPrograms').textContent = row && row.programs.length ? `Teaches: ${row.programs.map((p) => p.name).join(', ')}` : 'No course expertise recorded yet.';
    $('tpExpertise').style.display = $('tpRequest').style.display = canManage ? '' : 'none';
    $('tpCalendar').innerHTML = '';
    calendar = SchedCal.mount($('tpCalendar'), {
      editable: false,
      load: (s, e) => ArckAPI.request(`${API}/trainers/${id}/calendar?start=${s}&end=${e}`),
      onLoaded: (p) => {
        $('tpName').textContent = p.trainer_name;
        const s = p.summary;
        $('tpStats').innerHTML = [['Available hours', s.available_hours], ['Assigned hours', s.booked_hours], ['Remaining free hours', s.free_hours], ['Preferred hours', s.preferred_hours]]
          .map(([l, v]) => `<div class="sched-stat"><b>${v}</b>${l}</div>`).join('')
          + `<div class="sched-stat" style="grid-column:1 / -1;"><b style="font-size:1rem;">Upcoming commitments</b>${s.upcoming_sessions.length
            ? s.upcoming_sessions.map((x) => `<div><span class="muted">${fmtDate(x.date)} · ${time12(x.start_time)}–${time12(x.end_time)}</span> ${esc(x.batch_name)}</div>`).join('')
            : '<span>None in the next 60 days.</span>'}</div>`;
      },
      onError: (err) => toast(fail(err), true),
    });
  }

  $('tpExpertise').addEventListener('click', async () => {
    const id = tState.selected;
    try {
      const [opts, cur] = await Promise.all([ArckAPI.request(`${API}/expertise-options`), ArckAPI.request(`${API}/trainers/${id}/expertise`)]);
      const have = new Set(cur.program_ids);
      const body = document.createElement('div');
      body.innerHTML = opts.length ? `<p style="margin-top:0;">Tick every program this trainer can teach. Recommendations use this list.</p>${opts.map((p) =>
        `<div class="permission-item"><input type="checkbox" id="pe${p.id}" value="${p.id}" ${have.has(p.id) ? 'checked' : ''}><label for="pe${p.id}">${esc(p.name)}</label></div>`).join('')}` : '<p>No active programs exist yet.</p>';
      modal({ title: 'Course expertise', body, buttons: [{ label: 'Cancel', kind: 'outline' }, { label: 'Save', kind: 'accent', onClick: async () => {
        try {
          await ArckAPI.request(`${API}/trainers/${id}/expertise`, { method: 'POST', body: { program_ids: [...body.querySelectorAll('input:checked')].map((c) => Number(c.value)) } });
          toast('Expertise saved.'); await loadTrainers(); selectTrainer(id);
        } catch (err) { toast(fail(err), true); return false; }
      } }] });
    } catch (err) { toast(fail(err), true); }
  });

  $('tpRequest').addEventListener('click', () => {
    const id = tState.selected; const today = new Date().toISOString().slice(0, 10);
    const body = document.createElement('div');
    body.innerHTML = `<p style="margin-top:0;">The trainer is notified in the portal and by email (if enabled) and sees the requested dates on their calendar.</p>
      <div class="sched-form-grid"><div><label class="form-label" for="rqS">From</label><input class="form-control" type="date" id="rqS" value="${today}"></div>
      <div><label class="form-label" for="rqE">To (optional)</label><input class="form-control" type="date" id="rqE"></div>
      <div class="full"><label class="form-label" for="rqM">Message</label><textarea class="form-control" id="rqM" rows="3" maxlength="1000" placeholder="e.g. Please add your March availability."></textarea></div></div>
      <div class="sched-inline-error" id="rqErr" role="alert"></div>`;
    modal({ title: 'Request availability update', body, buttons: [{ label: 'Cancel', kind: 'outline' }, { label: 'Send request', kind: 'accent', onClick: async () => {
      const message = body.querySelector('#rqM').value.trim();
      if (message.length < 3) { body.querySelector('#rqErr').textContent = 'Please write a short message.'; return false; }
      try {
        await ArckAPI.request(`${API}/trainers/${id}/availability-requests`, { method: 'POST', body: { message, start_date: body.querySelector('#rqS').value, end_date: body.querySelector('#rqE').value || null } });
        toast('Request sent.'); calendar.reload();
      } catch (err) { body.querySelector('#rqErr').textContent = fail(err); return false; }
    } }] });
  });

  /* ---------- Compare ---------- */
  const cmp = { anchor: null, mounts: [], cache: { key: '', promise: null } };
  function closeCompare() { $('comparePanel').style.display = 'none'; cmp.mounts = []; $('cmpBody').innerHTML = ''; }
  function openCompare() {
    const ids = [...tState.compare].sort((a, b) => a - b);
    $('trainerPanelEmpty').style.display = 'none'; $('trainerPanel').style.display = 'none'; $('comparePanel').style.display = 'block';
    cmp.anchor = cmp.anchor || SchedCal.todayIn('Asia/Kolkata');
    $('cmpBody').innerHTML = ids.map((id) => `<section><h3>${esc((tState.items.find((t) => t.id === id) || {}).full_name || 'Trainer')}</h3><div id="cmp${id}"></div></section>`).join('');
    cmp.mounts = ids.map((id) => SchedCal.mount($(`cmp${id}`), {
      editable: false, toolbar: false, view: 'week', anchor: cmp.anchor,
      load: async (s, e) => {
        const key = `${ids.join(',')}|${s}|${e}`;
        if (cmp.cache.key !== key) { cmp.cache = { key, promise: ArckAPI.request(`${API}/calendar?trainer_ids=${ids.join(',')}&start=${s}&end=${e}`) }; }
        try { return (await cmp.cache.promise).find((c) => c.trainer_id === id); } catch (err) { cmp.cache = { key: '', promise: null }; throw err; }
      },
      onError: (err) => toast(fail(err), true),
    }));
  }
  const shiftCompare = (days) => { cmp.anchor = days === 0 ? SchedCal.todayIn('Asia/Kolkata') : SchedCal.addDays(cmp.anchor, days); cmp.mounts.forEach((m) => m.setAnchor(cmp.anchor)); };
  $('compareBtn').addEventListener('click', openCompare);
  $('cmpPrev').addEventListener('click', () => shiftCompare(-7)); $('cmpNext').addEventListener('click', () => shiftCompare(7)); $('cmpToday').addEventListener('click', () => shiftCompare(0));
  $('cmpClose').addEventListener('click', () => { closeCompare(); $('trainerPanelEmpty').style.display = tState.selected ? 'none' : ''; if (tState.selected) $('trainerPanel').style.display = 'block'; });

  /* ---------- Batches + recommendations ---------- */
  const batchState = { page: 1, total: 0, pageSize: 20, items: [], selected: null, loaded: false };
  async function loadBatches() {
    $('batchList').innerHTML = '<li class="muted">Loading…</li>';
    try {
      const data = await ArckAPI.request(`${API}/batches?${qs({ search: $('bSearch').value.trim(), needs_trainer: $('bNeeds').checked ? 'true' : '', page: batchState.page, page_size: batchState.pageSize })}`);
      batchState.items = data.items; batchState.total = data.total; batchState.loaded = true;
      $('batchList').innerHTML = data.items.length ? data.items.map((b) => `<li><button type="button" class="sched-trainer" data-batch="${b.id}" ${batchState.selected === b.id ? 'aria-current="true"' : ''}>
        <span><b>${esc(b.name)}</b><br><span class="muted">${esc(b.program_name || 'No program')} · ${b.class_days.length ? esc(b.class_days.join(', ')) : 'No class days'}${b.start_time ? ` · ${time12(b.start_time)}–${time12(b.end_time)}` : ''}</span><br>
        ${b.trainer_name ? `<span class="admin-activity-badge is-success">${esc(b.trainer_name)}</span>` : '<span class="admin-activity-badge is-danger">No trainer</span>'}
        <span class="admin-activity-badge is-muted">${esc(b.status)}</span></span></button></li>`).join('') : '<li class="muted">No batches match.</li>';
      const pages = Math.max(1, Math.ceil(data.total / data.page_size));
      $('bPage').textContent = `Page ${data.page} of ${pages}`; $('bPrev').disabled = data.page <= 1; $('bNext').disabled = data.page >= pages;
    } catch (err) { $('batchList').innerHTML = '<li class="muted">Couldn\'t load batches.</li>'; toast(fail(err), true); }
  }
  const reloadBatches = debounce(() => { batchState.page = 1; loadBatches(); });
  $('bSearch').addEventListener('input', reloadBatches); $('bNeeds').addEventListener('change', reloadBatches);
  $('bPrev').addEventListener('click', () => { batchState.page -= 1; loadBatches(); }); $('bNext').addEventListener('click', () => { batchState.page += 1; loadBatches(); });
  $('batchList').addEventListener('click', (e) => { const b = e.target.closest('[data-batch]'); if (b) selectBatch(Number(b.dataset.batch)); });

  async function selectBatch(id) {
    batchState.selected = id;
    document.querySelectorAll('[data-batch]').forEach((b) => b.toggleAttribute('aria-current', Number(b.dataset.batch) === id));
    $('recPanel').innerHTML = '<div class="admin-panel-empty">Finding suitable trainers…</div>';
    try {
      const rec = await ArckAPI.request(`${API}/batches/${id}/recommendations`);
      renderRecommendations(rec); loadAlerts();
    } catch (err) { $('recPanel').innerHTML = '<div class="admin-panel-empty">Couldn\'t load recommendations.</div>'; toast(fail(err), true); }
  }

  function renderRecommendations(rec) {
    const b = rec.batch;
    const bar = (p) => `<div class="sched-bar"><span>${esc(p.criterion)}</span><i style="--pct:${p.max ? Math.round((p.points / p.max) * 100) : 0}%"></i><span>${p.points} / ${p.max}</span></div><div class="muted" style="font-size:.76rem;margin:-4px 0 4px 138px;">${esc(p.note)}</div>`;
    const card = (c) => `<article class="sched-cand ${c.is_current ? 'is-current' : ''}" aria-label="${esc(c.full_name)}">
      <header><div><b>${esc(c.full_name)}</b> ${c.is_current ? '<span class="admin-activity-badge is-info">Current trainer</span>' : ''}<br>
        <span class="muted">${c.session_count} sessions · ${c.required_hours} h · busiest week ${c.peak_week_hours} h · ${c.preferred_percent}% in preferred hours</span></div>
        <div style="text-align:right;"><div class="sched-score">${c.score}</div><span class="muted" style="font-size:.72rem;">out of 100</span></div></header>
      <div class="sched-bars">${c.score_breakdown.map(bar).join('')}</div>
      <ul class="sched-reasons">${c.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}${c.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>
      <div class="sched-row-actions"><button type="button" class="sched-btn" data-view-cal="${c.trainer_id}">View calendar</button>
        ${canManage && !c.is_current ? `<button type="button" class="btn btn-accent" data-assign="${c.trainer_id}" style="padding:6px 14px;">${b.trainer_id ? 'Reassign to this trainer' : 'Assign this trainer'}</button>` : ''}</div></article>`;
    const excluded = (c) => `<article class="sched-cand"><header><div><b>${esc(c.full_name)}</b> ${c.is_current ? '<span class="admin-activity-badge is-info">Current trainer</span>' : ''}</div></header>
      <ul class="sched-reasons">${c.reasons.filter((r) => !c.conflicts.some((x) => x.message === r)).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
      ${c.conflicts.length ? `<ul class="sched-conflicts">${c.conflicts.map((x) => `<li>${esc(x.message)}${x.examples.length ? ` <span class="muted">e.g. ${x.examples.slice(0, 3).map((ex) => `${fmtDate(ex.date)} ${time12(ex.start_time)}–${time12(ex.end_time)}`).join('; ')}</span>` : ''}</li>`).join('')}</ul>` : ''}
      <div class="sched-row-actions"><button type="button" class="sched-btn" data-view-cal="${c.trainer_id}">View calendar</button>
        ${canManage && !c.is_current && c.overridable && canOverride ? `<button type="button" class="sched-btn" data-assign="${c.trainer_id}">Assign with override…</button>` : ''}</div></article>`;
    $('recPanel').innerHTML = `
      <div class="admin-panel" style="margin-top:0;"><div class="admin-panel-header" style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center;">
        <div><h2>${esc(b.name)}</h2><span class="muted" style="font-size:.85rem;">${esc(b.program_name || 'No program')} · ${rec.session_count} remaining session${rec.session_count === 1 ? '' : 's'}</span></div>
        <div class="sched-row-actions" style="margin:0;"><a class="sched-btn" href="admin-batch-detail.html?id=${b.id}" style="text-decoration:none;">Batch &amp; sessions</a>
          <button type="button" class="sched-btn" data-batch-history="${b.id}">History</button>
          ${canManage && b.trainer_id ? '<button type="button" class="sched-btn" data-unassign="1">Remove trainer</button>' : ''}</div></div>
        <div style="padding:16px;">
          <p class="muted" style="margin:0 0 14px;font-size:.84rem;">Ranking: Course expertise ${rec.weights.expertise} + Preferred hours ${rec.weights.preferred} + Workload headroom ${rec.weights.workload} = 100 points. A trainer who already holds this batch is always listed first. Trainers are only listed as suitable if they are free for every remaining session, have no overlapping confirmed batch, and stay within ${rec.max_weekly_hours} h/week. Nothing is assigned until you confirm.</p>
          <h3 style="font-size:1rem;">Suitable trainers (${rec.suitable.length})</h3>
          ${rec.suitable.length ? rec.suitable.map(card).join('') : `<div class="sched-empty"><h3>No suitable trainer for this timetable</h3><p style="margin:0;color:var(--muted);">Nobody with the right expertise is free for every session without a clash. See the reasons below, then change the batch timetable, ask a trainer to add availability, or pick one of the free windows.</p></div>`}
          ${rec.alternatives.length ? `<h3 style="font-size:1rem;margin-top:16px;">Alternative free slots</h3><ul class="sched-list" style="border:1px solid var(--border-c);border-radius:10px;">${rec.alternatives.map((a) => `<li>${esc(a.trainer_name)} · ${fmtDate(a.date)} · ${time12(a.start_time)}–${time12(a.end_time)}</li>`).join('')}</ul>` : ''}
          <details style="margin-top:16px;" ${rec.suitable.length ? '' : 'open'}><summary style="cursor:pointer;font-weight:600;">Not suitable (${rec.excluded.length})</summary><div style="margin-top:10px;">${rec.excluded.map(excluded).join('') || '<p class="muted">Everyone is suitable.</p>'}</div></details>
        </div></div>`;
    $('recPanel').dataset.batchId = b.id; $('recPanel').dataset.batchName = b.name; $('recPanel').dataset.currentTrainer = b.trainer_id || '';
    $('recPanel').rec = rec;
  }

  $('recPanel').addEventListener('click', (e) => {
    const vc = e.target.closest('[data-view-cal]'); const as = e.target.closest('[data-assign]'); const un = e.target.closest('[data-unassign]'); const hh = e.target.closest('[data-batch-history]');
    if (vc) { showTab('tabCalendars'); selectTrainer(Number(vc.dataset.viewCal)); }
    if (as) confirmAssignment(Number(as.dataset.assign));
    if (un) confirmAssignment(null);
    if (hh) { histState.batchId = Number(hh.dataset.batchHistory); showTab('tabHistory'); }
  });

  function confirmAssignment(trainerId) {
    const rec = $('recPanel').rec; const batchId = Number($('recPanel').dataset.batchId);
    const all = [...rec.suitable, ...rec.excluded];
    const cand = trainerId ? all.find((c) => c.trainer_id === trainerId) : null;
    const key = newKey(); // reused if the admin retries, so a double-click or retry can never assign twice
    const body = document.createElement('div');
    const needsOverride = !!(cand && cand.conflicts.length);
    body.innerHTML = trainerId === null
      ? `<p style="margin-top:0;">Remove the current trainer from <b>${esc(rec.batch.name)}</b>? They will be notified.</p>`
      : `<p style="margin-top:0;">${rec.batch.trainer_id ? 'Reassign' : 'Assign'} <b>${esc(cand.full_name)}</b> to <b>${esc(rec.batch.name)}</b> for ${cand.session_count} session${cand.session_count === 1 ? '' : 's'} (${cand.required_hours} h)? The trainer is notified. Availability is checked again when you confirm.</p>
        ${needsOverride ? `<div class="sched-inline-error" style="margin-bottom:8px;">This trainer has conflicts. Only an administrator allowed to override may continue, and a reason is recorded in the audit log.</div>
          <ul class="sched-conflicts">${cand.conflicts.map((c) => `<li>${esc(c.message)}</li>`).join('')}</ul>
          <label class="form-label" for="ovReason" style="margin-top:12px;">Reason for override (min. 10 characters)</label><textarea class="form-control" id="ovReason" rows="3" maxlength="1000"></textarea>` : ''}
        <div class="sched-inline-error" id="asErr" role="alert"></div>`;
    modal({ title: trainerId === null ? 'Remove trainer' : 'Confirm assignment', body, buttons: [
      { label: 'Cancel', kind: 'outline' },
      { label: needsOverride ? 'Assign with override' : (trainerId === null ? 'Remove trainer' : 'Confirm assignment'), kind: trainerId === null ? 'danger' : 'accent', onClick: async () => {
        const reason = needsOverride ? body.querySelector('#ovReason').value.trim() : null;
        if (needsOverride && (!reason || reason.length < 10)) { body.querySelector('#asErr').textContent = 'Enter a reason of at least 10 characters.'; return false; }
        try {
          const r = await ArckAPI.request(`${API}/batches/${batchId}/assign`, { method: 'POST', body: { trainer_id: trainerId, idempotency_key: key, override: needsOverride, reason } });
          toast(r.replayed ? 'Already saved — no duplicate was created.' : (trainerId === null ? 'Trainer removed.' : 'Trainer assigned and notified.'));
          await Promise.all([loadBatches(), loadTrainers(), loadAlerts()]); selectBatch(batchId);
        } catch (err) {
          const conflicts = err.data && err.data.conflicts;
          body.querySelector('#asErr').innerHTML = esc(fail(err)) + (conflicts ? `<ul class="sched-conflicts">${conflicts.map((c) => `<li>${esc(c.message)}</li>`).join('')}</ul>` : '')
            + (err.code === 'overlap' ? '<br>Reschedule or reassign the other batch first; overlapping sessions cannot be overridden.' : '');
          if (err.code === 'unavailable' || err.code === 'overlap') selectBatch(batchId);
          return false;
        }
      } },
    ] });
  }

  /* ---------- History ---------- */
  const histState = { page: 1, batchId: null };
  async function loadHistory() {
    $('histBody').innerHTML = '<tr><td colspan="5" class="admin-panel-empty">Loading…</td></tr>';
    $('histTitle').innerHTML = histState.batchId ? 'Assignment history for one batch <button type="button" class="sched-btn" id="histClear">Show all</button>' : 'Assignment history';
    const clear = $('histClear'); if (clear) clear.addEventListener('click', () => { histState.batchId = null; histState.page = 1; loadHistory(); });
    try {
      const d = await ArckAPI.request(`${API}/history?${qs({ batch_id: histState.batchId, page: histState.page, page_size: 20 })}`);
      $('histBody').innerHTML = d.items.length ? d.items.map((h) => `<tr><td>${fmtDateTime(h.created_at)}</td><td>${esc(h.batch_name || '(deleted batch)')}</td>
        <td><b>${ACTION_LABEL[h.action]}</b>: ${esc(h.from_trainer || 'nobody')} → ${esc(h.to_trainer || 'nobody')}</td><td>${esc(h.actor || '—')}</td>
        <td>${h.was_override ? `<span class="admin-activity-badge is-danger">Override</span><br><span class="muted">${esc(h.reason || '')}</span>` : '—'}</td></tr>`).join('')
        : '<tr><td colspan="5" class="admin-panel-empty">No trainer assignments have been recorded yet.</td></tr>';
      const pages = Math.max(1, Math.ceil(d.total / d.page_size));
      $('hPage').textContent = `Page ${d.page} of ${pages}`; $('hPrev').disabled = d.page <= 1; $('hNext').disabled = d.page >= pages;
    } catch (err) { $('histBody').innerHTML = '<tr><td colspan="5" class="admin-panel-empty">Couldn\'t load history.</td></tr>'; toast(fail(err), true); }
  }
  $('hPrev').addEventListener('click', () => { histState.page -= 1; loadHistory(); }); $('hNext').addEventListener('click', () => { histState.page += 1; loadHistory(); });

  /* ---------- Boot ---------- */
  try {
    const programs = await ArckAPI.request(`${API}/expertise-options`);
    $('fProgram').insertAdjacentHTML('beforeend', programs.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join(''));
  } catch (err) { toast(fail(err), true); }
  loadTrainers(); loadAlerts();
});
