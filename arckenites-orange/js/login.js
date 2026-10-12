document.addEventListener('DOMContentLoaded', () => {

  const form = document.getElementById('loginForm');
  if (!form) return;

  const usernameInput = document.getElementById('loginUsername');
  const passwordInput = document.getElementById('loginPassword');
  const errorBox = document.getElementById('loginError');
  const submitBtn = form.querySelector('button[type="submit"]');

  /* ---------- Continue with Google ----------
     The button starts the backend OAuth flow. The browser never sees the Google
     client secret or any Google token: the backend returns a one-time ticket in
     the URL, which is exchanged here for the same session the password login
     creates. The ticket is removed from the address bar straight away. */
  const googleBtn = document.getElementById('googleLoginBtn');
  if (googleBtn && typeof API_BASE !== 'undefined') {
    googleBtn.setAttribute('href', `${API_BASE}/auth/google/login`);
  }

  const GOOGLE_ERRORS = {
    cancelled: 'Google sign-in was cancelled.',
    no_account: 'No Arckenites Portal account is linked to this Google email. Google sign-in cannot create accounts. Please contact your administrator.',
    disabled: 'This account is disabled. Please contact your administrator.',
    locked: 'This account is temporarily locked after too many failed attempts. Please try again later.',
    maintenance: 'The system is currently under maintenance. Please try again later.',
    unverified: 'Your Google email address is not verified. Verify it with Google, then try again.',
    ambiguous: 'We could not sign you in with this Google account. Please contact your administrator.',
    identity: 'We could not sign you in with this Google account. Please contact your administrator.',
    unavailable: 'Google sign-in is not available right now. Please use your User ID and password.',
  };
  const GOOGLE_DEFAULT_ERROR = 'Google sign-in could not be completed. Please try again.';

  const showError = (message) => {
    errorBox.textContent = message;
    errorBox.style.display = 'block';
  };

  // The backend sends the outcome back in the query string. Read it once, then
  // clean the address bar so a ticket or error is not left in history.
  const params = new URLSearchParams(window.location.search);
  const ticket = params.get('google_ticket');
  const googleError = params.get('google_error');
  if (ticket || googleError) {
    window.history.replaceState(null, '', window.location.pathname);
  }

  if (googleError) {
    showError(GOOGLE_ERRORS[googleError] || GOOGLE_DEFAULT_ERROR);
  }

  if (ticket) {
    (async () => {
      submitBtn.disabled = true;
      try {
        const data = await ArckAPI.request('/auth/google/exchange', {
          method: 'POST', auth: false, body: { ticket },
        });
        ArckAPI.setSession(data.access_token, data.user);
        ArckAuth.redirectToRoleEntryPoint(data.user);
      } catch (err) {
        showError(err.detail || 'This sign-in link has expired. Please try again.');
        submitBtn.disabled = false;
      }
    })();
    return;
  }

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
