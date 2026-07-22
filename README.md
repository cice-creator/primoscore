# Primoscore

Il progetto comprende sito, questionario, database lead, area amministrativa privata, integrazione Brevo e configurazione per pubblicazione HTTPS.

## Avvio semplice su Mac

Fare doppio clic su `Avvia Primoscore.command`, quindi aprire:

- sito: `http://127.0.0.1:4173`
- area lead: `http://127.0.0.1:4173/admin/`

Al primo avvio viene creata una password amministrativa casuale. La password appare nella finestra del Terminale ed è salvata localmente in `data/admin_password.txt`. L'utente è `admin`.

## Cosa viene salvato

- nome, telefono, email facoltativa e provincia;
- score, fascia e dimensioni del risultato;
- risposte dichiarate nel questionario;
- data dei consensi privacy e ricontatto;
- richiesta specifica di consulenza gratuita per Genova;
- stato commerciale, note e data dell'ultimo contatto.

Il database locale è `data/primoscore.sqlite3` e non viene incluso nel controllo versione.

## Area privata

L'area `/admin/` offre login, riepilogo, ricerca, filtri, priorità per le consulenze richieste, dettaglio del questionario, stati, note ed esportazione CSV.

## Notifiche

Se vengono configurate le variabili SMTP riportate in `configurazione.example.env`, il sistema invia una notifica per ogni nuovo lead e una seconda notifica prioritaria quando viene richiesta la consulenza gratuita.

## Collegamento Brevo

1. In Brevo aprire **SMTP e API → Chiavi API** e creare una chiave dedicata a Primoscore.
2. Creare una lista, ad esempio `Lead Primoscore`, e annotarne l'ID.
3. Copiare `configurazione.example.env` in `.env`.
4. Compilare `BREVO_API_KEY`, `BREVO_LIST_ID`, `BREVO_SENDER_EMAIL` e `LEAD_NOTIFICATION_TO`.
5. Non inserire mai la chiave API nei file del sito o nel browser.

Ogni lead resta nel database Primoscore e viene sincronizzato con Brevo. In assenza di consenso al ricontatto, il contatto viene inserito come bloccato per campagne email e SMS. La richiesta esplicita di consulenza viene sincronizzata nuovamente.

Gli attributi Brevo standard utilizzati sono `FIRSTNAME`, `LASTNAME` e `SMS`. Per sincronizzare anche `PROVINCIA`, `PRIMOSCORE` e `CONSULENZA`, creare prima questi tre attributi in Brevo e impostare `BREVO_CUSTOM_ATTRIBUTES=1`.

## Pubblicazione con Docker

Il pacchetto include `Dockerfile`, `compose.yaml` e `Caddyfile`. Su un server con Docker:

1. puntare il DNS del dominio all'indirizzo IP del server;
2. creare `.env` partendo dal file di esempio;
3. impostare dominio, password amministrativa e credenziali Brevo;
4. avviare con `docker compose up -d --build`;
5. verificare `https://dominio/api/health` e l'area `/admin/`.

Caddy richiede automaticamente il certificato HTTPS. Il database è conservato in un volume persistente.

## Conservazione, audit e backup

- i lead più vecchi di `PRIMOSCORE_RETENTION_DAYS` vengono eliminati automaticamente all'avvio;
- creazione, modifica, richiesta consulenza e cancellazione sono registrate nell'audit locale;
- l'amministratore può eliminare definitivamente un lead;
- `scripts/backup.py` crea una copia SQLite coerente e conserva le ultime copie configurate.

## Prima della pubblicazione

1. completare in Privacy e Condizioni indirizzo, codice fiscale/partita IVA, email e PEC del titolare;
2. far validare informative, consensi, motore e relazione con Euroansa;
3. scegliere una password amministrativa lunga e unica;
4. configurare dominio, Brevo, email e hosting;
5. pianificare backup esterni cifrati e una prova periodica di ripristino;
6. completare la verifica Google Ads per i servizi finanziari prima delle campagne.
