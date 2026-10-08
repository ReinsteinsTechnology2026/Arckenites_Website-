/* Arckenites Inauguration — Phase 1.
   Fully standalone: no dependency on the portal's js/api.js or js/auth.js,
   no login, no OTP, no password. The only external call is one POST to the
   configured Google Sheet endpoint. */
document.addEventListener('DOMContentLoaded', () => {

  document.getElementById('iaYear').textContent = new Date().getFullYear();

  /* ---------- Theme switch ----------
     Starts OFF on every fresh load (the body's data-theme="default" markup
     attribute is the source of truth, not JS and not localStorage). Clicking
     the switch is the only thing that changes it, and it never persists
     across a reload. */
  const themeToggle = document.getElementById('iaThemeToggle');
  const themeState = document.getElementById('iaThemeState');
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const turningOn = document.body.getAttribute('data-theme') !== 'inauguration';
      document.body.setAttribute('data-theme', turningOn ? 'inauguration' : 'default');
      themeToggle.setAttribute('aria-checked', String(turningOn));
      themeToggle.classList.toggle('is-on', turningOn);
      if (themeState) themeState.textContent = turningOn ? 'ON' : 'OFF';
    });
  }

  /* ---------- Hanging lamps: touch the chain, the whole rig swings ----------
     pointerdown covers a desktop click and a mobile tap in a single handler,
     so touch devices never double-trigger from separate click/touchstart
     listeners. Each lamp's chain-hit button is wired to its OWN lamp's rig
     (found via the shared .ia-lamp parent), so only the touched lamp's
     chain + lantern swing — every lamp manages its own state independently.
     A deliberate click/tap triggers one swing; hovering only gives a subtle
     cursor affordance (handled by CSS), never a continuous/automatic swing. */
  const swingLamp = (rig) => {
    rig.classList.remove('is-swinging');
    void rig.offsetWidth; // force reflow so the animation restarts even if re-triggered mid-swing
    rig.classList.add('is-swinging');
  };
  document.querySelectorAll('.ia-lamp').forEach((lamp) => {
    const rig = lamp.querySelector('.ia-lamp-rig');
    const chainHit = lamp.querySelector('.ia-lamp-chain-hit');
    if (!rig || !chainHit) return;
    chainHit.addEventListener('pointerdown', () => swingLamp(rig));
    rig.addEventListener('animationend', (e) => {
      if (e.animationName === 'ia-lamp-swing' || e.animationName === 'ia-lamp-swing-reduced') {
        rig.classList.remove('is-swinging');
      }
    });
  });

  /* ---------- Reveal-on-scroll (progressive enhancement) ---------- */
  const revealTargets = document.querySelectorAll('[data-reveal]');
  if ('IntersectionObserver' in window && revealTargets.length) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15 });
    revealTargets.forEach((el) => observer.observe(el));
  } else {
    revealTargets.forEach((el) => el.classList.add('is-visible'));
  }

  /* ---------- Registration endpoint ----------
     A ?endpoint=... query parameter overrides the page's configured Apps
     Script URL. This exists only so the flow can be tested locally against
     a stand-in server without editing the page; production visitors never
     pass this parameter, so they always hit the configured endpoint. */
  const endpointOverride = new URLSearchParams(window.location.search).get('endpoint');
  const SHEET_ENDPOINT = endpointOverride || window.INAUGURATION_SHEET_ENDPOINT || '';

  /* ---------- Modal open/close ---------- */
  const backdrop = document.getElementById('iaModalBackdrop');
  const closeBtn = document.getElementById('iaModalClose');
  const formView = document.getElementById('iaFormView');
  const successView = document.getElementById('iaSuccessView');
  const form = document.getElementById('iaForm');
  const submitBtn = document.getElementById('iaSubmitBtn');
  const banner = document.getElementById('iaFormBanner');

  const nameInput = document.getElementById('iaName');
  const mobileInput = document.getElementById('iaMobile');
  const emailInput = document.getElementById('iaEmail');
  const nameError = document.getElementById('iaNameError');
  const mobileError = document.getElementById('iaMobileError');
  const emailError = document.getElementById('iaEmailError');

  let submitting = false;

  const resetForm = () => {
    form.reset();
    [nameInput, mobileInput, emailInput].forEach((el) => el.classList.remove('is-invalid'));
    [nameError, mobileError, emailError].forEach((el) => { el.textContent = ''; });
    banner.style.display = 'none';
    banner.textContent = '';
    formView.style.display = 'block';
    successView.style.display = 'none';
  };

  const openModal = () => {
    resetForm();
    backdrop.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    setTimeout(() => nameInput.focus(), 50);
  };

  const closeModal = () => {
    if (submitting) return; // don't let a mid-flight submission be abandoned silently
    backdrop.classList.remove('is-open');
    document.body.style.overflow = '';
  };

  document.getElementById('openRegister').addEventListener('click', openModal);
  document.getElementById('openRegisterBottom').addEventListener('click', openModal);
  closeBtn.addEventListener('click', closeModal);
  document.getElementById('iaSuccessClose').addEventListener('click', closeModal);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

  /* ---------- Validation ----------
     Client-side only: this page has no backend of its own in Phase 1. */
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const MOBILE_ALLOWED_RE = /^\+?[0-9 ()\-.]+$/;

  const validateName = () => {
    const value = nameInput.value.trim();
    if (!value) return 'Name is required.';
    if (value.length < 2) return 'Enter your full name.';
    return '';
  };
  const validateMobile = () => {
    const value = mobileInput.value.trim();
    if (!value) return 'Mobile number is required.';
    if (!MOBILE_ALLOWED_RE.test(value)) return 'Enter a valid mobile number.';
    const digits = value.replace(/\D/g, '');
    if (digits.length < 7 || digits.length > 15) return 'Enter a valid mobile number.';
    return '';
  };
  const validateEmail = () => {
    const value = emailInput.value.trim();
    if (!value) return 'Email is required.';
    if (!EMAIL_RE.test(value)) return 'Enter a valid email address.';
    return '';
  };

  const showFieldError = (input, errorEl, message) => {
    errorEl.textContent = message;
    input.classList.toggle('is-invalid', !!message);
  };

  [[nameInput, nameError, validateName], [mobileInput, mobileError, validateMobile], [emailInput, emailError, validateEmail]]
    .forEach(([input, errorEl, validator]) => {
      input.addEventListener('blur', () => showFieldError(input, errorEl, validator()));
      input.addEventListener('input', () => { if (input.classList.contains('is-invalid')) showFieldError(input, errorEl, validator()); });
    });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (submitting) return;

    const nameMsg = validateName();
    const mobileMsg = validateMobile();
    const emailMsg = validateEmail();
    showFieldError(nameInput, nameError, nameMsg);
    showFieldError(mobileInput, mobileError, mobileMsg);
    showFieldError(emailInput, emailError, emailMsg);
    if (nameMsg || mobileMsg || emailMsg) {
      (nameMsg ? nameInput : mobileMsg ? mobileInput : emailInput).focus();
      return;
    }

    if (!SHEET_ENDPOINT || SHEET_ENDPOINT === 'REPLACE_WITH_YOUR_APPS_SCRIPT_URL') {
      banner.textContent = 'Registration is not configured yet. Please try again shortly.';
      banner.style.display = 'block';
      return;
    }

    submitting = true;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting...';
    banner.style.display = 'none';

    try {
      // Sent as text/plain (not application/json) so the request is a "simple"
      // cross-origin request with no CORS preflight — the common, documented
      // way to post to a Google Apps Script Web App from a browser.
      const response = await fetch(SHEET_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: JSON.stringify({
          name: nameInput.value.trim(),
          mobile: mobileInput.value.trim(),
          email: emailInput.value.trim(),
          source: 'inauguration-phase1',
        }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      formView.style.display = 'none';
      successView.style.display = 'block';
    } catch (err) {
      // Never show the success message unless the submission actually went
      // through — a network or server failure must look like a failure.
      banner.textContent = "We couldn't submit your registration. Please check your connection and try again.";
      banner.style.display = 'block';
    } finally {
      submitting = false;
      submitBtn.disabled = false;
      submitBtn.textContent = 'Register';
    }
  });

});
