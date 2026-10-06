document.addEventListener('DOMContentLoaded', () => {

  const steps = {
    details: document.querySelector('.form-card[data-step="details"]'),
    otp: document.querySelector('.form-card[data-step="otp"]'),
    password: document.querySelector('.form-card[data-step="password"]'),
    done: document.querySelector('.form-card[data-step="done"]'),
  };
  if (!steps.details) return; // not on this page

  const showStep = (name) => {
    Object.values(steps).forEach((el) => { el.style.display = 'none'; });
    steps[name].style.display = 'block';
  };

  // Cosmetic only, matching the display the backend itself uses
  // (core/otp.py mask_email) — never used for anything security-relevant,
  // the real submission always uses the exact address the user typed.
  const maskEmail = (email) => {
    const [local, domain] = email.split('@');
    if (!domain) return email;
    const visible = local.slice(0, 2);
    return `${visible}${'*'.repeat(Math.max(local.length - visible.length, 4))}@${domain}`;
  };

  let currentEmail = '';
  let verificationToken = '';
  // The details most recently submitted, so "Start Again" can reuse them.
  let lastDetails = null;

  /* ---------- Duplicate-credential notices ----------
     The backend answers with HTTP 409 and a machine-readable `code`, a
     heading (`detail`) and supporting text (`hint`). The page only shows
     that wording — it never shows raw server errors, IDs, or database text. */
  const DUPLICATE_CODES = ['email_registered', 'mobile_registered', 'credentials_registered', 'registration_in_progress'];
  const dupNotice = document.getElementById('dupNotice');
  const dupHeading = document.getElementById('dupHeading');
  const dupHint = document.getElementById('dupHint');
  const dupActions = document.getElementById('dupActions');
  const regEmail = document.getElementById('regEmail');
  const regMobile = document.getElementById('regMobile');

  const hideDuplicate = () => { dupNotice.style.display = 'none'; dupActions.innerHTML = ''; };

  const addAction = (label, className, onClick) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = className;
    btn.textContent = label;
    btn.addEventListener('click', onClick);
    dupActions.appendChild(btn);
    return btn;
  };

  const addLoginLink = () => {
    const a = document.createElement('a');
    a.href = 'login.html';
    a.className = 'btn btn-primary-outline';
    a.textContent = 'Login';
    dupActions.appendChild(a);
  };

  const showDuplicate = (err) => {
    detailsError.style.display = 'none';
    dupHeading.textContent = err.detail;
    dupHint.textContent = err.hint || '';
    dupActions.innerHTML = '';

    if (err.code === 'registration_in_progress') {
      addAction('Continue Registration', 'btn btn-accent', () => {
        hideDuplicate();
        currentEmail = lastDetails.email;
        document.getElementById('otpSentToEmail').textContent = maskEmail(currentEmail);
        document.getElementById('otpInput').value = '';
        showStep('otp');
      });
      addAction('Start Again', 'btn btn-primary-outline', () => startAgain(err));
      return;
    }

    addLoginLink();
    if (err.code === 'email_registered') {
      addAction('Use Another Email', 'btn btn-primary-outline', () => {
        hideDuplicate();
        regEmail.value = '';
        regEmail.focus();
      });
    } else if (err.code === 'mobile_registered') {
      addAction('Use Another Mobile', 'btn btn-primary-outline', () => {
        hideDuplicate();
        regMobile.value = '';
        regMobile.focus();
      });
    }
    // credentials_registered: Login only, as specified.
    dupNotice.style.display = 'block';
  };

  // "Start Again" on an in-progress registration: asks the backend for a new
  // code through the normal, rate-limited OTP path (restart=true).
  const startAgain = async () => {
    if (!lastDetails) return;
    try {
      await ArckAPI.request('/community/register/start', {
        method: 'POST', auth: false,
        body: { ...lastDetails, restart: true },
      });
      hideDuplicate();
      currentEmail = lastDetails.email;
      document.getElementById('otpSentToEmail').textContent = maskEmail(currentEmail);
      document.getElementById('otpInput').value = '';
      showStep('otp');
    } catch (err) {
      if (DUPLICATE_CODES.includes(err.code)) {
        showDuplicate(err);
      } else {
        detailsError.textContent = err.detail || 'Could not start a new registration. Please try again shortly.';
        detailsError.style.display = 'block';
      }
    }
  };

  /* ---------- Step 1: details -> verify email ---------- */
  const detailsForm = document.getElementById('detailsForm');
  const detailsError = document.getElementById('detailsError');
  const detailsSubmitBtn = document.getElementById('detailsSubmitBtn');

  const DETAILS_SUBMIT_LABEL = detailsSubmitBtn.textContent;

  detailsForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    detailsError.style.display = 'none';
    hideDuplicate();
    detailsSubmitBtn.disabled = true;
    detailsSubmitBtn.textContent = 'Sending verification code...';

    const fullName = document.getElementById('regFullName').value.trim();
    const mobile = regMobile.value.trim();
    const email = regEmail.value.trim().toLowerCase();
    lastDetails = { full_name: fullName, mobile_number: mobile, email };

    try {
      await ArckAPI.request('/community/register/start', {
        method: 'POST', auth: false,
        body: lastDetails,
      });
      // Only ever reached when the backend has confirmed the email actually
      // left the server — a failed send raises here instead (503, caught
      // below), so this step never advances on a message that was never
      // delivered.
      currentEmail = email;
      document.getElementById('otpSentToEmail').textContent = maskEmail(email);
      document.getElementById('otpInput').value = '';
      showStep('otp');
    } catch (err) {
      if (DUPLICATE_CODES.includes(err.code)) {
        showDuplicate(err);
      } else {
        detailsError.textContent = err.detail || 'Could not start registration. Please check your details and try again.';
        detailsError.style.display = 'block';
      }
    } finally {
      detailsSubmitBtn.disabled = false;
      detailsSubmitBtn.textContent = DETAILS_SUBMIT_LABEL;
    }
  });

  /* ---------- Step 2: verify OTP ---------- */
  const otpForm = document.getElementById('otpForm');
  const otpError = document.getElementById('otpError');
  const otpSubmitBtn = document.getElementById('otpSubmitBtn');
  const resendOtpBtn = document.getElementById('resendOtpBtn');

  otpForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    otpError.style.display = 'none';
    otpSubmitBtn.disabled = true;

    const otp = document.getElementById('otpInput').value.trim();

    try {
      const result = await ArckAPI.request('/community/register/verify-otp', {
        method: 'POST', auth: false,
        body: { email: currentEmail, otp },
      });
      verificationToken = result.verification_token;
      showStep('password');
    } catch (err) {
      otpError.textContent = err.detail || 'Invalid or expired verification code.';
      otpError.style.display = 'block';
    } finally {
      otpSubmitBtn.disabled = false;
    }
  });

  resendOtpBtn.addEventListener('click', async () => {
    resendOtpBtn.disabled = true;
    const resendLabel = resendOtpBtn.textContent;
    resendOtpBtn.textContent = 'Sending...';
    otpError.style.display = 'none';
    try {
      await ArckAPI.request('/community/register/resend-otp', {
        method: 'POST', auth: false,
        body: { email: currentEmail },
      });
      // Reached only once the backend confirms a real send (or the request
      // was silently rate-limited) — either way the previous code is now
      // invalid, so the user must use whatever arrives from this call.
      otpError.style.color = 'var(--accent)';
      otpError.textContent = 'If eligible, a new code has been sent to your email.';
      otpError.style.display = 'block';
    } catch (err) {
      otpError.style.color = '';
      otpError.textContent = err.detail || 'Could not resend the code right now. Please try again shortly.';
      otpError.style.display = 'block';
    } finally {
      resendOtpBtn.textContent = resendLabel;
      setTimeout(() => { resendOtpBtn.disabled = false; }, 15000);
    }
  });

  /* ---------- Step 3: set password ---------- */
  const passwordForm = document.getElementById('passwordForm');
  const passwordError = document.getElementById('passwordError');
  const passwordSubmitBtn = document.getElementById('passwordSubmitBtn');

  passwordForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    passwordError.style.display = 'none';

    const newPassword = document.getElementById('regPassword').value;
    const confirmPassword = document.getElementById('regPasswordConfirm').value;

    if (newPassword !== confirmPassword) {
      passwordError.textContent = 'Passwords do not match.';
      passwordError.style.display = 'block';
      return;
    }

    passwordSubmitBtn.disabled = true;
    try {
      await ArckAPI.request('/community/register/set-password', {
        method: 'POST', auth: false,
        body: { verification_token: verificationToken, new_password: newPassword, confirm_password: confirmPassword },
      });
      showStep('done');
    } catch (err) {
      // Someone else completed an account with this email or mobile while
      // this registration was in progress. Say so plainly, with the hint.
      const message = err.hint ? `${err.detail} ${err.hint}` : err.detail;
      passwordError.textContent = message || 'Could not set your password. Please verify your email again.';
      passwordError.style.display = 'block';
    } finally {
      passwordSubmitBtn.disabled = false;
    }
  });

});
