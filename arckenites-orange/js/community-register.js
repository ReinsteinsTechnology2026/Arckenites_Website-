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

  /* ---------- Step 1: details -> verify email ---------- */
  const detailsForm = document.getElementById('detailsForm');
  const detailsError = document.getElementById('detailsError');
  const detailsSubmitBtn = document.getElementById('detailsSubmitBtn');

  const DETAILS_SUBMIT_LABEL = detailsSubmitBtn.textContent;

  detailsForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    detailsError.style.display = 'none';
    detailsSubmitBtn.disabled = true;
    detailsSubmitBtn.textContent = 'Sending verification code...';

    const fullName = document.getElementById('regFullName').value.trim();
    const mobile = document.getElementById('regMobile').value.trim();
    const email = document.getElementById('regEmail').value.trim().toLowerCase();

    try {
      await ArckAPI.request('/community/register/start', {
        method: 'POST', auth: false,
        body: { full_name: fullName, mobile_number: mobile, email },
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
      detailsError.textContent = err.detail || 'Could not start registration. Please check your details and try again.';
      detailsError.style.display = 'block';
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
      passwordError.textContent = err.detail || 'Could not set your password. Please verify your email again.';
      passwordError.style.display = 'block';
    } finally {
      passwordSubmitBtn.disabled = false;
    }
  });

});
