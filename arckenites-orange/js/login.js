document.addEventListener('DOMContentLoaded', () => {

  const form = document.getElementById('loginForm');
  if (!form) return;

  const usernameInput = document.getElementById('loginUsername');
  const passwordInput = document.getElementById('loginPassword');
  const errorBox = document.getElementById('loginError');
  const submitBtn = form.querySelector('button[type="submit"]');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';
    submitBtn.disabled = true;

    try {
      const user = await ArckAuth.login(usernameInput.value.trim(), passwordInput.value);
      ArckAuth.redirectToRoleEntryPoint(user);
    } catch (err) {
      errorBox.textContent = err.detail || 'Invalid User ID or password.';
      errorBox.style.display = 'block';
      passwordInput.value = '';
    } finally {
      submitBtn.disabled = false;
    }
  });

});
