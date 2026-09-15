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

A ogni avvio viene salvato un backup consistente prima delle migrazioni; si
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
