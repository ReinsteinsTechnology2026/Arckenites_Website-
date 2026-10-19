const escapeJobText = (value) => String(value ?? '').replace(/[&<>"']/g, (char) =>
  ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));

document.addEventListener('DOMContentLoaded', async () => {
  const user = await ArckAuth.requireRole('admin');
  if (!user) return;
  const canCreate = ArckAuth.hasPermission('placement.create');
  const canEdit = ArckAuth.hasPermission('placement.edit');
  const list = document.getElementById('adminJobsList');
  const panel = document.getElementById('jobFormPanel');
  const form = document.getElementById('jobForm');
  const message = document.getElementById('jobActionStatus');
  let jobs = [];
  let editing = null;

  document.getElementById('adminAvatarInitials').textContent = user.full_name
    .split(' ').filter(Boolean).slice(0, 2).map((part) => part[0].toUpperCase()).join('') || 'A';
  document.getElementById('adminProfileName').textContent = user.full_name;
  document.getElementById('adminProfileRole').textContent = user.role;
  document.querySelectorAll('.admin-sidebar-group-header').forEach((header) => header.addEventListener('click', () => {
    const open = header.closest('.admin-sidebar-group').classList.toggle('is-open');
    header.setAttribute('aria-expanded', String(open));
  }));
  const sidebar = document.getElementById('adminSidebar');
  const backdrop = document.getElementById('adminSidebarBackdrop');
  document.getElementById('adminSidebarCollapseBtn').addEventListener('click', () => sidebar.classList.toggle('is-collapsed'));
  document.getElementById('adminMobileToggle').addEventListener('click', () => { sidebar.classList.add('is-mobile-open'); backdrop.classList.add('is-visible'); });
  backdrop.addEventListener('click', () => { sidebar.classList.remove('is-mobile-open'); backdrop.classList.remove('is-visible'); });
  document.getElementById('adminProfileTrigger').addEventListener('click', (event) => {
    event.stopPropagation(); document.getElementById('adminProfilePanel').classList.toggle('is-open');
  });
  document.addEventListener('click', () => document.getElementById('adminProfilePanel').classList.remove('is-open'));
  document.getElementById('adminSidebarLogout').addEventListener('click', () => ArckAuth.logout());
  document.getElementById('adminProfileLogout').addEventListener('click', () => ArckAuth.logout());
  if (!canCreate) document.getElementById('newJobBtn').style.display = 'none';

  const openForm = (job = null) => {
    editing = job;
    form.reset();
    document.getElementById('jobFormError').style.display = 'none';
    document.getElementById('jobFormTitle').textContent = job ? 'Edit Job Application' : 'New Job Application';
    document.getElementById('jobSaveBtn').textContent = job ? 'Save Changes' : 'Save Draft';
    if (job) {
      document.getElementById('jobTitle').value = job.title;
      document.getElementById('jobCompany').value = job.company;
      document.getElementById('jobDescription').value = job.description;
      document.getElementById('jobEligibility').value = job.eligibility || '';
      document.getElementById('jobUrl').value = job.application_url;
      document.getElementById('jobDeadline').value = job.deadline || '';
    }
    panel.style.display = 'block';
    panel.scrollIntoView({behavior: 'smooth', block: 'start'});
  };
  document.getElementById('newJobBtn').addEventListener('click', () => openForm());
  document.getElementById('jobCancelBtn').addEventListener('click', () => { panel.style.display = 'none'; });

  const statusText = (job) => job.status === 'archived' ? 'Archived'
    : job.status === 'draft' ? 'Draft' : job.is_enabled ? 'Published and enabled' : 'Disabled';
  const actions = (job) => {
    if (!canEdit || job.status === 'archived') return '';
    const available = job.status === 'draft' ? ['publish', 'archive']
      : job.is_enabled ? ['disable', 'unpublish', 'archive'] : ['enable', 'unpublish', 'archive'];
    return `<button type="button" class="btn btn-primary-outline" data-job-action="edit" data-id="${job.id}">Edit</button>`
      + available.map((action) => `<button type="button" class="btn btn-primary-outline" data-job-action="${action}" data-id="${job.id}">${action[0].toUpperCase() + action.slice(1)}</button>`).join('');
  };
  const card = (job) => {
    const count = Object.values(job.reaction_counts).reduce((total, value) => total + value, 0);
    return `<article class="admin-panel job-card">
      <div class="job-card-heading"><div><h2>${escapeJobText(job.title)}</h2><strong>${escapeJobText(job.company)}</strong></div>
        <span class="admin-activity-badge ${job.status === 'published' && job.is_enabled ? 'is-success' : 'is-muted'}">${statusText(job)}</span></div>
      <p>${escapeJobText(job.description)}</p>
      ${job.eligibility ? `<p><strong>Eligibility:</strong> ${escapeJobText(job.eligibility)}</p>` : ''}
      <div class="job-card-meta"><span>Deadline: ${job.deadline || 'Not set'}</span><span>Reactions: ${count}</span>
        ${job.published_at ? `<span>Published: ${new Date(job.published_at).toLocaleDateString()}</span>` : ''}</div>
      <div class="job-card-actions">${actions(job)}</div>
    </article>`;
  };
  const load = async () => {
    try {
      jobs = [];
      let page;
      do {
        page = await ArckAPI.request(`/admin/job-applications?offset=${jobs.length}&limit=100`);
        jobs.push(...page);
      } while (page.length === 100);
      list.innerHTML = jobs.length ? jobs.map(card).join('')
        : '<div class="admin-panel-empty">No job applications have been created yet.</div>';
    } catch (error) {
      list.innerHTML = '<div class="admin-panel-empty">Could not load job applications.</div>';
    }
  };

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const error = document.getElementById('jobFormError');
    if (!form.reportValidity()) return;
    const payload = {
      title: document.getElementById('jobTitle').value,
      company: document.getElementById('jobCompany').value,
      description: document.getElementById('jobDescription').value,
      eligibility: document.getElementById('jobEligibility').value || null,
      application_url: document.getElementById('jobUrl').value,
      deadline: document.getElementById('jobDeadline').value || null,
    };
    const button = document.getElementById('jobSaveBtn');
    button.disabled = true;
    try {
      await ArckAPI.request(editing ? `/admin/job-applications/${editing.id}` : '/admin/job-applications',
        {method: editing ? 'PATCH' : 'POST', body: payload});
      panel.style.display = 'none';
      message.textContent = editing ? 'Announcement updated.' : 'Draft saved. Publish it when ready.';
      await load();
    } catch (err) {
      error.textContent = ArckAPI.errorMessage(err, 'Could not save announcement.');
      error.style.display = 'block';
    } finally { button.disabled = false; }
  });

  list.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-job-action]');
    if (!button || button.disabled || !canEdit) return;
    const job = jobs.find((item) => item.id === Number(button.dataset.id));
    if (!job) return;
    if (button.dataset.jobAction === 'edit') { openForm(job); return; }
    button.disabled = true;
    try {
      await ArckAPI.request(`/admin/job-applications/${job.id}/status`,
        {method: 'POST', body: {action: button.dataset.jobAction}});
      message.textContent = `Announcement ${({publish: 'published', unpublish: 'unpublished', enable: 'enabled', disable: 'disabled', archive: 'archived'})[button.dataset.jobAction]}.`;
      await load();
    } catch (error) {
      message.textContent = ArckAPI.errorMessage(error, 'Could not change announcement status.');
      button.disabled = false;
    }
  });
  await load();
});
