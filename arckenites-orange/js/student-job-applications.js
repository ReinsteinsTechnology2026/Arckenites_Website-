const StudentJobApplications = (() => {
  const reactions = [
    ['like', '👍', 'Like'], ['love', '❤️', 'Love'],
    ['celebrate', '🎉', 'Celebrate'], ['interested', '👀', 'Interested'],
  ];
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  const safeUrl = (value) => {
    try {
      const url = new URL(value);
      return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
    } catch (_) { return null; }
  };
  const date = (value) => value ? new Date(value.slice(0, 10) + 'T00:00:00').toLocaleDateString() : null;
  const list = () => document.getElementById('studentJobsList');

  const card = (job) => {
    const link = safeUrl(job.application_url);
    const buttons = reactions.map(([kind, emoji, label]) => `
      <button type="button" class="job-reaction ${job.my_reaction === kind ? 'is-selected' : ''}"
        data-job-id="${job.id}" data-reaction="${kind}" aria-pressed="${job.my_reaction === kind}"
        aria-label="${label}: ${job.reaction_counts[kind] || 0}">${emoji} ${job.reaction_counts[kind] || 0}</button>`).join('');
    return `<article class="admin-panel job-card">
      <div class="job-card-heading"><div><h2>${escape(job.title)}</h2><strong>${escape(job.company)}</strong></div>
        <span class="admin-activity-badge is-success">Published</span></div>
      <p>${escape(job.description)}</p>
      ${job.eligibility ? `<p><strong>Eligibility:</strong> ${escape(job.eligibility)}</p>` : ''}
      <div class="job-card-meta">${job.deadline ? `<span>Deadline: ${date(job.deadline)}</span>` : ''}
        ${job.published_at ? `<span>Published: ${date(job.published_at)}</span>` : ''}</div>
      <div class="job-card-actions">${link ? `<a class="btn btn-accent" href="${escape(link)}" target="_blank" rel="noopener noreferrer">Apply Now <i class="fa-solid fa-arrow-up-right-from-square"></i></a>` : ''}
        <div class="job-reactions" aria-label="Emoji reactions">${buttons}</div></div>
    </article>`;
  };

  const load = async () => {
    const target = list();
    target.innerHTML = '<div class="admin-panel-empty">Loading job applications&hellip;</div>';
    try {
      const jobs = [];
      let page;
      do {
        page = await ArckAPI.request(`/student/job-applications?offset=${jobs.length}&limit=100`);
        jobs.push(...page);
      } while (page.length === 100);
      target.innerHTML = jobs.length ? jobs.map(card).join('')
        : '<div class="admin-panel-empty">No job applications are available at the moment.</div>';
    } catch (error) {
      target.innerHTML = error.status === 403
        ? '<div class="admin-panel-empty">Job Applications is currently unavailable.</div>'
        : '<div class="admin-panel-empty">Could not load job applications. Please try again.</div>';
    }
  };

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('#studentJobsList [data-reaction]');
    if (!button || button.disabled) return;
    const kind = button.dataset.reaction;
    const id = Number(button.dataset.jobId);
    if (!Number.isSafeInteger(id) || !reactions.some(([value]) => value === kind)) return;
    const current = button.getAttribute('aria-pressed') === 'true';
    list().querySelectorAll('[data-reaction]').forEach((item) => { item.disabled = true; });
    try {
      await ArckAPI.request(`/student/job-applications/${id}/reaction`, current
        ? {method: 'DELETE'} : {method: 'PUT', body: {kind}});
      await load();
    } catch (error) {
      await load();
      const notice = document.createElement('p');
      notice.className = 'student-account-status';
      notice.textContent = 'Could not update your reaction. The announcement may no longer be available.';
      list().prepend(notice);
    }
  });

  return {load};
})();
