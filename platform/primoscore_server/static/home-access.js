for(const button of document.querySelectorAll('[data-access]'))button.addEventListener('click',()=>location.assign(button.dataset.access==='master'?'/accesso/master':'/accesso/consulente'));
const note=document.querySelector('#access-note');if(note)note.textContent='Accedi al tuo profilo oppure registra il tuo studio.';
