const accessNote = document.querySelector('#access-note');
for (const button of document.querySelectorAll('[data-access]')) {
  button.addEventListener('click', () => {
    accessNote.textContent = button.dataset.access === 'master'
      ? 'L’accesso Master sarà disponibile prossimamente.'
      : 'L’accesso consulenti sarà disponibile prossimamente.';
  });
}
