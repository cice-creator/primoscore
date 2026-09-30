# Primoscore — piattaforma consulenti del credito

La versione attuale è pubblicata su Render dal Dockerfile nella radice, con dominio primoscore.it gestito su Register. La configurazione operativa e le procedure di backup sono in [deploy/render/README.md](deploy/render/README.md).

Voucher, Sviluppo, accessi e motore sono in `platform/`; homepage in `dist/`. Ogni consulente ha dati separati. Il Master gestisce la piattaforma e accede agli studi con motivazione registrata. Nessun dato di CiceroEV è importato e non è attiva alcuna sincronizzazione automatica con CiceroEV.

Brevo gestisce esclusivamente messaggi transazionali. Non si sincronizzano liste marketing o calendari. I file storici `server.py`, `site/`, `compose.yaml` e i vecchi script non sono usati dall’immagine corrente e non vanno usati per avviarla.

Il database, le credenziali e le chiavi non devono mai entrare nel repository.
