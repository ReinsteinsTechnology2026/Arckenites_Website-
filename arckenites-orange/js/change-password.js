document.addEventListener('DOMContentLoaded', async () => {

  // Any authenticated user (regardless of role) can be here — this page
  // doesn't gate by role, only by "are you logged in at all".
  try {
    await ArckAuth.getCurrentUser();
  } catch (_) {
    return; // onUnauthorized already redirected to login.html
  }

  const form = document.getElementById('changePasswordForm');
  const currentInput = document.getElementById('currentPassword');
  const newInput = document.getElementById('newPassword');
  const confirmInput = document.getElementById('confirmPassword');
  const errorBox = document.getElementById('changePasswordError');
  const submitBtn = form.querySelector('button[type="submit"]');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';

    if (newInput.value !== confirmInput.value) {
      errorBox.textContent = 'New password and confirmation do not match.';
      errorBox.style.display = 'block';
      return;
    }

    submitBtn.disabled = true;
    try {
      await ArckAPI.request('/auth/change-password', {
        method: 'POST',
        body: { current_password: currentInput.value, new_password: newInput.value },
      });
      const updatedUser = await ArckAuth.getCurrentUser();
      ArckAuth.redirectToRoleEntryPoint(updatedUser);
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not update password.';
      errorBox.style.display = 'block';
    } finally {
      submitBtn.disabled = false;
    }
  });

});
