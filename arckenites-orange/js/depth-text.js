(function () {
  const MAX_LAYERS = 64;

  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

  const getLayerColor = (faceColor, depthColor, index, total) => {
    const progress = total <= 1 ? 1 : index / total;
    const eased = progress * progress;
    const faceMix = Math.round((1 - eased) * 72 + 4);
    return `color-mix(in srgb, ${faceColor} ${faceMix}%, ${depthColor})`;
  };

  const getTransform = (rotateX, rotateY) =>
    `rotateX(${rotateX.toFixed(3)}deg) rotateY(${rotateY.toFixed(3)}deg)`;

  function initDepthText(root) {
    const text = root.dataset.text || 'Arckenites';
    const layers = clamp(Math.round(Number(root.dataset.layers) || 34), 2, MAX_LAYERS);
    const depth = clamp(Number(root.dataset.depth) || 2.4, 0, 12);
    const faceColor = root.dataset.faceColor || '#f8fafc';
    const depthColor = root.dataset.depthColor || '#7c3aed';
    const tilt = clamp(Number(root.dataset.tilt) || 7.5, 0, 12);
    const pointerTracking = root.dataset.pointerTracking !== 'false';
    const smoothing = clamp(Number(root.dataset.smoothing) || 0.14, 0.02, 0.35);
    const perspective = clamp(Number(root.dataset.perspective) || 900, 300, 2000);
    const autoOrbit = root.dataset.autoOrbit !== 'false';
    const orbitSpeed = clamp(Number(root.dataset.orbitSpeed) || 0.35, 0, 2);
    const fontSize = root.dataset.fontSize || 'clamp(2.6rem, 7vw, 5rem)';
    const fontWeight = root.dataset.fontWeight || '900';
    const shadow = root.dataset.shadow !== 'false';

    const baseRotation = { x: -tilt * 0.32, y: tilt * 0.42 };

    root.style.setProperty('--depth-text-perspective', `${perspective}px`);
    root.style.setProperty('--depth-text-font-size', fontSize);
    root.style.setProperty('--depth-text-font-weight', fontWeight);
    root.style.setProperty('--depth-text-face-color', faceColor);
    root.style.setProperty('--depth-text-depth-color', depthColor);
    root.style.setProperty(
      '--depth-text-shadow',
      shadow
        ? `0 6px 10px color-mix(in srgb, ${depthColor} 24%, transparent), 0 2px 4px rgba(0, 0, 0, 0.35)`
        : 'none'
    );

    const stage = document.createElement('span');
    stage.className = 'depth-text__stage';

    for (let layerIndex = 0; layerIndex < layers; layerIndex += 1) {
      const index = layers - layerIndex;
      const layerEl = document.createElement('span');
      layerEl.className = 'depth-text__layer';
      layerEl.setAttribute('aria-hidden', 'true');
      layerEl.style.color = getLayerColor(faceColor, depthColor, index, layers);
      layerEl.style.transform = `translateZ(${-index * depth}px)`;
      layerEl.textContent = text;
      stage.appendChild(layerEl);
    }

    const faceEl = document.createElement('span');
    faceEl.className = 'depth-text__face';
    faceEl.textContent = text;
    stage.appendChild(faceEl);

    root.innerHTML = '';
    root.appendChild(stage);

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    const canTrackPointer = pointerTracking && finePointer && !reducedMotion;

    if (reducedMotion) {
      stage.style.transform = getTransform(baseRotation.x, baseRotation.y);
      return;
    }

    let frameId = 0;
    let activePointer = false;
    const startTime = performance.now();
    const current = { ...baseRotation };
    const target = { ...baseRotation };

    const applyTransform = () => {
      stage.style.transform = getTransform(current.x, current.y);
    };

    const handlePointerMove = (event) => {
      const rect = root.getBoundingClientRect();
      if (!rect.width || !rect.height) return;

      activePointer = true;
      const x = clamp((event.clientX - (rect.left + rect.width / 2)) / (rect.width * 0.8), -1, 1);
      const y = clamp((event.clientY - (rect.top + rect.height / 2)) / (rect.height * 0.8), -1, 1);

      target.x = baseRotation.x - y * tilt;
      target.y = baseRotation.y + x * tilt;
    };

    const handlePointerLeave = () => {
      activePointer = false;
      target.x = baseRotation.x;
      target.y = baseRotation.y;
    };

    if (canTrackPointer) {
      window.addEventListener('pointermove', handlePointerMove);
      window.addEventListener('pointerleave', handlePointerLeave);
      window.addEventListener('blur', handlePointerLeave);
    }

    const tick = (now) => {
      if ((!canTrackPointer || !activePointer) && autoOrbit) {
        const elapsed = (now - startTime) / 1000;
        const orbit = elapsed * orbitSpeed * Math.PI * 2;
        const fallbackAmount = canTrackPointer ? 0.18 : 0.55;
        target.x = baseRotation.x + Math.sin(orbit) * tilt * fallbackAmount;
        target.y = baseRotation.y + Math.cos(orbit * 0.85) * tilt * fallbackAmount;
      }

      current.x += (target.x - current.x) * smoothing;
      current.y += (target.y - current.y) * smoothing;
      applyTransform();
      frameId = requestAnimationFrame(tick);
    };

    applyTransform();
    frameId = requestAnimationFrame(tick);

    window.addEventListener('beforeunload', () => cancelAnimationFrame(frameId));
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-depth-text]').forEach(initDepthText);
  });
})();
