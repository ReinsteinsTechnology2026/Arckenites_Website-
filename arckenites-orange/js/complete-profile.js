document.addEventListener('DOMContentLoaded', async () => {

  let user;
  try {
    user = await ArckAuth.getCurrentUser();
  } catch (_) {
    return; // onUnauthorized already redirected to login.html
  }

  if (user.role !== 'student') {
    ArckAPI.clearSession();
    window.location.href = 'login.html';
    return;
  }
  if (user.must_change_password) {
    window.location.href = 'change-password.html';
    return;
  }
  if (user.profile_completed) {
    window.location.href = 'student-dashboard.html';
    return;
  }

  const nameInput = document.getElementById('profileName');
  const mobileInput = document.getElementById('profileMobile');
  const emailInput = document.getElementById('profileEmail');
  const errorBox = document.getElementById('completeProfileError');
  const form = document.getElementById('completeProfileForm');
  const submitBtn = form.querySelector('button[type="submit"]');

  nameInput.value = user.full_name || '';

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';

    submitBtn.disabled = true;
    try {
      const updatedUser = await ArckAPI.request('/students/me/profile', {
        method: 'POST',
        body: {
          full_name: nameInput.value.trim(),
          mobile_number: mobileInput.value.trim(),
          email: emailInput.value.trim(),
        },
      });
      ArckAuth.redirectToRoleEntryPoint(updatedUser);
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not save your details. Please try again.';
      errorBox.style.display = 'block';
      submitBtn.disabled = false;
    }
  });

});
