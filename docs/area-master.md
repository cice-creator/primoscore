# Area Master

La nuova area è disponibile in `/master/`; il profilo Master vi conduce automaticamente dopo l’accesso. Include panoramica, ricerca per studio/consulente/città, filtro per stato, elenco con 20 studi per pagina e scheda dedicata.

QR generati indica codici unici (voucher città e campagne), non copie stampate. Lead raccolti conta i contatti dello studio, escludendo quelli anonimizzati. Ogni contatto è attribuito al QR di origine; i contatti senza QR sono indicati separatamente. Valutazioni completate conta i contatti con almeno un risultato non anonimizzato; non coincide con la qualificazione manuale dei partner. I totali comprendono gli studi sospesi.

Per aprire una scheda serve una motivazione. Scheda e dettaglio lead sono riservati al Master, controllati sul server e registrati nel registro dello studio. Non sono esposti risposte finanziarie, credenziali o segreti Authenticator. La scheda mostra dati professionali, privacy, account consulenti e ultime 50 attività Master. Attivazione, sospensione e riattivazione usano le operazioni autorizzate esistenti. La panoramica conserva lo stato degli invii, backup e spazio disponibile, con possibilità di riprovare gli invii falliti.

Le date sono visualizzate nel fuso Europe/Rome. Nessuna migrazione è necessaria. La demo usa un archivio temporaneo con dati sintetici. Verifica dalla cartella platform: `.venv/bin/python -m unittest discover -s tests -q`.
