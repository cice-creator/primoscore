const provinces = ["Agrigento","Alessandria","Ancona","Aosta","Arezzo","Ascoli Piceno","Asti","Avellino","Bari","Barletta-Andria-Trani","Belluno","Benevento","Bergamo","Biella","Bologna","Bolzano","Brescia","Brindisi","Cagliari","Caltanissetta","Campobasso","Caserta","Catania","Catanzaro","Chieti","Como","Cosenza","Cremona","Crotone","Cuneo","Enna","Fermo","Ferrara","Firenze","Foggia","Forlì-Cesena","Frosinone","Genova","Gorizia","Grosseto","Imperia","Isernia","L'Aquila","La Spezia","Latina","Lecce","Lecco","Livorno","Lodi","Lucca","Macerata","Mantova","Massa-Carrara","Matera","Messina","Milano","Modena","Monza e Brianza","Napoli","Novara","Nuoro","Oristano","Padova","Palermo","Parma","Pavia","Perugia","Pesaro e Urbino","Pescara","Piacenza","Pisa","Pistoia","Pordenone","Potenza","Prato","Ragusa","Ravenna","Reggio Calabria","Reggio Emilia","Rieti","Rimini","Roma","Rovigo","Salerno","Sassari","Savona","Siena","Siracusa","Sondrio","Sud Sardegna","Taranto","Teramo","Terni","Torino","Trapani","Trento","Treviso","Trieste","Udine","Varese","Venezia","Verbano-Cusio-Ossola","Vercelli","Verona","Vibo Valentia","Vicenza","Viterbo"];

const steps = [
  {id:"purpose",mission:1,kicker:"Il tuo progetto",title:"Quale progetto hai in mente?",help:"Scegli la situazione che ti rappresenta meglio.",primo:"Cominciamo dalla parte più semplice: il tuo obiettivo.",type:"choice",options:["Acquisto prima casa","Cambio casa","Seconda casa","Prestito","Sono solo curioso"]},
  {id:"province",mission:1,kicker:"Il punto di partenza",title:"In quale provincia vivi?",help:"Serve a contestualizzare la richiesta e indirizzarla correttamente. Non modifica lo score.",primo:"Ogni percorso parte da un luogo. Da dove iniziamo?",type:"province"},
  {id:"age",mission:1,kicker:"La durata possibile",title:"Quanti anni hai?",help:"L'età serve solo a stimare la durata teorica del progetto.",primo:"Perfetto. Aggiungiamo l'ultima coordinata.",type:"number",placeholder:"Es. 35",min:18,max:79},
  {id:"job",mission:2,kicker:"La tua situazione",title:"Qual è la tua situazione lavorativa?",help:"Scegli la fonte principale del reddito.",primo:"Ora capiamo da dove arriva la forza del tuo reddito.",type:"choice",options:["Dipendente indeterminato","Dipendente determinato","Dipendente pubblico","Autonomo / professionista","Pensionato","Altro"]},
  {id:"income",mission:2,kicker:"La sostenibilità",title:"Qual è il reddito netto mensile complessivo?",help:"Inserisci il totale di chi parteciperà al progetto.",trust:"Questo dato serve solo a stimare la sostenibilità della rata. Non consultiamo banche dati creditizie.",primo:"Nessun giudizio: questo dato mi serve per stimare la sostenibilità.",type:"moneyToggle",toggle:"Ci sarà un cointestatario"},
  {id:"commitments",mission:2,kicker:"Gli impegni attuali",title:"Quanto pagate ogni mese per rate e mantenimenti?",help:"Somma prestiti, finanziamenti, carte rateali ed eventuali mantenimenti. Se non ne hai, inserisci 0.",trust:"Consideriamo soltanto l'importo mensile dichiarato da te.",primo:"Ogni persona ha impegni da gestire. Li consideriamo senza giudizio.",type:"money"},
  {id:"household",mission:2,kicker:"La tua squadra",title:"Quante persone vivono con questo reddito?",help:"Indica adulti sostenuti economicamente e figli a carico.",primo:"Chi partecipa alla tua missione quotidiana?",type:"household"},
  {id:"project",mission:3,kicker:"Il progetto casa",title:"Quanto costa la casa e quanto mutuo vorresti?",help:"Questi dati misurano il rapporto tra valore e importo richiesto.",trust:"È una simulazione informativa: non è una richiesta di finanziamento.",primo:"Ora guardiamo il progetto che vuoi realizzare.",type:"project"},
  {id:"liquidity",mission:3,kicker:"La tua riserva",title:"Quanto puoi destinare ad anticipo e spese?",help:"Puoi scegliere una cifra indicativa oppure inserire l'importo preciso.",primo:"Una riserva aiuta ad affrontare anticipo e spese.",type:"money"},
  {id:"history",mission:3,kicker:"L'ultima informazione",title:"Come sono andati i pagamenti di prestiti e rate?",help:"Scegli la situazione più vicina alla tua. Puoi anche indicare “Non so”.",trust:"Non consultiamo CRIF o altre banche dati: utilizziamo soltanto la tua risposta.",primo:"La regolarità è importante, ma qui non c'è alcun giudizio.",type:"choice",options:["Sempre puntuali","Nessuno storico","Non so","Ritardo poi regolarizzato","Problemi attuali"]},
  {id:"contact",mission:3,kicker:"Il risultato è pronto",title:"Il tuo Primoscore è pronto.",help:"Inserisci nome e cellulare per associare e mostrare il risultato. Questi dati non modificano lo score.",trust:"Il test è gratuito. I tuoi dati non verranno mostrati nel risultato pubblico.",primo:"Ci siamo: il calcolo è terminato.",type:"contact"}
];

