#!/usr/bin/env python3
import os, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

root=Path(__file__).resolve().parents[1]
source=Path(os.getenv("PRIMOSCORE_DB_PATH",root/"data"/"primoscore.sqlite3"))
destination=Path(os.getenv("PRIMOSCORE_BACKUP_DIR",root/"backups"))
destination.mkdir(parents=True,exist_ok=True)
if not source.exists(): sys.exit("Database non trovato")
target=destination/f"primoscore-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.sqlite3"
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst: src.backup(dst)
os.chmod(target,0o600)
keep=max(3,int(os.getenv("PRIMOSCORE_BACKUP_KEEP","30")))
for old in sorted(destination.glob("primoscore-*.sqlite3"),reverse=True)[keep:]: old.unlink()
print(target)
