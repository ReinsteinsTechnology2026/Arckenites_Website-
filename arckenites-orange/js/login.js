document.addEventListener('DOMContentLoaded', () => {

  const form = document.getElementById('loginForm');
  if (!form) return;

  const params = new URLSearchParams(window.location.search);
  const portal = params.get('portal');
  const chooser = document.getElementById('portalChooser');
  const loginPanel = document.getElementById('portalLogin');
  const validPortal = portal === 'student' || portal === 'staff';
  chooser.style.display = validPortal ? 'none' : '';
  loginPanel.style.display = validPortal ? '' : 'none';
  document.getElementById('loginSubtitle').textContent = validPortal
    ? (portal === 'student' ? 'Student Login' : 'Staff Login')
    : 'Choose the portal you want to access.';
  document.getElementById('portalBack').addEventListener('click', (event) => {
    event.preventDefault();
    window.location.href = 'login.html';
  });
  if (!validPortal && (params.has('google_ticket') || params.has('google_error'))) {
    window.location.replace('login.html');
    return;
  }

  const usernameInput = document.getElementById('loginUsername');
  const passwordInput = document.getElementById('loginPassword');
  const errorBox = document.getElementById('loginError');
  const submitBtn = form.querySelector('button[type="submit"]');
  const mfaPanel = document.getElementById('mfaLoginPanel');
  const mfaForm = document.getElementById('mfaLoginForm');
  const mfaCode = document.getElementById('mfaLoginCode');
  const mfaError = document.getElementById('mfaLoginError');
  let mfaChallenge = null;
  let enrollmentRequired = false;
  let pendingUser = null;

  const startMfa = async (data) => {
    mfaChallenge = data.challenge;
    enrollmentRequired = !!data.enrollment_required;
    form.style.display = 'none';
    mfaPanel.style.display = 'block';
    mfaError.style.display = 'none';
    document.getElementById('mfaLoginInstructions').textContent = enrollmentRequired
      ? 'MFA is required. Scan the QR code, then enter the six-digit code to complete enrollment.'
      : 'Enter the current code from your authenticator app, or a saved recovery code.';
    document.getElementById('mfaEnrollmentQr').style.display = 'none';
    if (enrollmentRequired) {
      try {
        const setup = await ArckAPI.request('/auth/mfa/enrollment/start', {
          method: 'POST', auth: false, body: {challenge: mfaChallenge},
        });
        document.getElementById('mfaEnrollmentImage').src = setup.qr_code;
        document.getElementById('mfaEnrollmentQr').style.display = 'block';
      } catch (err) {
        mfaError.textContent = ArckAPI.errorMessage(err, 'Could not start MFA setup. Sign in again.');
        mfaError.style.display = 'block';
      }
    }
    mfaCode.focus();
  };
  mfaForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = mfaForm.querySelector('button[type="submit"]');
    button.disabled = true;
    mfaError.style.display = 'none';
    try {
      const result = await ArckAPI.request(enrollmentRequired
        ? '/auth/mfa/enrollment/confirm' : '/auth/mfa/challenge/verify', {
        method: 'POST', auth: false, body: {challenge: mfaChallenge, code: mfaCode.value.replace(/\s+/g, '')},
      });
      mfaChallenge = null;
      mfaCode.value = '';
      ArckAPI.setSession(result.access_token, result.user);
      if (result.recovery_codes) {
        pendingUser = result.user;
        document.getElementById('mfaLoginRecoveryCodes').textContent = result.recovery_codes.join('\n');
        document.getElementById('mfaLoginRecovery').style.display = 'block';
        mfaForm.style.display = 'none';
        document.getElementById('mfaLoginContinue').focus();
      } else {
        ArckAuth.redirectToRoleEntryPoint(result.user);
      }
    } catch (err) {
      mfaError.textContent = ArckAPI.errorMessage(err, 'Verification failed. Sign in again if the challenge expired.');
      mfaError.style.display = 'block';
      mfaCode.value = '';
    } finally {
      button.disabled = false;
    }
  });
  document.getElementById('mfaLoginContinue').addEventListener('click', () => {
    if (pendingUser) ArckAuth.redirectToRoleEntryPoint(pendingUser);
  });
  document.getElementById('mfaLoginCancel').addEventListener('click', () => {
    mfaChallenge = null;
    enrollmentRequired = false;
    mfaCode.value = '';
    mfaPanel.style.display = 'none';
    form.style.display = '';
  });

  // Complete sign-ins that were already redirected back from Google.
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
          method: 'POST', auth: false, body: { ticket, portal },
        });
        if (data.mfa_required) await startMfa(data);
        else {
          ArckAPI.setSession(data.access_token, data.user);
          ArckAuth.redirectToRoleEntryPoint(data.user);
        }
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
      const result = await ArckAuth.login(usernameInput.value.trim(), passwordInput.value, portal);
      if (result.mfa_required) await startMfa(result);
      else ArckAuth.redirectToRoleEntryPoint(result);
    } catch (err) {
      errorBox.textContent = err.detail || 'Invalid User ID or password.';
      errorBox.style.display = 'block';
      passwordInput.value = '';
    } finally {
      submitBtn.disabled = false;
    }
  });

});