const state = JSON.parse(localStorage.getItem("primoscore_state") || "{}");
let current = Math.min(Number(localStorage.getItem("primoscore_step") || 0), steps.length - 1);
let stepStartedAt = Date.now();
let autoAdvanceTimer;
let latestResult=null,latestScoreLabel="",leadSubmitPromise=null;
const sessionId = sessionStorage.getItem("primoscore_session") || crypto.randomUUID();
sessionStorage.setItem("primoscore_session", sessionId);

const $ = selector => document.querySelector(selector);
const views = {landing:$("#landing"),quiz:$("#quiz"),processing:$("#processing"),result:$("#result")};

function track(event, props={}) {
  const log = JSON.parse(localStorage.getItem("primoscore_events") || "[]");
  log.push({event, at:new Date().toISOString(), session_id:sessionId, ...props});
  localStorage.setItem("primoscore_events", JSON.stringify(log.slice(-250)));
}

function showView(name) {
  Object.values(views).forEach(view => view.classList.remove("active"));
  views[name].classList.add("active");
  document.body.dataset.view = name;
  window.scrollTo({top:0});
}

function save() {
  localStorage.setItem("primoscore_state", JSON.stringify(state));
  localStorage.setItem("primoscore_step", current);
}

function formatEuro(value) {
  const digits = String(value ?? "").replace(/\D/g, "");
  return digits ? Number(digits).toLocaleString("it-IT") : "";
}

function euroInput(id, value="", label="Importo mensile") {
  return `<div class="field-wrap"><label for="${id}">${label}</label><span class="input-prefix">€</span><input class="text-input money-input" id="${id}" inputmode="numeric" autocomplete="off" placeholder="0" value="${formatEuro(value)}"></div>`;
}

function choiceMarkup(step) {
  return `<div class="choices">${step.options.map(option => `<button type="button" class="choice ${state[step.id]===option?"selected":""}" data-value="${option}"><span class="choice-dot"></span>${option}</button>`).join("")}</div><p class="auto-hint">Seleziona una risposta: passerai automaticamente alla domanda successiva.</p>`;
}

function progressCopy(percent) {
  if (percent === 0) return "Partiamo dalle basi";
  if (percent < 35) return "Ottimo inizio";
  if (percent < 65) return "Prima fase completata";
  if (percent < 90) return "Ci siamo quasi";
  return "Ultimo passaggio";
}

function trustMarkup(step) {
  return step.trust ? `<div class="trust-note"><span>🔒</span><p>${step.trust}</p></div>` : "";
}

