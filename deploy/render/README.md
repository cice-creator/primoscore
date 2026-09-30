# Primoscore su Render

Il servizio esistente usa Docker, piano Starter e disco da 1 GB in `/app/data`.
Il nuovo archivio è `/app/data/primoscore-v2/primoscore.sqlite3`. La chiave viene
creata una sola volta in `auth.key` nella stessa directory privata; se manca su
un archivio esistente il servizio si arresta. Nessun dato precedente viene importato.

Il processo iniziale prepara la directory e abbandona i privilegi root. Avvia un
worker Gunicorn con due thread e un processo di invio email. Se uno termina,
l'intero servizio termina per permettere il riavvio di Render. Il disco consente
una sola istanza; non aumentare le repliche senza migrare l'archivio.

Configurazione di default:
- origine `https://primoscore.it`
- mittente e account Master `info@primoscore.it`
- trasporto Brevo, con `BREVO_API_KEY` già custodita in Render
- porta da `PORT`, altrimenti 4173
- controllo HTTP `/healthz`

Prima dell'apertura operativa verificare il mittente Brevo e creare il Master:
```
python /app/deploy/render/runtime.py status
python /app/deploy/render/runtime.py bootstrap-master
```
Il secondo comando richiede una password scelta dal titolare, poi la verifica
email e l'attivazione di Google Authenticator. Non ci sono password predefinite
né trasferimenti di credenziali dalla vecchia applicazione.

A ogni avvio viene salvato un backup consistente prima delle migrazioni e il processo email ne crea uno ogni 24 ore; si
conservano gli ultimi sette. Il backup locale non copre la perdita del disco:
per il ripristino conservare esternamente sia un backup SQLite sia `auth.key`.
Non ripristinare una vecchia versione del codice contro un database migrato.

Le vecchie variabili PRIMOSCORE_ADMIN_* e BREVO_LIST_ID/CUSTOM_ATTRIBUTES non sono
usate dalla nuova piattaforma. Non viene effettuata sincronizzazione marketing
né sincronizzazione dei calendari. Il motore è la versione fissata dal manifest;
Primoscore viene aggiornato autonomamente: la sincronizzazione con CiceroEV è esclusa.

I file storici server.py/site/compose.yaml restano nello storico del progetto e
non entrano nell'immagine Docker attuale. Nessun database, account demo, file .env
o chiave privata deve essere aggiunto al repository.


## Verifiche operative e ripristino

Il Master vede invii falliti, età dell’ultima copia locale e spazio libero nel profilo. Esaminare un backup oltre 26 ore, meno di 100 MB liberi o email fallite. Il pulsante di reinvio conserva i controlli di validità dei collegamenti. `/healthz` risponde 503 quando l’archivio non è leggibile.

Conservare esternamente database e chiave, con accesso limitato al titolare e protezione del dispositivo/archivio. La destinazione indipendente deve essere scelta prima di dichiarare completata questa misura. Le sette copie locali e gli snapshot Render non sono indipendenti dal fornitore.

Per un ripristino: fermare scritture e invii; preservare lo stato corrente e il registro `privacy_erasures`; lavorare su una copia in ambiente isolato e senza email; verificare `PRAGMA integrity_check` e `foreign_key_check`, corrispondenza della chiave e decifratura dei dati; applicare le migrazioni della versione corrente; riapplicare le anonimizzazioni successive alla copia utilizzando `operations.anonymize` dopo aver reinserito le rispettive righe del registro. Revocare sessioni e collegamenti pendenti, cancellare le code di invio nella copia ripristinata per evitare duplicati. Collaudare accessi e isolamento con dati sintetici. Riattivare l’invio solo dopo la validazione. Non ripristinare direttamente un vecchio snapshot sul servizio attivo.

## Richieste sui dati

Il consulente verifica l’identità del richiedente e gli eventuali obblighi di conservazione, esporta l’archivio da Clienti se richiesto e usa “Anonimizza” nella scheda cliente. L’operazione richiede la parola ANONIMIZZA, verifica studio e revisione, revoca l’accesso del cliente e rimuove recapiti, risposte, risultati, note e messaggi pendenti. Rimangono identificatori tecnici e storico operativo senza i recapiti rimossi: non è una cancellazione fisica dell’intera riga. Non inserire dati personali nelle motivazioni amministrative.

Le copie precedenti conservano i dati fino alla rotazione: sette copie, prodotte ogni giorno e a ogni riavvio. Gli snapshot del fornitore seguono la sua conservazione. Ogni ripristino deve applicare il registro delle anonimizzazioni prima della riapertura. Conservare il registro anche nella destinazione indipendente. In caso di perdita simultanea di tutte le copie recenti, non riaprire una copia vecchia senza riconciliare le richieste di cancellazione.

Non è impostato un termine arbitrario di cancellazione dei clienti: ogni studio deve definire e documentare i propri tempi e gestire le richieste. Prima dell’uso commerciale occorrono l’accordo sul trattamento effettivamente concluso tra le parti e la verifica dei fornitori e delle rispettive condizioni. Il software non firma accordi per conto delle parti. L’informativa del singolo studio resta obbligatoria per la raccolta tramite QR; lo studio di collaudo accetta soltanto dati inventati.
