/* Arckenites Inauguration — Phase 1.
   Fully standalone: no dependency on the portal's js/api.js or js/auth.js,
   no login, no OTP, no password. The only external call is one POST to the
   configured Google Sheet endpoint. */
document.addEventListener('DOMContentLoaded', () => {

  document.getElementById('iaYear').textContent = new Date().getFullYear();

  /* ---------- Voice: a short synthesized chime + browser speech synthesis ----------
     No prerecorded professional voice-over asset exists in this project,
     so this uses the browser's built-in SpeechSynthesis API rather than
     faking a silent toggle. Voice quality/pitch varies by browser and OS —
     this picks the best available match for "a clear, warm voice" via
     name-based heuristics (there is no reliable cross-browser gender flag
     on SpeechSynthesisVoice) and falls back to the first available voice
     rather than assuming a specific one exists on every device. The
     "sparkle" effect is a few bright, quickly-decaying sine tones via the
     Web Audio API — not an external sound file — kept short and quiet
     enough to never obscure the spoken words. Everything here only ever
     runs in response to a real click (the switch, or the registration
     submit), so it respects autoplay restrictions: nothing plays on page
     load. */
  let audioCtx = null;
  const getAudioCtx = () => {
    if (!audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      audioCtx = new Ctx();
    }
    if (audioCtx.state === 'suspended') audioCtx.resume();
    return audioCtx;
  };
  const playSparkle = () => {
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      const now = ctx.currentTime;
      [1760, 2217, 2637, 3136].forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = freq;
        const start = now + i * 0.06;
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(0.12, start + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.35);
        osc.connect(gain).connect(ctx.destination);
        osc.start(start);
        osc.stop(start + 0.4);
      });
    } catch (_) { /* a synthesized chime failing is never worth surfacing to the user */ }
  };

  let pickedVoice = null;
  const pickFemaleVoice = () => {
    if (!('speechSynthesis' in window)) return null;
    const voices = window.speechSynthesis.getVoices();
    if (!voices.length) return null;
    const femaleHints = /female|zira|samantha|susan|victoria|karen|tessa|moira|fiona|serena|aria|jenny|amy|emma|salli|joanna/i;
    return voices.find((v) => femaleHints.test(v.name) && /^en/i.test(v.lang))
      || voices.find((v) => femaleHints.test(v.name))
      || voices.find((v) => /^en/i.test(v.lang))
      || voices[0];
  };
  if ('speechSynthesis' in window) {
    pickedVoice = pickFemaleVoice(); // some browsers already have voices ready
    window.speechSynthesis.onvoiceschanged = () => { pickedVoice = pickFemaleVoice(); };
  }

  /* Always cancels any in-flight utterance first, so rapid repeated clicks
     (the switch, or a replay button) can never overlap two messages. */
  const speak = (text) => {
    if (!('speechSynthesis' in window)) return false;
    try {
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.rate = 0.95;
      utter.pitch = 1.1;
      utter.volume = 1;
      if (pickedVoice) utter.voice = pickedVoice;
      window.speechSynthesis.speak(utter);
      return true;
    } catch (_) {
      return false;
    }
  };
  const stopSpeaking = () => {
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  };

  /* ---------- Theme switch ----------
     Starts OFF ("Click to turn on") on every fresh load (the body's
     data-theme="default" markup attribute is the source of truth, not JS
     and not localStorage). Clicking the switch is the only thing that
     changes it, and it never persists across a reload. Turning it ON also
     plays the welcome voice message; turning it OFF stops/resets any
     playback cleanly rather than letting it run on into a hidden page. */
  const themeToggle = document.getElementById('iaThemeToggle');
  const themeState = document.getElementById('iaThemeState');
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const turningOn = document.body.getAttribute('data-theme') !== 'inauguration';
      document.body.setAttribute('data-theme', turningOn ? 'inauguration' : 'default');
      themeToggle.setAttribute('aria-checked', String(turningOn));
      themeToggle.classList.toggle('is-on', turningOn);
      if (themeState) themeState.textContent = turningOn ? 'ON' : 'Click to turn on';

      if (turningOn) {
        playSparkle();
        speak('Welcome to Arckenites Inauguration!');
      } else {
        stopSpeaking();
      }
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
  const addressInput = document.getElementById('iaAddress');
  const domainSelect = document.getElementById('iaDomain');
  const domainOtherWrap = document.getElementById('iaDomainOtherWrap');
  const domainOtherInput = document.getElementById('iaDomainOther');
  const whatsappOtherWrap = document.getElementById('iaWhatsappOtherWrap');
  const whatsappOtherInput = document.getElementById('iaWhatsappOther');
  const commentsInput = document.getElementById('iaComments');

  const nameError = document.getElementById('iaNameError');
  const mobileError = document.getElementById('iaMobileError');
  const whatsappError = document.getElementById('iaWhatsappError');
  const emailError = document.getElementById('iaEmailError');
  const addressError = document.getElementById('iaAddressError');
  const domainError = document.getElementById('iaDomainError');
  const travelError = document.getElementById('iaTravelError');
  const foodError = document.getElementById('iaFoodError');
  const tshirtError = document.getElementById('iaTshirtError');

  const travelGroup = document.getElementById('iaTravel');
  const foodGroup = document.getElementById('iaFood');
  const tshirtGroup = document.getElementById('iaTshirt');

  let submitting = false;

  /* ---------- Registration thank-you voice ----------
     Plays once per actual successful submission, never on validation/API
     failure, and never twice for the same success (guarded by
     thankYouPlayed, reset only when a fresh registration flow starts —
     resetForm() runs every time the modal opens, so a later, separate
     registration in the same page load can still play it again, but
     re-showing the already-displayed success view never re-triggers it).
     A manual replay button (#iaReplayThankYou) always works regardless of
     this guard, as the accessible fallback for blocked/failed autoplay. */
  let thankYouPlayed = false;
  const playThankYouMessage = () => {
    if (thankYouPlayed) return;
    thankYouPlayed = true;
    playSparkle();
    speak("Thanks for your time, we'll wait for your presence.");
  };
  const replayThankYouBtn = document.getElementById('iaReplayThankYou');
  if (replayThankYouBtn) {
    replayThankYouBtn.addEventListener('click', () => {
      playSparkle();
      speak("Thanks for your time, we'll wait for your presence.");
    });
  }

  /* ---------- Conditional fields ----------
     WhatsApp "Other" reveals its own number input; Domain "Other" reveals
     a free-text input. Both are hidden (and their value ignored) otherwise. */
  document.querySelectorAll('input[name="iaWhatsappType"]').forEach((radio) => {
    radio.addEventListener('change', () => {
      const showOther = document.querySelector('input[name="iaWhatsappType"]:checked').value === 'other';
      whatsappOtherWrap.hidden = !showOther;
      if (!showOther) { whatsappOtherInput.value = ''; whatsappError.textContent = ''; whatsappOtherInput.classList.remove('is-invalid'); }
    });
  });
  domainSelect.addEventListener('change', () => {
    const showOther = domainSelect.value === 'other';
    domainOtherWrap.hidden = !showOther;
    if (!showOther) { domainOtherInput.value = ''; }
  });

  const resetForm = () => {
    form.reset();
    whatsappOtherWrap.hidden = true;
    domainOtherWrap.hidden = true;
    [nameInput, mobileInput, whatsappOtherInput, emailInput, addressInput, domainSelect, domainOtherInput]
      .forEach((el) => el.classList.remove('is-invalid'));
    [travelGroup, foodGroup, tshirtGroup].forEach((el) => el.classList.remove('is-invalid'));
    [nameError, mobileError, whatsappError, emailError, addressError, domainError, travelError, foodError, tshirtError]
      .forEach((el) => { el.textContent = ''; });
    banner.style.display = 'none';
    banner.textContent = '';
    formView.style.display = 'block';
    successView.style.display = 'none';
    thankYouPlayed = false; // a fresh registration attempt may play the thank-you message again
  };

  const openModal = () => {
    resetForm();
    backdrop.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    setTimeout(() => nameInput.focus(), 50);
  };

  const closeModal = () => {
    if (submitting) return; // don't let a mid-flight submission be abandoned silently
    stopSpeaking(); // don't let the thank-you/welcome voice keep playing after the modal is dismissed
    backdrop.classList.remove('is-open');
    document.body.style.overflow = '';
  };

  document.getElementById('openRegister').addEventListener('click', openModal);
  closeBtn.addEventListener('click', closeModal);
  document.getElementById('iaSuccessClose').addEventListener('click', closeModal);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

  /* ---------- Validation ----------
     Client-side only: this page has no backend of its own in Phase 1.
     Mobile and WhatsApp are validated as EXACTLY 10 digits, per spec. */
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const TEN_DIGIT_RE = /^[0-9]{10}$/;

  const validateName = () => {
    const value = nameInput.value.trim();
    if (!value) return 'Full name is required.';
    if (value.length < 2) return 'Enter your full name.';
    return '';
  };
  const validateMobile = () => {
    const value = mobileInput.value.trim();
    if (!value) return 'Mobile number is required.';
    if (!TEN_DIGIT_RE.test(value)) return 'Enter exactly 10 digits.';
    return '';
  };
  const validateWhatsapp = () => {
    const type = document.querySelector('input[name="iaWhatsappType"]:checked').value;
    if (type === 'same') return '';
    const value = whatsappOtherInput.value.trim();
    if (!value) return 'WhatsApp number is required.';
    if (!TEN_DIGIT_RE.test(value)) return 'Enter exactly 10 digits.';
    return '';
  };
  const validateEmail = () => {
    const value = emailInput.value.trim();
    if (!value) return 'Email is required.';
    if (!EMAIL_RE.test(value)) return 'Enter a valid email address.';
    return '';
  };
  const validateAddress = () => {
    if (!addressInput.value.trim()) return 'Address is required.';
    return '';
  };
  const validateDomain = () => {
    if (!domainSelect.value) return 'Please select your domain.';
    if (domainSelect.value === 'other' && !domainOtherInput.value.trim()) return 'Please specify your domain.';
    return '';
  };
  const validateRadioGroup = (name) => {
    if (!document.querySelector(`input[name="${name}"]:checked`)) return 'Please choose one option.';
    return '';
  };

  const showFieldError = (input, errorEl, message) => {
    errorEl.textContent = message;
    input.classList.toggle('is-invalid', !!message);
  };
  const showGroupError = (groupEl, errorEl, message) => {
    errorEl.textContent = message;
    groupEl.classList.toggle('is-invalid', !!message);
  };

  [[nameInput, nameError, validateName], [mobileInput, mobileError, validateMobile],
   [emailInput, emailError, validateEmail], [addressInput, addressError, validateAddress]]
    .forEach(([input, errorEl, validator]) => {
      input.addEventListener('blur', () => showFieldError(input, errorEl, validator()));
      input.addEventListener('input', () => { if (input.classList.contains('is-invalid')) showFieldError(input, errorEl, validator()); });
    });
  whatsappOtherInput.addEventListener('blur', () => showFieldError(whatsappOtherInput, whatsappError, validateWhatsapp()));
  whatsappOtherInput.addEventListener('input', () => { if (whatsappOtherInput.classList.contains('is-invalid')) showFieldError(whatsappOtherInput, whatsappError, validateWhatsapp()); });
  domainSelect.addEventListener('blur', () => showFieldError(domainSelect, domainError, validateDomain()));
  domainOtherInput.addEventListener('input', () => { if (domainSelect.classList.contains('is-invalid')) showFieldError(domainSelect, domainError, validateDomain()); });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (submitting) return;

    const nameMsg = validateName();
    const mobileMsg = validateMobile();
    const whatsappMsg = validateWhatsapp();
    const emailMsg = validateEmail();
    const addressMsg = validateAddress();
    const domainMsg = validateDomain();
    const travelMsg = validateRadioGroup('iaTravel');
    const foodMsg = validateRadioGroup('iaFood');
    const tshirtMsg = validateRadioGroup('iaTshirt');

    showFieldError(nameInput, nameError, nameMsg);
    showFieldError(mobileInput, mobileError, mobileMsg);
    showFieldError(whatsappOtherInput, whatsappError, whatsappMsg);
    showFieldError(emailInput, emailError, emailMsg);
    showFieldError(addressInput, addressError, addressMsg);
    showFieldError(domainSelect, domainError, domainMsg);
    showGroupError(travelGroup, travelError, travelMsg);
    showGroupError(foodGroup, foodError, foodMsg);
    showGroupError(tshirtGroup, tshirtError, tshirtMsg);

    const firstInvalid = nameMsg ? nameInput : mobileMsg ? mobileInput : whatsappMsg ? whatsappOtherInput
      : emailMsg ? emailInput : addressMsg ? addressInput : domainMsg ? domainSelect : null;
    if (firstInvalid) { firstInvalid.focus(); return; }
    if (travelMsg || foodMsg || tshirtMsg) return; // pill groups aren't focusable as a single element

    if (!SHEET_ENDPOINT || SHEET_ENDPOINT === 'REPLACE_WITH_YOUR_APPS_SCRIPT_URL') {
      banner.textContent = 'Registration is not configured yet. Please try again shortly.';
      banner.style.display = 'block';
      return;
    }

    submitting = true;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting...';
    banner.style.display = 'none';

    const whatsappType = document.querySelector('input[name="iaWhatsappType"]:checked').value;
    const whatsappNumber = whatsappType === 'same' ? mobileInput.value.trim() : whatsappOtherInput.value.trim();
    const domainValue = domainSelect.value === 'other' ? domainOtherInput.value.trim() : domainSelect.options[domainSelect.selectedIndex].text;

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
          whatsapp: whatsappNumber,
          email: emailInput.value.trim(),
          address: addressInput.value.trim(),
          domain: domainValue,
          travel: document.querySelector('input[name="iaTravel"]:checked').value,
          food: document.querySelector('input[name="iaFood"]:checked').value,
          tshirt: document.querySelector('input[name="iaTshirt"]:checked').value,
          comments: commentsInput.value.trim(),
          source: 'inauguration-phase1',
        }),
      });
      // Apps Script always responds HTTP 200, even for a rejected
      // registration — the real result is in the JSON body's "status",
      // which must be inspected before ever showing success.
      let result = null;
      try { result = await response.json(); } catch (_) { /* leave result null, handled below */ }

      if (!response.ok || !result) {
        throw new Error('submission failed');
      }
      if (result.status === 'duplicate') {
        banner.textContent = result.message || 'This email has already been registered for this event.';
        banner.style.display = 'block';
      } else if (result.status === 'ok') {
        formView.style.display = 'none';
        successView.style.display = 'block';
        playThankYouMessage(); // only after a real confirmed success — never on a validation/API error
      } else {
        throw new Error(result.message || 'submission failed');
      }
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