function render() {
  clearTimeout(autoAdvanceTimer);
  const step = steps[current];
  const percent = Math.round(current / (steps.length - 1) * 100);
  $("#step-counter").textContent = percent ? `Profilo al ${percent}%` : "Profilo avviato";
  $("#progress-message").textContent = progressCopy(percent);
  $("#primo-copy").textContent = step.primo;
  document.documentElement.style.setProperty("--quiz-progress", `${percent}%`);
  document.querySelectorAll(".mission").forEach(item => {
    const mission = Number(item.dataset.mission);
    item.classList.toggle("active", mission === step.mission);
    item.classList.toggle("done", mission < step.mission);
  });

  let body = `<span class="step-kicker">${step.kicker}</span><h2 id="question-title">${step.title}</h2><p class="question-help">${step.help}</p>${trustMarkup(step)}`;
  if (step.type === "choice") body += choiceMarkup(step);
  if (step.type === "province") body += `<div class="field-wrap"><label for="province-input">Provincia di residenza</label><input class="text-input" id="province-input" list="province-list" autocomplete="address-level1" placeholder="Inizia a scrivere…" value="${state.province||""}"><datalist id="province-list">${provinces.map(p=>`<option value="${p}">`).join("")}</datalist></div>`;
  if (step.type === "number") body += `<div class="field-wrap"><label for="age-input">Età</label><input class="text-input" id="age-input" type="number" inputmode="numeric" min="${step.min}" max="${step.max}" placeholder="${step.placeholder}" value="${state.age||""}"></div>`;
  if (step.type === "money") {
    body += euroInput(`${step.id}-input`, state[step.id]??"", step.id === "liquidity" ? "Importo disponibile" : "Importo mensile");
    if (step.id === "liquidity") body += `<div class="quick-amounts" aria-label="Importi rapidi"><button type="button" data-amount="15000">€15.000</button><button type="button" data-amount="35000">€35.000</button><button type="button" data-amount="75000">€75.000</button><button type="button" data-amount="120000">€120.000+</button></div>`;
  }
  if (step.type === "moneyToggle") body += euroInput("income-input",state.income??"")+`<div class="toggle-row"><span>${step.toggle}</span><label class="switch"><input id="coapplicant-input" type="checkbox" ${state.coapplicant?"checked":""} aria-label="Ci sarà un cointestatario"><span class="slider"></span></label></div>`;
  if (step.type === "household") body += `<div class="field-grid"><div class="field-wrap"><label for="adults-input">Adulti sostenuti</label><input class="text-input" id="adults-input" type="number" min="1" max="8" inputmode="numeric" value="${state.adults||1}"></div><div class="field-wrap"><label for="children-input">Figli a carico</label><input class="text-input" id="children-input" type="number" min="0" max="10" inputmode="numeric" value="${state.children||0}"></div></div>`;
  if (step.type === "project") body += `<div class="field-grid"><div class="field-wrap"><label for="price-input">Prezzo della casa</label><span class="input-prefix">€</span><input class="text-input money-input" id="price-input" inputmode="numeric" placeholder="250.000" value="${formatEuro(state.price||"")}"></div><div class="field-wrap"><label for="mortgage-input">Mutuo desiderato</label><span class="input-prefix">€</span><input class="text-input money-input" id="mortgage-input" inputmode="numeric" placeholder="180.000" value="${formatEuro(state.mortgage||"")}"></div></div><div class="field-wrap"><label for="term-input">Durata desiderata</label><select class="select-input" id="term-input"><option value="">Seleziona</option>${[10,15,20,25,30].map(n=>`<option value="${n}" ${Number(state.term)===n?"selected":""}>${n} anni</option>`).join("")}</select></div>`;
  if (step.type === "contact") body += `<div class="contact-value"><b>✓ Calcolo completato</b><span>Il numero serve ad associare il risultato, non a calcolarlo.</span></div><div class="field-grid"><div class="field-wrap"><label for="name-input">Nome e cognome <em>obbligatorio</em></label><input class="text-input" id="name-input" autocomplete="name" placeholder="Come ti chiami?" value="${state.name||""}"></div><div class="field-wrap"><label for="phone-input">Cellulare <em>obbligatorio</em></label><input class="text-input" id="phone-input" type="tel" autocomplete="tel" inputmode="tel" placeholder="Es. 333 123 4567" value="${state.phone||""}"></div></div><details class="optional-email"><summary>Aggiungi email <span>facoltativa</span></summary><div class="field-wrap"><label for="email-input">Email</label><input class="text-input" id="email-input" type="email" autocomplete="email" placeholder="Per ricevere il riepilogo" value="${state.email||""}"></div></details><label class="check-row"><input type="checkbox" id="privacy-input" ${state.privacy?"checked":""}><span>Dichiaro di aver letto l'<a href="privacy.html" target="_blank" rel="noopener">informativa privacy</a>. <b>Obbligatorio</b></span></label><label class="check-row"><input type="checkbox" id="contact-input" ${state.contactConsent?"checked":""}><span>Desidero essere ricontattato per approfondire il progetto indicato. <b>Facoltativo</b></span></label>`;

  body += `<p class="error" id="error" role="alert"></p>`;
  $("#question-content").innerHTML = body;
  $("#question-card").classList.toggle("choice-step", step.type === "choice");
  $("#continue-btn").innerHTML = step.type === "contact" ? `Mostrami il mio Primoscore <span>→</span>` : `Continua <span>→</span>`;
  bind();
  stepStartedAt = Date.now();
  track("step_view", {step_id:step.id, step_index:current, progress_percent:percent});
  if (step.type === "contact") track("contact_view", {province:state.province});
}

