(() => {
  const scene = document.querySelector('.connection-scene');
  const message = document.querySelector('.message-text');
  const toggle = document.querySelector('.motion-toggle');
  if (!scene || !message || !toggle) return;

  const phases = [
    { name: 'voucher', text: 'Un voucher prende forma.' },
    { name: 'contatti', text: 'Il contatto diventa relazione.' },
    { name: 'sviluppo', text: 'La relazione apre opportunità.' }
  ];
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let phase = 0;
  let timer = null;
  let userPaused = false;
  let visible = true;
  let running = false;

  function cancelTimer() {
    window.clearTimeout(timer);
    timer = null;
  }

  function syncMotion() {
    cancelTimer();
    running = !reduceMotion.matches && !userPaused && !document.hidden && visible;
    scene.classList.toggle('is-playing', running);
    toggle.hidden = reduceMotion.matches;
    toggle.setAttribute('aria-pressed', String(userPaused));
    toggle.setAttribute('aria-label', userPaused ? 'Riprendi l’animazione' : 'Metti in pausa l’animazione');
    toggle.querySelector('span').textContent = userPaused ? 'Riprendi animazione' : 'Pausa animazione';
    if (reduceMotion.matches) {
      scene.dataset.phase = 'sviluppo';
      message.textContent = 'Il primo passo, insieme.';
      return;
    }
    // Pause shows a complete phrase instead of leaving half a typed word.
    scene.dataset.phase = phases[phase].name;
    message.textContent = phases[phase].text;
    if (running) timer = window.setTimeout(nextPhase, 4400);
  }

  function nextPhase() {
    if (!running) return;
    phase = (phase + 1) % phases.length;
    const current = phases[phase];
    scene.dataset.phase = current.name;
    message.textContent = '';
    const characters = Array.from(current.text);
    let index = 0;
    const typeCharacter = () => {
      if (!running) return;
      message.textContent = characters.slice(0, ++index).join('');
      timer = window.setTimeout(index < characters.length ? typeCharacter : nextPhase,
        index < characters.length ? 42 : 4200);
    };
    typeCharacter();
  }

  toggle.addEventListener('click', () => {
    userPaused = !userPaused;
    syncMotion();
  });
  reduceMotion.addEventListener('change', syncMotion);
  document.addEventListener('visibilitychange', syncMotion);
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting;
      syncMotion();
    }, { threshold: 0.08 }).observe(scene);
  }
  window.addEventListener('pagehide', cancelTimer);
  window.addEventListener('pageshow', syncMotion);
  syncMotion();
})();
