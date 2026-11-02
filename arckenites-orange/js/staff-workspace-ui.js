/* Trainer batch workspace — tab strip behaviour only (no data, no API calls).
   Keeps the active tab in view and flags when the strip can scroll further so
   the CSS can show edge hints. Works with touch/trackpad scrolling and keyboard focus. */
document.addEventListener('DOMContentLoaded', () => {
  const strip = document.querySelector('.workspace-tabs');
  if (!strip) return;

  const updateHints = () => {
    const max = strip.scrollWidth - strip.clientWidth;
    strip.classList.toggle('has-more-start', strip.scrollLeft > 2);
    strip.classList.toggle('has-more-end', max > 2 && strip.scrollLeft < max - 2);
  };

  const reveal = (tab) => {
    if (!tab || strip.scrollWidth <= strip.clientWidth) return;
    const target = tab.offsetLeft - (strip.clientWidth - tab.offsetWidth) / 2;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    strip.scrollTo({ left: Math.max(0, target), behavior: reduced ? 'auto' : 'smooth' });
  };

  strip.addEventListener('scroll', updateHints, { passive: true });
  window.addEventListener('resize', () => { updateHints(); reveal(strip.querySelector('.workspace-tab.is-active')); });
  strip.addEventListener('click', (e) => reveal(e.target.closest('.workspace-tab')));
  strip.addEventListener('focusin', (e) => reveal(e.target.closest('.workspace-tab')));

  updateHints();
  reveal(strip.querySelector('.workspace-tab.is-active'));
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(updateHints);
});