function bind() {
  const step = steps[current];
  document.querySelectorAll(".choice").forEach(button => button.onclick = () => {
    if (button.disabled) return;
    state[step.id] = button.dataset.value;
    document.querySelectorAll(".choice").forEach(item => {item.classList.toggle("selected",item===button);item.disabled=true;});
    save();
    track("answer_selected", {step_id:step.id, answer_type:"choice"});
    autoAdvanceTimer = setTimeout(() => advance(), 320);
  });
  document.querySelectorAll(".money-input").forEach(input => input.addEventListener("input", () => {
    const position = input.selectionStart;
    const before = input.value;
    input.value = formatEuro(input.value);
    const delta = input.value.length - before.length;
    input.setSelectionRange(Math.max(0,position+delta),Math.max(0,position+delta));
  }));
  document.querySelectorAll(".quick-amounts button").forEach(button => button.onclick = () => {
    $("#liquidity-input").value = formatEuro(button.dataset.amount);
    document.querySelectorAll(".quick-amounts button").forEach(item=>item.classList.toggle("selected",item===button));
  });
}

function val(selector) { const element=$(selector); return element ? element.value.trim() : ""; }
function num(selector) { return Number(val(selector).replace(/[^0-9]/g,"")); }
function fail(message) { $("#error").textContent=message; track("validation_error",{step_id:steps[current].id,message}); return false; }

function collect() {
  const step=steps[current], error=$("#error");
  error.textContent="";
  if (step.type==="choice" && !state[step.id]) return fail("Scegli una risposta per continuare.");
  if (step.type==="province") {const value=val("#province-input");if(!provinces.includes(value))return fail("Seleziona una provincia dall'elenco.");state.province=value;}
  if (step.type==="number") {const value=num("#age-input");if(value<18||value>79)return fail("Inserisci un'età compresa tra 18 e 79 anni.");state.age=value;}
  if (step.type==="money") {const value=num(`#${step.id}-input`);if(step.id!=="commitments"&&value<=0)return fail("Inserisci un importo valido.");state[step.id]=value;}
  if (step.type==="moneyToggle") {const value=num("#income-input");if(value<500)return fail("Inserisci il reddito netto mensile complessivo.");state.income=value;state.coapplicant=$("#coapplicant-input").checked;}
  if (step.type==="household") {state.adults=num("#adults-input");state.children=num("#children-input");if(state.adults<1)return fail("Indica almeno un adulto.");}
  if (step.type==="project") {state.price=num("#price-input");state.mortgage=num("#mortgage-input");state.term=num("#term-input");if(!state.price||!state.mortgage||!state.term)return fail("Completa prezzo, mutuo e durata.");if(state.mortgage>state.price*1.1)return fail("Controlla l'importo del mutuo rispetto al prezzo.");}
  if (step.type==="contact") {state.name=val("#name-input");state.phone=val("#phone-input");state.email=val("#email-input");state.privacy=$("#privacy-input").checked;state.contactConsent=$("#contact-input").checked;if(state.name.length<3)return fail("Inserisci nome e cognome.");if(state.phone.replace(/\D/g,"").length<9)return fail("Inserisci un cellulare valido.");if(!state.privacy)return fail("Conferma di aver letto l'informativa privacy.");}
  save();
  track("step_complete",{step_id:step.id,duration_ms:Date.now()-stepStartedAt,answer_type:step.type});
  return true;
}

