/* ============================================================
   ARCKENITES PORTAL — SCHEDULING CALENDAR
   Day / week / month calendar shared by the trainer "My Availability"
   page and the admin "Trainer Scheduling" page. It only renders the
   payload built by the server (segments per business-timezone date),
   so availability rules live in exactly one place: the backend.
   ============================================================ */
const SchedCal = (() => {
  const SLOT = 30;            // minutes per selectable slot
  const PX_PER_MIN = 0.8;     // 48px per hour
  const STATE_LABEL = {
    available: 'Available', preferred: 'Preferred', unavailable: 'Unavailable', leave: 'Leave / holiday',
    booked: 'Booked session', requested: 'Admin requested update',
  };

  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const parse = (iso) => new Date(`${iso}T12:00:00Z`);               // noon UTC: immune to DST arithmetic
  const fmt = (d) => d.toISOString().slice(0, 10);
  const addDays = (iso, n) => { const d = parse(iso); d.setUTCDate(d.getUTCDate() + n); return fmt(d); };
  const mondayOf = (iso) => addDays(iso, -((parse(iso).getUTCDay() + 6) % 7));
  const toMin = (hhmm) => { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m; };
  const toHHMM = (min) => `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
  const time12 = (hhmm) => {
    if (hhmm === '24:00') return '12:00 AM';
    const [h, m] = hhmm.split(':').map(Number);
    return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
  };
  const dayName = (iso, opts) => parse(iso).toLocaleDateString(undefined, { timeZone: 'UTC', ...opts });

  const todayIn = (tz) => new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const nowMinIn = (tz) => {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date());
    return Number(parts.find((p) => p.type === 'hour').value) * 60 + Number(parts.find((p) => p.type === 'minute').value);
  };

  function mount(root, opts) {
    const state = {
      view: opts.view || 'week', tz: opts.timezone || 'Asia/Kolkata', payload: null, loading: false,
      anchor: opts.anchor || todayIn(opts.timezone || 'Asia/Kolkata'),
    };
    const editable = !!opts.editable && typeof opts.onSlotSelect === 'function';
    const uid = `sc${Math.random().toString(36).slice(2, 7)}`;

    const showToolbar = opts.toolbar !== false;
    root.innerHTML = `
      ${showToolbar ? `<div class="sched-toolbar" role="toolbar" aria-label="Calendar navigation">
        <button type="button" class="sched-btn" data-nav="-1" aria-label="Previous period"><i class="fa-solid fa-chevron-left"></i></button>
        <button type="button" class="sched-btn" data-nav="0">Today</button>
        <button type="button" class="sched-btn" data-nav="1" aria-label="Next period"><i class="fa-solid fa-chevron-right"></i></button>
        <div class="sched-title" aria-live="polite" id="${uid}-title"></div>
        <label class="sr-only" for="${uid}-jump">Jump to date</label>
        <input type="date" class="form-control" id="${uid}-jump" style="width:auto;">
        <div class="spacer"></div>
        <div class="sched-seg" role="group" aria-label="Calendar view">
          <button type="button" data-view="day" aria-pressed="false">Day</button>
          <button type="button" data-view="week" aria-pressed="false">Week</button>
          <button type="button" data-view="month" aria-pressed="false">Month</button>
        </div>
      </div>` : ''}
      <div class="sched-cal" id="${uid}-cal"><div class="sched-cal-loading" role="status">Loading calendar…</div><div id="${uid}-body"></div></div>`;
    const body = root.querySelector(`#${uid}-body`);
    const cal = root.querySelector(`#${uid}-cal`);

    const range = () => {
      if (state.view === 'day') return [state.anchor, state.anchor];
      if (state.view === 'week') { const s = mondayOf(state.anchor); return [s, addDays(s, 6)]; }
      const first = `${state.anchor.slice(0, 7)}-01`; const s = mondayOf(first); return [s, addDays(s, 41)];
    };

    const title = () => {
      const [s, e] = range();
      if (state.view === 'day') return dayName(s, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
      if (state.view === 'week') return `${dayName(s, { day: 'numeric', month: 'short' })} – ${dayName(e, { day: 'numeric', month: 'short', year: 'numeric' })}`;
      return dayName(`${state.anchor.slice(0, 7)}-01`, { month: 'long', year: 'numeric' });
    };

    async function load() {
      state.loading = true; cal.classList.add('is-loading');
      const [s, e] = range();
      const titleEl = root.querySelector(`#${uid}-title`); if (titleEl) titleEl.textContent = title();
      const jumpEl = root.querySelector(`#${uid}-jump`); if (jumpEl) jumpEl.value = state.anchor;
      root.querySelectorAll('[data-view]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === state.view)));
      try {
        state.payload = await opts.load(s, e);
        if (state.payload && state.payload.timezone) state.tz = state.payload.timezone;
        render();
        if (opts.onLoaded) opts.onLoaded(state.payload, { start: s, end: e, view: state.view });
      } catch (err) {
        body.innerHTML = '<div class="admin-panel-empty">Couldn\'t load the calendar. Check your connection and try again.</div>';
        if (opts.onError) opts.onError(err);
      } finally { state.loading = false; cal.classList.remove('is-loading'); }
    }

    function segmentsByDate() {
      const map = new Map();
      (state.payload ? state.payload.segments : []).forEach((s) => { if (!map.has(s.date)) map.set(s.date, []); map.get(s.date).push(s); });
      return map;
    }

    function segHtml(s) {
      const a = toMin(s.start_time), b = toMin(s.end_time);
      const style = `top:${a * PX_PER_MIN}px;height:${Math.max((b - a) * PX_PER_MIN, 14)}px`;
      const label = s.label || STATE_LABEL[s.state];
      const text = `${STATE_LABEL[s.state]}${s.label && s.state !== 'available' && s.state !== 'preferred' ? ` — ${s.label}` : ''}: ${time12(s.start_time)} to ${time12(s.end_time)}`;
      const interactive = (s.state === 'unavailable' || s.state === 'leave') && s.ref_id && opts.onSegmentClick;
      const short = (b - a) >= 45 ? `${esc(label)}<small>${time12(s.start_time)} – ${time12(s.end_time)}</small>` : '';
      if (interactive) {
        return `<button type="button" class="sched-seg-item st-${s.state}" style="${style}" data-ref="${s.ref_id}" data-state="${s.state}" aria-label="${esc(text)}. Edit." title="${esc(text)}">${short}</button>`;
      }
      return `<div class="sched-seg-item st-${s.state}" style="${style}" role="img" aria-label="${esc(text)}" title="${esc(text)}">${state.view !== 'month' && s.state !== 'available' && s.state !== 'preferred' ? short : ''}</div>`;
    }

    function renderGrid() {
      const [s, e] = range();
      const dates = []; for (let d = s; d <= e; d = addDays(d, 1)) dates.push(d);
      const today = todayIn(state.tz);
      const bySeg = segmentsByDate();
      const head = dates.map((d) => `<div class="${d === today ? 'is-today' : ''}">${dayName(d, { weekday: 'short' })}<b>${parse(d).getUTCDate()}</b></div>`).join('');
      const hours = Array.from({ length: 24 }, (_, h) => `<div>${h === 0 ? '' : time12(`${String(h).padStart(2, '0')}:00`)}</div>`).join('');
      const cols = dates.map((d) => {
        const segs = (bySeg.get(d) || []).map(segHtml).join('');
        const now = d === today ? `<div class="sched-now" style="top:${nowMinIn(state.tz) * PX_PER_MIN}px" aria-hidden="true"></div>` : '';
        const hint = editable ? `${dayName(d, { weekday: 'long', day: 'numeric', month: 'long' })}. Use arrow keys to choose a time, Shift plus arrows to extend, Enter to add availability.` : dayName(d, { weekday: 'long', day: 'numeric', month: 'long' });
        return `<div class="sched-col ${d === today ? 'is-today' : ''} ${editable ? '' : 'is-readonly'}" data-date="${d}" ${editable ? 'tabindex="0"' : ''} aria-label="${esc(hint)}">${segs}${now}</div>`;
      }).join('');
      body.innerHTML = `<div class="sched-grid-head" style="--cols:${dates.length}"><div></div>${head}</div>
        <div class="sched-grid-scroll" tabindex="-1"><div class="sched-grid" style="--cols:${dates.length}"><div class="sched-hours" aria-hidden="true">${hours}</div>${cols}</div></div>`;
      const scroller = body.querySelector('.sched-grid-scroll');
      scroller.scrollTop = 7 * 60 * PX_PER_MIN;
      body.querySelectorAll('[data-ref]').forEach((el) => el.addEventListener('click', (ev) => { ev.stopPropagation(); opts.onSegmentClick({ id: Number(el.dataset.ref), state: el.dataset.state }); }));
      if (editable) body.querySelectorAll('.sched-col').forEach(wireSelection);
    }

    function selectionBox(col, startMin, endMin) {
      let box = col.querySelector('.sched-selection');
      if (!box) { box = document.createElement('div'); box.className = 'sched-selection'; box.setAttribute('aria-hidden', 'true'); col.appendChild(box); }
      box.style.top = `${startMin * PX_PER_MIN}px`; box.style.height = `${(endMin - startMin) * PX_PER_MIN}px`;
      box.textContent = `${time12(toHHMM(startMin))} – ${time12(toHHMM(endMin))}`;
    }
    const clearSelection = () => body.querySelectorAll('.sched-selection').forEach((b) => b.remove());

    function wireSelection(col) {
      const snap = (y) => Math.min(1440, Math.max(0, Math.round((y / PX_PER_MIN) / SLOT) * SLOT));
      let anchorMin = null;
      col.addEventListener('pointerdown', (ev) => {
        if (ev.button !== 0 || ev.target.closest('[data-ref]') || ev.pointerType === 'touch') return;
        anchorMin = snap(ev.clientY - col.getBoundingClientRect().top);
        col.setPointerCapture(ev.pointerId);
        selectionBox(col, Math.min(anchorMin, 1410), Math.min(anchorMin, 1410) + SLOT);
      });
      col.addEventListener('pointermove', (ev) => {
        if (anchorMin === null) return;
        const cur = snap(ev.clientY - col.getBoundingClientRect().top);
        const lo = Math.min(anchorMin, cur), hi = Math.max(anchorMin, cur);
        selectionBox(col, lo, Math.max(hi, lo + SLOT));
      });
      col.addEventListener('pointerup', (ev) => {
        if (anchorMin === null) return;
        const cur = snap(ev.clientY - col.getBoundingClientRect().top);
        const lo = Math.min(anchorMin, cur); const hi = Math.max(Math.max(anchorMin, cur), lo + SLOT);
        anchorMin = null; clearSelection();
        opts.onSlotSelect({ date: col.dataset.date, start: toHHMM(Math.min(lo, 1410)), end: hi >= 1440 ? '23:59' : toHHMM(hi) });
      });
      col.addEventListener('pointercancel', () => { anchorMin = null; clearSelection(); });

      let kb = null;
      col.addEventListener('focus', () => { if (!kb) { kb = { start: 9 * 60, end: 9 * 60 + SLOT }; } selectionBox(col, kb.start, kb.end); });
      col.addEventListener('blur', () => { clearSelection(); });
      col.addEventListener('keydown', (ev) => {
        if (!kb) kb = { start: 9 * 60, end: 9 * 60 + SLOT };
        const step = { ArrowDown: SLOT, ArrowUp: -SLOT }[ev.key];
        if (step) {
          ev.preventDefault();
          if (ev.shiftKey) kb.end = Math.min(1440, Math.max(kb.start + SLOT, kb.end + step));
          else { const len = kb.end - kb.start; kb.start = Math.min(1440 - len, Math.max(0, kb.start + step)); kb.end = kb.start + len; }
          selectionBox(col, kb.start, kb.end);
          const top = kb.start * PX_PER_MIN; const sc = col.closest('.sched-grid-scroll');
          if (top < sc.scrollTop || top > sc.scrollTop + sc.clientHeight - 60) sc.scrollTop = top - 120;
        } else if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          opts.onSlotSelect({ date: col.dataset.date, start: toHHMM(kb.start), end: kb.end >= 1440 ? '23:59' : toHHMM(kb.end) });
        } else if (ev.key === 'Escape') { kb = null; clearSelection(); }
      });
    }

    function renderMonth() {
      const [s] = range();
      const month = state.anchor.slice(0, 7);
      const today = todayIn(state.tz);
      const bySeg = segmentsByDate();
      const dows = Array.from({ length: 7 }, (_, i) => `<div class="sched-month-dow">${dayName(addDays(s, i), { weekday: 'short' })}</div>`).join('');
      const cells = Array.from({ length: 42 }, (_, i) => {
        const d = addDays(s, i); const segs = bySeg.get(d) || [];
        const mins = (st) => segs.filter((x) => st.includes(x.state)).reduce((t, x) => t + toMin(x.end_time) - toMin(x.start_time), 0);
        const open = mins(['available', 'preferred']); const booked = segs.filter((x) => x.state === 'booked').length;
        const blocked = segs.find((x) => x.state === 'leave' || x.state === 'unavailable');
        const chips = [
          open ? `<span class="sched-chip st-available">${(open / 60).toFixed(open % 60 ? 1 : 0)} h open</span>` : '',
          booked ? `<span class="sched-chip st-booked">${booked} session${booked > 1 ? 's' : ''}</span>` : '',
          blocked ? `<span class="sched-chip st-${blocked.state}">${esc(blocked.label || STATE_LABEL[blocked.state])}</span>` : '',
        ].join('');
        const sr = `${dayName(d, { weekday: 'long', day: 'numeric', month: 'long' })}: ${(open / 60).toFixed(1)} hours open, ${booked} sessions${blocked ? ', ' + STATE_LABEL[blocked.state] : ''}. Open day view.`;
        return `<button type="button" class="sched-day ${d.slice(0, 7) !== month ? 'is-outside' : ''} ${d === today ? 'is-today' : ''}" data-day="${d}" aria-label="${esc(sr)}"><span class="sched-day-num">${parse(d).getUTCDate()}</span>${chips}</button>`;
      }).join('');
      body.innerHTML = `<div class="sched-month">${dows}${cells}</div>`;
      body.querySelectorAll('[data-day]').forEach((b) => b.addEventListener('click', () => { state.anchor = b.dataset.day; state.view = 'day'; load(); }));
    }

    function render() { if (state.view === 'month') renderMonth(); else renderGrid(); }

    root.querySelectorAll('[data-nav]').forEach((b) => b.addEventListener('click', () => {
      const n = Number(b.dataset.nav);
      if (n === 0) state.anchor = todayIn(state.tz);
      else if (state.view === 'month') { const d = parse(`${state.anchor.slice(0, 7)}-01`); d.setUTCMonth(d.getUTCMonth() + n); state.anchor = fmt(d); }
      else state.anchor = addDays(state.anchor, n * (state.view === 'week' ? 7 : 1));
      load();
    }));
    root.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => { state.view = b.dataset.view; load(); }));
    const jump = root.querySelector(`#${uid}-jump`);
    if (jump) jump.addEventListener('change', (ev) => { if (ev.target.value) { state.anchor = ev.target.value; load(); } });

    load();
    return { reload: load, setAnchor(d) { state.anchor = d; return load(); }, get view() { return state.view; }, get range() { return range(); }, get timezone() { return state.tz; } };
  }

  const legendHtml = () => `<ul class="sched-legend" aria-label="Calendar legend">${
    ['available', 'preferred', 'unavailable', 'leave', 'booked', 'requested'].map((k) => `<li><span class="sched-swatch st-${k}"></span>${STATE_LABEL[k]}</li>`).join('')}</ul>`;

  return { mount, legendHtml, esc, time12, STATE_LABEL, addDays, mondayOf, todayIn };
})();
