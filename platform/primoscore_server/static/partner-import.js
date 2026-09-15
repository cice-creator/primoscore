'use strict';
// Workbook contents stay in this review until the consultant confirms selected rows.
const importDialog=document.createElement('dialog');
importDialog.className='import-dialog';importDialog.setAttribute('aria-labelledby','import-title');
document.body.append(importDialog);
let importReport=null,importBusy=false,importFilename='';
const importRows=()=>importReport.rows.map(r=>({...r.fields,row:r.row,numericPhone:r.numericPhone}));
function importScreen(){
 const r=importReport;
 importDialog.innerHTML=`<div class="dialog-top"><div><p class="eyebrow">SVILUPPO · PRIMOSCORE</p><h2 id="import-title">Importa partner da Excel</h2></div><button type="button" class="plain" data-import="close" aria-label="Chiudi">×</button></div><p>Compila il modello con i tuoi partner. Dopo il controllo potrai scegliere le righe da aggiungere al tuo studio.</p><div class="actions"><button type="button" class="secondary" data-import="template">Scarica modello Excel</button></div><form id="import-upload"><label>File compilato (.xlsx, massimo 5 MB e 500 partner)<input name="file" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required></label><button type="submit">Controlla il file</button></form><p id="import-error" role="alert"></p>`+
 (r?`<p><strong>File controllato:</strong> ${esc(importFilename)}</p><div class="stats">${stat('Pronti da importare',r.counts.ready)}${stat('Possibili duplicati',r.counts.duplicate)}${stat('Da correggere',r.counts.error)}</div><p>I duplicati e le righe con errori sono esclusi. Puoi correggere le righe e ripetere il controllo. I partner già presenti rimangono invariati.</p>${r.newCities.length?`<p><strong>Nuove città nelle righe valide:</strong> ${r.newCities.map(esc).join(', ')}. Verranno create solo per i partner selezionati.</p>`:''}<div class="actions"><label class="check"><input type="checkbox" id="import-all" ${r.counts.ready?'checked':''}>Seleziona tutti i nuovi partner validi</label><button type="button" class="secondary small" data-import="review">Ricontrolla</button></div><div class="table-wrap"><table><thead><tr><th>Importa</th><th>Riga</th><th>Partner e recapiti</th><th>Località</th><th>Controllo</th><th></th></tr></thead><tbody>${r.rows.map((row,i)=>`<tr><td><input type="checkbox" data-import-row="${i}" aria-label="Importa riga ${row.row}" ${row.kind==='ready'?'checked':'disabled'}></td><td>${row.row}</td><td><strong>${esc(row.fields.name||'Nome mancante')}</strong><small>${esc(row.fields.category)}</small><small>${esc([row.fields.phone,row.fields.mobile,row.fields.email].filter(Boolean).join(' · '))}</small></td><td>${esc(row.fields.city)}</td><td><span class="badge ${row.kind==='ready'?'active':'overdue'}">${{ready:'Pronto',duplicate:'Possibile duplicato',error:'Da correggere'}[row.kind]}</span>${row.issues.map(v=>`<p class="import-issue">${esc(v)}</p>`).join('')}${row.duplicate?`<p>${esc(row.duplicate.reason)}<br><strong>${esc(row.duplicate.name)}</strong>${row.duplicate.row?' · riga '+row.duplicate.row:''}</p>`:''}${row.warnings.map(v=>`<p class="muted">${esc(v)}</p>`).join('')}</td><td><button type="button" class="secondary small" data-import="edit" data-row="${i}">Rivedi / correggi</button></td></tr>`).join('')}</tbody></table></div><p class="muted">Voucher e lead storici saranno salvati nelle note. L’importazione non registra stampe, consegne o clienti.</p><div class="actions"><button type="button" data-import="commit" ${r.counts.ready?'':'disabled'}>Importa ${r.counts.ready} partner selezionati</button><button type="button" class="secondary" data-import="close">Annulla</button></div>`:'');
 importDialog.querySelector('#import-upload').onsubmit=async e=>{e.preventDefault();if(importBusy)return;await importAction(async()=>{
  const file=new FormData(e.currentTarget).get('file');if(!file?.size)throw Error('Scegli un file Excel compilato.');if(file.size>5*1024*1024)throw Error('Il file supera 5 MB.');
  // Discard the prior review before checking a different file, even if it fails.
  importReport=null;importFilename=file.name;importScreen();
  importDialog.querySelectorAll('button').forEach(b=>b.disabled=true);
  const body=new FormData();body.append('file',file);
  const response=await fetch('/api/workspace/import/preview',{method:'POST',credentials:'same-origin',headers:{...headers(),'X-CSRF-Token':csrf},body});
  const result=await response.json();if(!response.ok)throw Error(result.error||'Controllo non riuscito.');importReport=result;importScreen();
 })};
}
async function importAction(fn){
 importBusy=true;importDialog.querySelectorAll('button').forEach(b=>b.disabled=true);importDialog.querySelector('#import-error').textContent='';
 try{await fn()}catch(e){importDialog.querySelector('#import-error').textContent=e.message}finally{importBusy=false;importDialog.querySelectorAll('button').forEach(b=>b.disabled=false);importSelection()}
}
function importSelection(){
 const boxes=[...importDialog.querySelectorAll('[data-import-row]:not(:disabled)')],count=boxes.filter(b=>b.checked).length,commit=importDialog.querySelector('[data-import="commit"]'),all=importDialog.querySelector('#import-all');
 if(commit){commit.disabled=importBusy||!count;commit.textContent=`Importa ${count} partner selezionati`}
 if(all){all.checked=count>0&&count===boxes.length;all.indeterminate=count>0&&count<boxes.length}
}
importDialog.addEventListener('change',e=>{if(e.target.id==='import-all')importDialog.querySelectorAll('[data-import-row]:not(:disabled)').forEach(b=>b.checked=e.target.checked);importSelection()});
importDialog.addEventListener('cancel',e=>{if(importBusy)e.preventDefault()});
importDialog.addEventListener('click',async e=>{
 const b=e.target.closest('[data-import]');if(!b||importBusy)return;
 const action=b.dataset.import;
 if(action==='close'){importDialog.close();return}
 if(action==='edit'){
  const index=Number(b.dataset.row),row=importReport.rows[index];
  const html=importReport.columns.map(c=>field(c.key,c.label,row.fields[c.key]||'',['notes','outcome','nextAction','maps'].includes(c.key)?'textarea':'text',c.key==='name')).join('');
  form('Rivedi riga '+row.row,grid(html),async values=>{const rows=importRows();rows[index]={...values,row:row.row,numericPhone:row.numericPhone};importReport=await api('workspace/import/preview',{rows});importScreen()},'Ricontrolla le righe');return;
 }
 await importAction(async()=>{
  if(action==='template')await download('workspace/import/template','Primoscore-Modello-Partner.xlsx');
  else if(action==='review'){importReport=await api('workspace/import/preview',{rows:importRows()});importScreen()}
  else if(action==='commit'){
   const selection=[...importDialog.querySelectorAll('[data-import-row]:checked')].map(b=>Number(b.dataset.importRow));
   const result=await api('workspace/import/commit',{importId:importReport.importId,selection});
   importDialog.close();importReport=null;await load();notice(`${result.inserted} nuovi partner importati.${result.newCities.length?' Aggiunte '+result.newCities.length+' città.':''}`);
  }
 });
});
root.addEventListener('click',async e=>{
 const b=e.target.closest('[data-action^="import."]');if(!b)return;
 if(b.dataset.action==='import.open'){importReport=null;importFilename='';importScreen();importDialog.showModal()}
 else try{await download('workspace/import/template','Primoscore-Modello-Partner.xlsx')}catch(e){notice(e.message)}
});