function advance() {
  if (!collect()) {document.querySelectorAll(".choice").forEach(item=>item.disabled=false);return;}
  if (current===steps.length-1) return showResult();
  if (steps[current].id==="history") return showProcessing();
  current++;
  save();
  render();
}

function showProcessing() {
  track("processing_view");
  showView("processing");
  const card=$(".processing-card");
  card.classList.remove("run");
  requestAnimationFrame(()=>card.classList.add("run"));
  setTimeout(()=>{current++;save();showView("quiz");render();},2100);
}

function estimatedPayment(principal,years) {const rate=.04/12,months=years*12;return principal*rate*Math.pow(1+rate,months)/(Math.pow(1+rate,months)-1);}
function calculate() {
  const payment=estimatedPayment(state.mortgage,state.term),rrr=(payment+(state.commitments||0))/(state.income||1),ltv=state.mortgage/state.price,ageEnd=state.age+state.term;
  const rrrPts=rrr<=.30?20:rrr<=.33?17:rrr<=.35?13:rrr<=.40?8:rrr<=.50?3:0;
  const base=1000+(Math.max(1,state.adults)-1)*350+(state.children||0)*250,residual=state.income-payment-(state.commitments||0);
  const residualPts=residual>=base*1.5?15:residual>=base*1.2?12:residual>=base?8:residual>=base*.8?3:0;
  let jobPts={"Dipendente indeterminato":20,"Dipendente pubblico":20,"Autonomo / professionista":16,"Pensionato":16,"Dipendente determinato":12,"Altro":8}[state.job]||8;
  if(state.coapplicant)jobPts=Math.min(20,jobPts+2);
  const ltvPts=ltv<=.7?12:ltv<=.8?10:ltv<=.9?6:ltv<=1?2:0;
  const required=Math.max(0,state.price-state.mortgage)+state.price*.07,coverage=state.liquidity/(required||1),liqPts=coverage>=1?8:coverage>=.8?5:coverage>=.5?2:0;
  const histPts={"Sempre puntuali":20,"Nessuno storico":12,"Non so":10,"Ritardo poi regolarizzato":5,"Problemi attuali":0}[state.history]??10;
  const agePts=ageEnd<=75?5:ageEnd<=80?2:0;
  let score=Math.round(rrrPts+residualPts+jobPts+ltvPts+liqPts+histPts+agePts);
  if(state.history==="Problemi attuali")score=Math.min(score,45);
  return {score,payment,dimensions:{Sostenibilità:Math.round((rrrPts+residualPts)/35*100),Stabilità:Math.round(jobPts/20*100),"Rapporto mutuo/valore":Math.round(ltvPts/12*100),Liquidità:Math.round(liqPts/8*100),Affidabilità:Math.round(histPts/20*100)}};
}

function leadPayload(result,label) {
  const answerKeys=["purpose","age","job","income","coapplicant","commitments","adults","children","price","mortgage","term","liquidity","history"];
  const answers=Object.fromEntries(answerKeys.filter(key=>state[key]!==undefined).map(key=>[key,state[key]]));
  return {name:state.name,phone:state.phone,email:state.email||"",province:state.province,score:result.score,score_band:label,dimensions:result.dimensions,answers,privacy_consent:Boolean(state.privacy),contact_consent:Boolean(state.contactConsent),consent_version:"2026-07-20",source:new URLSearchParams(location.search).get("utm_source")||"website"};
}

