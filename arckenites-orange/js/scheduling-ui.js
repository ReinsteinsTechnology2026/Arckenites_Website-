/* Small UI helpers shared by the scheduling pages: accessible modal, toast, formatting. */
const SchedUI = (() => {
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const time12 = (hhmm) => {
    if (!hhmm) return '—';
    const [h, m] = hhmm.split(':').map(Number);
    return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
  };
  const fmtDate = (iso) => iso ? new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }) : '—';
  const fmtDateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
  const newKey = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '') : `${Date.now()}${Math.random().toString(16).slice(2)}`).slice(0, 48);

  function toast(message, isError = false) {
    const host = document.getElementById('toasts');
    if (!host) return;
    const el = document.createElement('div');
    el.className = isError ? 'is-error' : '';
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => el.remove(), isError ? 8000 : 4500);
  }

  /** Opens a modal. buttons: [{label, kind: 'accent'|'outline'|'danger', onClick(close, btn), keepOpen}] */
  function modal({ title, body, buttons = [], width }) {
    const previous = document.activeElement;
    const back = document.createElement('div');
    back.className = 'modal-backdrop';
    back.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="mdlTitle" ${width ? `style="width:${width}px"` : ''}>
      <div class="modal-header"><h3 id="mdlTitle">${esc(title)}</h3><button type="button" class="icon-btn" data-x aria-label="Close dialog"><i class="fa-solid fa-xmark"></i></button></div>
      <div class="modal-body"></div><div class="modal-footer"></div></div>`;
    const bodyEl = back.querySelector('.modal-body');
    if (typeof body === 'string') bodyEl.innerHTML = body; else bodyEl.appendChild(body);
    const foot = back.querySelector('.modal-footer');
    const close = () => { back.classList.remove('is-open'); setTimeout(() => back.remove(), 200); document.removeEventListener('keydown', onKey); if (previous && previous.focus) previous.focus(); };
    const focusables = () => Array.from(back.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')).filter((e) => !e.disabled && e.offsetParent !== null);
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); close(); }
      if (e.key === 'Tab') { const f = focusables(); if (!f.length) return; const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); } }
    };
    buttons.forEach((b) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = b.kind === 'accent' ? 'btn btn-accent' : (b.kind === 'danger' ? 'btn btn-accent' : 'btn btn-primary-outline');
      if (b.kind === 'danger') btn.style.background = 'var(--danger)';
      btn.textContent = b.label;
      btn.addEventListener('click', async () => {
        if (b.keepOpen === undefined && !b.onClick) return close();
        btn.disabled = true;
        try { const r = await b.onClick(close, btn); if (r !== false && !b.keepOpen) close(); } finally { btn.disabled = false; }
      });
      foot.appendChild(btn);
    });
    back.querySelector('[data-x]').addEventListener('click', close);
    back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(back);
    requestAnimationFrame(() => { back.classList.add('is-open'); const f = back.querySelector('input:not([type=hidden]), select, textarea') || focusables()[1] || focusables()[0]; if (f) f.focus(); });
    return { close, el: back, body: bodyEl };
  }

  const confirmDialog = ({ title, message, confirmLabel = 'Confirm', danger = false }) => new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    const m = modal({ title, body: `<p style="margin:0;color:var(--ink)">${message}</p>`, buttons: [
      { label: 'Cancel', kind: 'outline', onClick: (close) => { done(false); close(); }, keepOpen: true },
      { label: confirmLabel, kind: danger ? 'danger' : 'accent', onClick: (close) => { done(true); close(); }, keepOpen: true },
    ] });
    m.el.querySelector('[data-x]').addEventListener('click', () => done(false));
  });

  const conflictList = (conflicts = []) => conflicts.length ? `<ul class="sched-conflicts">${conflicts.map((c) =>
    `<li><b>${esc(c.batch_name || c.message || c.code)}</b>${c.date ? ` — ${fmtDate(c.date)}, ${time12(c.start_time)}–${time12(c.end_time)}` : ''}${c.detail ? ` <span class="muted">(${esc(c.detail)})</span>` : ''}</li>`).join('')}</ul>` : '';

  return { esc, DAYS, time12, fmtDate, fmtDateTime, newKey, toast, modal, confirmDialog, conflictList };
})();
