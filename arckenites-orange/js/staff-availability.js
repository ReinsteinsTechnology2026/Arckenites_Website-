const { esc, DAYS, time12, fmtDate, fmtDateTime, toast, modal, confirmDialog, conflictList } = SchedUI;

const KIND_LABEL = { available: 'Available', preferred: 'Preferred hours', unavailable: 'Unavailable', leave: 'Leave / holiday' };
const CATEGORY_LABEL = { holiday: 'Holiday', leave: 'Leave', personal: 'Personal commitment', schedule_change: 'Schedule change', other: 'Other' };

document.addEventListener('DOMContentLoaded', async () => {
  const user = await ArckAuth.requireRole('staff');
  if (!user) return;

  /* ---------- Topbar (same pattern as the other trainer pages) ---------- */
  document.getElementById('staffAvatarWrap').innerHTML = ArckAPI.avatarHtml(user.full_name, user.photo_url, 36);
  document.getElementById('staffAvatarWrapMenu').innerHTML = ArckAPI.avatarHtml(user.full_name, user.photo_url, 40);
  document.getElementById('staffProfileName').textContent = user.full_name;
  const panel = document.getElementById('staffProfilePanel');
  document.getElementById('staffProfileTrigger').addEventListener('click', (e) => { e.stopPropagation(); panel.classList.toggle('is-open'); });
  document.addEventListener('click', () => panel.classList.remove('is-open'));
  document.getElementById('staffProfileLogout').addEventListener('click', () => ArckAuth.logout());
  const goProfile = (e) => { e.stopPropagation(); window.location.href = 'staff-dashboard.html?openProfile=1'; };
  document.getElementById('staffAvatarBtn').addEventListener('click', goProfile);
  document.getElementById('staffAvatarMenuBtn').addEventListener('click', goProfile);

  const banner = document.getElementById('serverBanner');
  document.getElementById('legend').innerHTML = SchedCal.legendHtml();

  let payload = null;
  let calendar = null;
  const API = '/staff/me/availability';

  const failure = (err) => {
    if (err && err.status === 0) banner.classList.add('is-visible');
    return ArckAPI.errorMessage(err);
  };

  /* ---------- Rendering of the side panels / summary ---------- */
  function renderPanels(p, { start, end }) {
    payload = p;
    document.getElementById('tzNote').textContent = `Times are shown in ${p.timezone}.`;
    const s = p.summary;
    document.getElementById('stats').innerHTML = [
      ['Available hours', s.available_hours, 'in this view'], ['Booked hours', s.booked_hours, 'confirmed sessions'],
      ['Free hours', s.free_hours, 'still open for new batches'], ['Preferred hours', s.preferred_hours, 'marked as preferred'],
    ].map(([label, v, sub]) => `<div class="sched-stat"><b>${v}</b>${label}<br><span>${sub}</span></div>`).join('');

    document.getElementById('upcomingList').innerHTML = s.upcoming_sessions.length
      ? s.upcoming_sessions.map((x) => `<li><b>${esc(x.batch_name)}</b><br><span class="muted">${fmtDate(x.date)} · ${time12(x.start_time)} – ${time12(x.end_time)}</span></li>`).join('')
      : '<li class="muted">No upcoming sessions assigned to you yet.</li>';

    document.getElementById('rulesList').innerHTML = p.rules.length ? p.rules.map((r) => `
      <li><b>${DAYS[r.weekday]}</b> · ${time12(r.start_time)} – ${time12(r.end_time)}
        <span class="admin-activity-badge ${r.kind === 'preferred' ? 'is-info' : 'is-success'}">${r.kind === 'preferred' ? 'Preferred' : 'Available'}</span><br>
        <span class="muted">From ${fmtDate(r.effective_from)}${r.effective_until ? ` until ${fmtDate(r.effective_until)}` : ''}</span>
        <div class="sched-row-actions"><button type="button" class="sched-btn" data-edit-rule="${r.id}">Edit</button>
        <button type="button" class="sched-btn" data-del-rule="${r.id}">Remove</button></div></li>`).join('')
      : '<li class="muted">No weekly hours yet. Use “Weekly hours” to add your regular schedule.</li>';

    document.getElementById('entriesList').innerHTML = p.entries.length ? p.entries.map((e) => `
      <li><b>${esc(KIND_LABEL[e.kind])}</b>${e.category ? ` · ${esc(CATEGORY_LABEL[e.category] || e.category)}` : ''}<br>
        <span class="muted">${fmtDate(e.start_date)} ${time12(e.start_time)} → ${e.end_date !== e.start_date ? fmtDate(e.end_date) + ' ' : ''}${time12(e.end_time)}${e.note ? ` · ${esc(e.note)}` : ''}</span>
        <div class="sched-row-actions"><button type="button" class="sched-btn" data-edit-entry="${e.id}">Edit</button>
        <button type="button" class="sched-btn" data-del-entry="${e.id}">Remove</button></div></li>`).join('')
      : '<li class="muted">No one-off availability or time off in this view.</li>';

    document.getElementById('onboarding').style.display = (!p.rules.length && !p.entries.length) ? 'block' : 'none';
  }

  /* ---------- Calendar ---------- */
  calendar = SchedCal.mount(document.getElementById('calendar'), {
    editable: true,
    load: (s, e) => ArckAPI.request(`${API}?start=${s}&end=${e}`),
    onLoaded: renderPanels,
    onError: (err) => toast(failure(err), true),
    onSlotSelect: (slot) => openEntryDialog({ start_date: slot.date, start_time: slot.start, end_time: slot.end }),
    onSegmentClick: ({ id }) => { const e = payload.entries.find((x) => x.id === id); if (e) openEntryDialog(e); },
  });

  /* ---------- Notices ---------- */
  async function loadNotices() {
    try {
      const rows = await ArckAPI.request(`${API}/notices?status=open`);
      const box = document.getElementById('noticesPanel');
      box.style.display = rows.length ? 'block' : 'none';
      document.getElementById('noticesList').innerHTML = rows.map((n) => `<li>${esc(n.message)}<br>
        <span class="muted">${fmtDateTime(n.created_at)}</span>
        <div class="sched-row-actions"><button type="button" class="sched-btn" data-ack="${n.id}">Mark as done</button></div></li>`).join('');
    } catch (err) { failure(err); }
  }
  document.getElementById('noticesList').addEventListener('click', async (ev) => {
    const id = ev.target.closest('[data-ack]')?.dataset.ack;
    if (!id) return;
    try { await ArckAPI.request(`${API}/notices/${id}/resolve`, { method: 'POST' }); await loadNotices(); calendar.reload(); }
    catch (err) { toast(failure(err), true); }
  });
  loadNotices();

  /* ---------- Saving with the confirmed-batch guard ---------- */
  async function save(runner, successMessage, retryWithReview) {
    try {
      const result = await runner(false, null);
      toast(result && result.status === 'submitted_for_review' ? result.detail : successMessage);
      calendar.reload();
      return true;
    } catch (err) {
      if (err.code === 'confirmed_batch_conflict') return conflictDialog(err, (review, note) => runner(review, note), successMessage);
      toast(failure(err), true);
      return false;
    }
  }

  function conflictDialog(err, resubmit, successMessage) {
    return new Promise((resolve) => {
      let note;
      const m = modal({
        title: 'This affects a confirmed batch',
        body: `<p style="margin:0 0 8px;color:var(--ink)">${esc(err.message)}</p>${conflictList(err.data.conflicts)}
          <label class="form-label" for="reviewNote" style="margin-top:14px;">Note for the administrator (optional)</label>
          <textarea class="form-control" id="reviewNote" rows="3" maxlength="500"></textarea>
          <p style="margin:10px 0 0;font-size:.82rem;">Your calendar stays unchanged until an administrator reassigns or reschedules the batch.</p>`,
        buttons: [
          { label: 'Keep my availability', kind: 'outline', onClick: (close) => { close(); resolve(false); }, keepOpen: true },
          { label: 'Ask administrators to review', kind: 'accent', keepOpen: true, onClick: async (close) => {
            try {
              const r = await resubmit(true, m.el.querySelector('#reviewNote').value.trim());
              toast(r.detail || 'Sent to administrators for review.'); close(); resolve(true); loadNotices(); calendar.reload();
            } catch (e2) { toast(failure(e2), true); }
          } },
        ],
      });
    });
  }

  /* ---------- One-off entry dialog (create / edit) ---------- */
  function openEntryDialog(seed = {}) {
    const editing = !!seed.id;
    const today = new Date().toISOString().slice(0, 10);
    const body = document.createElement('div');
    body.innerHTML = `
      <div class="sched-form-grid">
        <div class="full"><label class="form-label" for="enKind">What are you marking?</label>
          <select class="form-select" id="enKind">${Object.entries(KIND_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></div>
        <div><label class="form-label" for="enSD">Start date</label><input class="form-control" type="date" id="enSD" required></div>
        <div><label class="form-label" for="enST">Start time</label><input class="form-control" type="time" id="enST" step="900" required></div>
        <div><label class="form-label" for="enED">End date</label><input class="form-control" type="date" id="enED" required></div>
        <div><label class="form-label" for="enET">End time</label><input class="form-control" type="time" id="enET" step="900" required></div>
        <div class="full" id="enCatWrap"><label class="form-label" for="enCat">Reason</label>
          <select class="form-select" id="enCat"><option value="">Not specified</option>${Object.entries(CATEGORY_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></div>
        <div class="full"><label class="form-label" for="enNote">Note (optional)</label><input class="form-control" id="enNote" maxlength="300"></div>
      </div>
      <p style="margin:12px 0 0;font-size:.82rem;">For a whole day, use 12:00 AM to 11:59 PM. Times are in ${esc(payload ? payload.timezone : 'the business timezone')}.</p>
      <div class="sched-inline-error" id="enErr" role="alert"></div>`;
    const q = (id) => body.querySelector(id);
    q('#enKind').value = seed.kind || 'available';
    q('#enSD').value = seed.start_date || today; q('#enED').value = seed.end_date || seed.start_date || today;
    q('#enST').value = seed.start_time || '09:00'; q('#enET').value = seed.end_time || '17:00';
    q('#enCat').value = seed.category || ''; q('#enNote').value = seed.note || '';
    const syncCat = () => { q('#enCatWrap').style.display = ['unavailable', 'leave'].includes(q('#enKind').value) ? '' : 'none'; };
    q('#enKind').addEventListener('change', syncCat); syncCat();
    q('#enSD').addEventListener('change', () => { if (q('#enED').value < q('#enSD').value) q('#enED').value = q('#enSD').value; });

    modal({
      title: editing ? 'Edit availability' : 'Add availability or time off', body,
      buttons: [
        { label: 'Cancel', kind: 'outline' },
        ...(editing ? [{ label: 'Remove', kind: 'danger', keepOpen: true, onClick: async (close) => {
          if (!(await confirmDialog({ title: 'Remove this entry?', message: 'This removes it from your calendar.', confirmLabel: 'Remove', danger: true }))) return;
          const ok = await save((review, note) => ArckAPI.request(`${API}/entries/${seed.id}${review ? `?submit_for_review=true${note ? `&review_note=${encodeURIComponent(note)}` : ''}` : ''}`, { method: 'DELETE' }), 'Entry removed.');
          if (ok) close();
        } }] : []),
        { label: editing ? 'Save changes' : 'Save', kind: 'accent', keepOpen: true, onClick: async (close) => {
          const kind = q('#enKind').value;
          const data = { kind, start_date: q('#enSD').value, start_time: q('#enST').value, end_date: q('#enED').value, end_time: q('#enET').value,
            category: ['unavailable', 'leave'].includes(kind) ? (q('#enCat').value || null) : null, note: q('#enNote').value.trim() || null };
          if (!data.start_date || !data.start_time || !data.end_date || !data.end_time) { q('#enErr').textContent = 'Please fill in every date and time.'; return; }
          if (`${data.end_date}T${data.end_time}` <= `${data.start_date}T${data.start_time}`) { q('#enErr').textContent = 'The end must be after the start.'; return; }
          q('#enErr').textContent = '';
          const url = editing ? `${API}/entries/${seed.id}` : `${API}/entries`;
          try {
            const result = await ArckAPI.request(url, { method: editing ? 'PATCH' : 'POST', body: data });
            toast(result.status === 'submitted_for_review' ? result.detail : 'Saved. Your calendar is updated.'); calendar.reload(); close();
          } catch (err) {
            if (err.code === 'confirmed_batch_conflict') {
              const ok = await conflictDialog(err, (review, note) => ArckAPI.request(url, { method: editing ? 'PATCH' : 'POST', body: { ...data, submit_for_review: true, review_note: note || null } }), 'Saved.');
              if (ok) close();
            } else q('#enErr').textContent = failure(err);
          }
        } },
      ],
    });
  }

  /* ---------- Weekly hours dialog ---------- */
  function openRuleDialog(rule = null) {
    const today = new Date().toISOString().slice(0, 10);
    const body = document.createElement('div');
    body.innerHTML = `
      <div class="sched-form-grid">
        ${rule ? '' : `<div class="full"><span class="form-label">Days</span><div class="sched-days" role="group" aria-label="Days of the week">
          ${DAYS.map((d, i) => `<label><input type="checkbox" name="wd" value="${i}" ${i < 5 ? 'checked' : ''}>${d.slice(0, 3)}</label>`).join('')}</div></div>`}
        <div><label class="form-label" for="ruS">From</label><input class="form-control" type="time" id="ruS" step="900"></div>
        <div><label class="form-label" for="ruE">To</label><input class="form-control" type="time" id="ruE" step="900"></div>
        <div class="full"><label class="form-label" for="ruK">Type</label><select class="form-select" id="ruK">
          <option value="available">Available</option><option value="preferred">Preferred (I like teaching then)</option></select></div>
        <div><label class="form-label" for="ruF">Starting</label><input class="form-control" type="date" id="ruF"></div>
        <div><label class="form-label" for="ruU">Until (optional)</label><input class="form-control" type="date" id="ruU"></div>
      </div>
      ${rule ? '<p style="margin:12px 0 0;font-size:.82rem;">Changes apply from today onward; earlier weeks keep their history.</p>' : ''}
      <div class="sched-inline-error" id="ruErr" role="alert"></div>`;
    const q = (id) => body.querySelector(id);
    q('#ruS').value = rule ? rule.start_time : '09:00'; q('#ruE').value = rule ? rule.end_time : '17:00';
    q('#ruK').value = rule ? rule.kind : 'available'; q('#ruF').value = rule ? rule.effective_from : today; q('#ruU').value = rule?.effective_until || '';
    modal({
      title: rule ? `Edit ${DAYS[rule.weekday]} hours` : 'Set weekly hours', body,
      buttons: [
        { label: 'Cancel', kind: 'outline' },
        { label: 'Save', kind: 'accent', keepOpen: true, onClick: async (close) => {
          const start_time = q('#ruS').value, end_time = q('#ruE').value;
          if (!start_time || !end_time || end_time <= start_time) { q('#ruErr').textContent = 'End time must be after start time.'; return; }
          const effective_until = q('#ruU').value || null;
          if (effective_until && effective_until < q('#ruF').value) { q('#ruErr').textContent = '“Until” cannot be before the start date.'; return; }
          const weekdays = [...body.querySelectorAll('[name=wd]:checked')].map((c) => Number(c.value));
          if (!rule && !weekdays.length) { q('#ruErr').textContent = 'Choose at least one day.'; return; }
          const send = (review) => rule
            ? ArckAPI.request(`${API}/rules/${rule.id}${review ? '?submit_for_review=true' : ''}`, { method: 'PATCH', body: { start_time, end_time, kind: q('#ruK').value, effective_from: q('#ruF').value, effective_until, clear_effective_until: !effective_until } })
            : ArckAPI.request(`${API}/rules`, { method: 'POST', body: { weekdays, start_time, end_time, kind: q('#ruK').value, effective_from: q('#ruF').value, effective_until } });
          try { const r = await send(false); toast(r && r.status === 'submitted_for_review' ? r.detail : 'Weekly hours saved.'); calendar.reload(); close(); }
          catch (err) {
            if (err.code === 'confirmed_batch_conflict') { if (await conflictDialog(err, () => send(true), '')) close(); }
            else q('#ruErr').textContent = failure(err);
          }
        } },
      ],
    });
  }

  /* ---------- Wiring ---------- */
  document.getElementById('addEntryBtn').addEventListener('click', () => openEntryDialog());
  document.getElementById('addWeeklyBtn').addEventListener('click', () => openRuleDialog());
  document.getElementById('onboardWeeklyBtn').addEventListener('click', () => openRuleDialog());
  document.getElementById('rulesList').addEventListener('click', async (ev) => {
    const edit = ev.target.closest('[data-edit-rule]'); const del = ev.target.closest('[data-del-rule]');
    if (edit) openRuleDialog(payload.rules.find((r) => r.id === Number(edit.dataset.editRule)));
    if (del) {
      const r = payload.rules.find((x) => x.id === Number(del.dataset.delRule));
      if (!(await confirmDialog({ title: 'Remove weekly hours?', message: `Remove ${DAYS[r.weekday]} ${time12(r.start_time)} – ${time12(r.end_time)}? Past weeks are kept; it stops applying from today.`, confirmLabel: 'Remove', danger: true }))) return;
      save((review) => ArckAPI.request(`${API}/rules/${r.id}${review ? '?submit_for_review=true' : ''}`, { method: 'DELETE' }), 'Weekly hours removed.');
    }
  });
  document.getElementById('entriesList').addEventListener('click', async (ev) => {
    const edit = ev.target.closest('[data-edit-entry]'); const del = ev.target.closest('[data-del-entry]');
    if (edit) openEntryDialog(payload.entries.find((x) => x.id === Number(edit.dataset.editEntry)));
    if (del) {
      const id = Number(del.dataset.delEntry);
      if (!(await confirmDialog({ title: 'Remove this entry?', message: 'This removes it from your calendar.', confirmLabel: 'Remove', danger: true }))) return;
      save((review, note) => ArckAPI.request(`${API}/entries/${id}${review ? `?submit_for_review=true${note ? `&review_note=${encodeURIComponent(note)}` : ''}` : ''}`, { method: 'DELETE' }), 'Entry removed.');
    }
  });
});
