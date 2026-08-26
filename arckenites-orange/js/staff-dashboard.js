const escapeHtml = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const BATCH_STATUS_BADGE = { upcoming: 'is-info', active: 'is-success', paused: 'is-pending', completed: 'is-muted', cancelled: 'is-danger' };
const BATCH_STATUS_LABEL = { upcoming: 'Upcoming', active: 'Active', paused: 'Paused', completed: 'Completed', cancelled: 'Cancelled' };

document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('staff');
  if (!user) return; // requireRole already redirected

  /* ---------- Topbar profile dropdown (photo + name, same pattern as the admin portal) ---------- */
  const renderTopbarProfile = () => {
    document.getElementById('staffAvatarWrap').innerHTML = ArckAPI.avatarHtml(user.full_name, user.photo_url, 36);
    document.getElementById('staffAvatarWrapMenu').innerHTML = ArckAPI.avatarHtml(user.full_name, user.photo_url, 40);
    document.getElementById('staffProfileName').textContent = user.full_name;
  };
  renderTopbarProfile();

  const profileTrigger = document.getElementById('staffProfileTrigger');
  const profilePanel = document.getElementById('staffProfilePanel');
  profileTrigger.addEventListener('click', (e) => {
    e.stopPropagation();
    profilePanel.classList.toggle('is-open');
  });
  document.addEventListener('click', () => profilePanel.classList.remove('is-open'));
  document.getElementById('staffProfileLogout').addEventListener('click', () => ArckAuth.logout());

  /* ---------- Profile view (reached only via the dropdown, not the main page) ---------- */
  const batchesView = document.getElementById('staffBatchesView');
  const profileSettingsView = document.getElementById('staffProfileSettingsView');
  const openProfileView = () => {
    profilePanel.classList.remove('is-open');
    batchesView.style.display = 'none';
    profileSettingsView.style.display = 'block';
  };
  document.getElementById('staffProfileSettingsLink').addEventListener('click', openProfileView);
  document.getElementById('staffBackToBatchesBtn').addEventListener('click', () => {
    profileSettingsView.style.display = 'none';
    batchesView.style.display = 'block';
  });

  // Clicking the avatar (topbar or dropdown menu) goes to the Profile view —
  // it does NOT upload a photo directly. Uploading only happens from the
  // dedicated avatar control inside the Profile view itself.
  document.getElementById('staffAvatarBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    openProfileView();
  });
  document.getElementById('staffAvatarMenuBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    openProfileView();
  });

  // A batch-workspace/support page's avatar links here with ?openProfile=1
  // since those pages have no Profile view of their own.
  if (new URLSearchParams(window.location.search).get('openProfile')) {
    openProfileView();
  }

  // One shared, hidden file input for the whole page — only the avatar
  // control inside the Profile view opens it. Wiring `change`/upload here
  // ONCE (rather than inside a render function that re-runs on every photo
  // change) matters: this element is never recreated, so re-attaching the
  // listener there would stack duplicate uploads per file picked.
  const photoInput = document.getElementById('profilePhotoInput');
  const uploadPhoto = async (file) => {
    try {
      const result = await ArckAPI.uploadFile('/users/me/photo', file);
      user.photo_url = result.photo_url;
      ArckAPI.setSession(ArckAPI.getToken(), user);
      renderTopbarProfile();
      renderProfilePhoto();
    } catch (err) {
      alert(err.message || 'Could not upload photo.');
    }
  };
  const removePhoto = async () => {
    try {
      await ArckAPI.request('/users/me/photo', { method: 'DELETE' });
      user.photo_url = null;
      ArckAPI.setSession(ArckAPI.getToken(), user);
      renderTopbarProfile();
      renderProfilePhoto();
    } catch (err) {
      alert(err.message || 'Could not remove photo.');
    }
  };
  photoInput.addEventListener('change', () => {
    const file = photoInput.files[0];
    photoInput.value = '';
    if (file) uploadPhoto(file);
  });

  // My Profile — the trainer's OWN view (via /auth/me); nowhere else in the
  // portal shows a trainer's username/email/phone, including to students.
  const renderProfilePhoto = () => {
    document.getElementById('profilePhotoTrigger').innerHTML = ArckAPI.avatarHtml(user.full_name, user.photo_url, 96);
    document.getElementById('profilePhotoCaption').textContent = user.photo_url ? 'Change Photo' : 'Upload Photo';
    document.getElementById('profilePhotoRemoveBtn').style.display = user.photo_url ? 'inline-block' : 'none';
  };
  document.getElementById('profilePhotoTrigger').addEventListener('click', () => photoInput.click());
  document.getElementById('profilePhotoCaption').addEventListener('click', () => photoInput.click());
  document.getElementById('profilePhotoRemoveBtn').addEventListener('click', removePhoto);
  renderProfilePhoto();

  // Name/Email/Phone/Address start disabled (view-only); "Edit" enables
  // them, and the same button (now labeled "Save") submits the PATCH.
  const nameInput = document.getElementById('profileNameInput');
  const emailInput = document.getElementById('profileEmailInput');
  const phoneInput = document.getElementById('profilePhoneInput');
  const addressInput = document.getElementById('profileAddressInput');
  const editSaveBtn = document.getElementById('profileEditSaveBtn');
  const editError = document.getElementById('profileEditError');
  const editableInputs = [nameInput, emailInput, phoneInput, addressInput];
  let isEditingProfile = false;

  const fillProfileFormFromUser = () => {
    nameInput.value = user.full_name || '';
    emailInput.value = user.email || '';
    phoneInput.value = user.phone || '';
    addressInput.value = user.address || '';
  };
  fillProfileFormFromUser();

  document.getElementById('profileEditForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    editError.style.display = 'none';

    if (!isEditingProfile) {
      isEditingProfile = true;
      editableInputs.forEach((input) => { input.disabled = false; });
      editSaveBtn.textContent = 'Save';
      nameInput.focus();
      return;
    }

    editSaveBtn.disabled = true;
    try {
      const updated = await ArckAPI.request('/staff/me/profile', {
        method: 'PATCH',
        body: {
          full_name: nameInput.value.trim(),
          email: emailInput.value.trim() || null,
          phone: phoneInput.value.trim() || null,
          address: addressInput.value.trim() || null,
        },
      });
      Object.assign(user, updated);
      ArckAPI.setSession(ArckAPI.getToken(), user);
      renderTopbarProfile();
      renderAccountDetails();
      fillProfileFormFromUser();
      isEditingProfile = false;
      editableInputs.forEach((input) => { input.disabled = true; });
      editSaveBtn.textContent = 'Edit';
    } catch (err) {
      editError.textContent = err.message || 'Could not save your profile.';
      editError.style.display = 'block';
    } finally {
      editSaveBtn.disabled = false;
    }
  });

  // Read-only account info that isn't user-editable here — kept separate
  // from the editable form above.
  const renderAccountDetails = () => {
    document.getElementById('profileAccountDetailsBody').innerHTML = `
      <div class="detail-row"><span class="form-label">Username</span><span>${escapeHtml(user.username)}</span></div>
      <div class="detail-row"><span class="form-label">Trainer ID</span><span>${escapeHtml(user.trainer_id || 'Not assigned yet')}</span></div>
      ${user.designation ? `<div class="detail-row"><span class="form-label">Designation</span><span>${escapeHtml(user.designation)}</span></div>` : ''}
      ${user.department ? `<div class="detail-row"><span class="form-label">Department</span><span>${escapeHtml(user.department)}</span></div>` : ''}
      <div class="detail-row"><span class="form-label">Account</span><span class="admin-activity-badge ${user.is_active ? 'is-success' : 'is-danger'}">${user.is_active ? 'Active' : 'Inactive'}</span></div>
      <div class="detail-row"><span class="form-label"></span><a class="btn btn-primary-outline" href="change-password.html" style="margin-top:6px;">Change Password</a></div>
    `;
  };
  renderAccountDetails();

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
