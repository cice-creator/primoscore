#!/bin/zsh
cd "$(dirname "$0")"
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
echo "Avvio Primoscore…"
echo "Il sito sarà disponibile su http://127.0.0.1:4173"
echo "L'area lead sarà disponibile su http://127.0.0.1:4173/admin/"
echo ""
exec python3 server.py
