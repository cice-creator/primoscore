# Primoscore — sito e raccolta lead

Prima versione navigabile della landing e del questionario Primoscore.

## Avvio locale

Aprire una shell nella cartella `site` ed eseguire un server statico, ad esempio:

```bash
python3 -m http.server 4173
```

Poi visitare `http://localhost:4173`.

## Stato

- Landing mobile-first
- Quiz in 3 missioni / 11 passaggi
- Provincia obbligatoria
- Salvataggio locale e ripresa del quiz
- Motore score preliminare 0–100
- Raccolta nome e cellulare prima del risultato
- Tracciamento eventi salvato localmente nel browser

Il questionario invia ora i lead al server locale incluso nel progetto. Testi legali, consensi, motore di score e relazione con Euroansa devono comunque essere validati prima della pubblicazione.

## Ottimizzazioni del funnel

- CTA home `Calcola il mio Primoscore`
- score bloccato `??/100` come ricompensa visiva
- avanzamento automatico per le risposte a scelta
- percentuale reale di completamento
- rassicurazioni contestuali prima dei dati delicati
- formattazione automatica degli importi in euro
- selezioni rapide per la liquidità
- elaborazione animata prima della raccolta contatto
- nome e cellulare obbligatori, email facoltativa e richiusa
- footer nascosto durante il questionario

## Eventi misurati localmente

Gli eventi sono salvati in `localStorage` nella chiave `primoscore_events`:

- `quiz_start`
- `step_view`
- `answer_selected`
- `step_complete`, con durata della domanda
- `validation_error`
- `quiz_back`
- `quiz_pause`
- `processing_view`
- `contact_view`
- `lead_submitted`
- `result_view`

Prima della pubblicazione dovranno essere collegati a una piattaforma analytics e a un backend o CRM conforme alle informative definitive.

## SEO e pubblicazione

La configurazione SEO assume come dominio definitivo `https://primoscore.it/` e comprende:

- title e meta description
- canonical e hreflang italiano
- Open Graph e Twitter Card
- dati strutturati `Organization`, `WebSite`, `WebPage`, `WebApplication`, `FAQPage` e `Article`
- contenuti statici people-first, FAQ, metodologia e informazioni sull'autore
- hub editoriale e cinque guide basate su fonti istituzionali
- collegamenti interni tra home, metodologia, autore e guide
- `robots.txt`, `sitemap.xml` e web manifest
- immagine social e asset WebP ottimizzati
- pagine Privacy, Cookie e Condizioni impostate `noindex` finché restano bozze

Prima della pubblicazione:

1. confermare o sostituire il dominio in `index.html`, `robots.txt` e `sitemap.xml`;
2. completare e validare i testi legali;
3. pubblicare il sito in HTTPS;
4. registrare il dominio in Google Search Console;
5. inviare `https://primoscore.it/sitemap.xml` e richiedere l'indicizzazione delle nove pagine presenti;
6. collegare analytics e conversioni solo dopo la configurazione del consenso applicabile.
7. configurare sul server redirect univoco tra `www` e dominio principale, pagina 404 e cache degli asset;
8. verificare Core Web Vitals e risultati avanzati dopo la pubblicazione.