async function ensureLeadSaved(result=latestResult,label=latestScoreLabel) {
  if(state.serverLeadId&&state.serverLeadToken)return {id:state.serverLeadId,token:state.serverLeadToken};
  if(leadSubmitPromise)return leadSubmitPromise;
  leadSubmitPromise=fetch("/api/leads",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(leadPayload(result,label))}).then(async response=>{
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||"Invio del lead non riuscito");
    state.serverLeadId=data.id;state.serverLeadToken=data.token;save();
    track("lead_saved_server",{lead_id:data.id,province:state.province});
    return data;
  }).catch(error=>{
    leadSubmitPromise=null;
    localStorage.setItem("primoscore_pending_lead",JSON.stringify({payload:leadPayload(result,label),saved_at:new Date().toISOString()}));
    track("lead_server_error",{message:error.message});
    throw error;
  });
  return leadSubmitPromise;
}

function showResult() {
  const result=calculate();
  let label="Profilo da costruire",message="Il progetto presenta alcuni aspetti da approfondire con attenzione.";
  if(result.score>=81){label="Profilo molto forte";message="I dati dichiarati mostrano una struttura complessivamente molto solida.";}
  else if(result.score>=70){label="Profilo solido";message="Il progetto mostra diversi punti di forza e una buona sostenibilità teorica.";}
  else if(result.score>=60){label="Buona base";message="Il progetto parte da una buona base, con alcuni elementi da approfondire.";}
  else if(result.score>=40){label="Profilo da rafforzare";message="Alcune aree possono essere migliorate prima di procedere.";}
  latestResult=result;latestScoreLabel=label;
  $("#score-value").textContent=result.score;$("#score-label").textContent=label;$("#score-message").textContent=message;$("#score-ring").style.setProperty("--score",result.score);
  $("#dimension-list").innerHTML=Object.entries(result.dimensions).map(([key,value])=>`<div class="dimension"><div class="dimension-head"><span>${key}</span><span>${value}%</span></div><div class="dimension-track"><div class="dimension-bar" style="width:${value}%"></div></div></div>`).join("");
  const genovaCard=$("#genova-consultation");
  const isGenova=String(state.province||"").trim().toLowerCase()==="genova";
  genovaCard.hidden=!isGenova;
  if(isGenova){
    const requested=Boolean(state.genovaConsultationRequested);
    $("#consultation-btn").hidden=requested;
    $("#consultation-success").hidden=!requested;
    track("genova_consultation_view",{score:result.score});
  }
  track("lead_submitted",{province:state.province});track("result_view",{score:result.score,score_band:label});showView("result");
  ensureLeadSaved(result,label).catch(()=>{});
}

$("#start-btn").onclick=()=>{track("quiz_start",{resumed:current>0});showView("quiz");render();};
$(".seo-start-btn").onclick=()=>{track("quiz_start",{source:"seo_content",resumed:current>0});showView("quiz");render();};
$("#continue-btn").onclick=advance;
$("#back-btn").onclick=()=>{clearTimeout(autoAdvanceTimer);if(current===0){showView("landing");return;}track("quiz_back",{from_step:steps[current].id});current--;save();render();};
$("#restart-btn").onclick=()=>{track("quiz_restart");localStorage.removeItem("primoscore_state");localStorage.removeItem("primoscore_step");location.reload();};
$("#consultation-btn").onclick=async()=>{
  const button=$("#consultation-btn"),error=$("#consultation-error");
  error.textContent="";button.disabled=true;button.textContent="Invio della richiesta…";
  try{
    const lead=await ensureLeadSaved();
    const response=await fetch(`/api/leads/${lead.id}/consultation`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token:lead.token})});
    const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.error||"Richiesta non inviata");
    state.genovaConsultationRequested=true;save();button.hidden=true;$("#consultation-success").hidden=false;
    track("genova_consultation_requested",{province:state.province,phone_provided:Boolean(state.phone),lead_id:lead.id});
  }catch(err){error.textContent="Non siamo riusciti a inviare la richiesta. Riprova tra poco.";button.disabled=false;button.innerHTML='Sì, desidero essere contattato <span aria-hidden="true">→</span>';track("genova_consultation_error",{message:err.message});}
};
document.addEventListener("visibilitychange",()=>{if(document.hidden&&document.body.dataset.view==="quiz")track("quiz_pause",{step_id:steps[current].id,step_index:current});});

showView("landing");
if(Object.keys(state).length&&current>0){$("#start-btn").innerHTML="Riprendi il mio Primoscore <span>→</span>";track("quiz_resumed",{last_step:steps[current].id});}
