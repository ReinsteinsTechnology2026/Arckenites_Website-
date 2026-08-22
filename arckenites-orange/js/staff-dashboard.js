const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const BATCH_STATUS_BADGE = { upcoming: 'is-info', active: 'is-success', paused: 'is-pending', completed: 'is-muted', cancelled: 'is-danger' };
const BATCH_STATUS_LABEL = { upcoming: 'Upcoming', active: 'Active', paused: 'Paused', completed: 'Completed', cancelled: 'Cancelled' };

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('staff');
  if (!user) return; // requireRole already redirected

  document.getElementById('staffWelcome').textContent = `Welcome, ${user.full_name}`;
  document.getElementById('staffLogoutBtn').addEventListener('click', () => ArckAuth.logout());

  const grid = document.getElementById('batchCardsGrid');

  const cardHtml = (b) => `
    <button type="button" class="batch-card" data-open-batch="${b.id}">
      <div class="batch-card-top">
        <div class="batch-card-icon"><i class="fa-solid fa-people-group"></i></div>
        <span class="admin-activity-badge ${BATCH_STATUS_BADGE[b.status] || 'is-muted'}">${BATCH_STATUS_LABEL[b.status] || b.status}</span>
      </div>
      <div>
        <h3>${escapeHtml(b.name)}</h3>
        <p class="batch-card-program">${b.program_name ? escapeHtml(b.program_name) : (b.course ? escapeHtml(b.course) : 'No program linked')}</p>
      </div>
      <div class="batch-card-footer">
        <span><i class="fa-solid fa-user-graduate"></i> ${b.student_count}${b.max_capacity ? ` / ${b.max_capacity}` : ''} students</span>
        ${b.unread_chat_count > 0 ? `<span class="admin-notif-badge">${b.unread_chat_count > 9 ? '9+' : b.unread_chat_count}</span>` : ''}
      </div>
    </button>
  `;

  const loadBatches = async () => {
    try {
      const batches = await ArckAPI.request('/staff/me/batches');
      grid.innerHTML = batches.length
        ? batches.map(cardHtml).join('')
        : '<div class="admin-panel-empty">You have no batches assigned yet. Check back once admin allocates one to you.</div>';
    } catch (_) {
      grid.innerHTML = '<div class="admin-panel-empty">Couldn\'t load your batches.</div>';
    }
  };

  grid.addEventListener('click', (e) => {
    const card = e.target.closest('[data-open-batch]');
    if (!card) return;
    window.location.href = `staff-batch-workspace.html?id=${card.dataset.openBatch}`;
  });

  await loadBatches();

});
