#!/usr/bin/env python3
"""Server Primoscore: sito, raccolta lead e area amministrativa privata."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from http import cookies
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
DATA = Path(os.getenv("PRIMOSCORE_DATA_DIR", str(ROOT / "data"))).resolve()
DB_PATH = DATA / "primoscore.sqlite3"
PASSWORD_FILE = DATA / "admin_password.txt"
HOST = os.getenv("PRIMOSCORE_HOST", "127.0.0.1")
PORT = int(os.getenv("PRIMOSCORE_PORT", "4173"))
PUBLIC_ENABLED = os.getenv("PRIMOSCORE_PUBLIC_ENABLED", "0") == "1"
ADMIN_USER = os.getenv("PRIMOSCORE_ADMIN_USER", "Primoscore2026")
SESSION_TTL = 8 * 60 * 60
MAX_BODY = 64 * 1024
STATUSES = {"new", "to_contact", "contacted", "appointment", "closed", "discarded"}
RETENTION_DAYS = max(30, int(os.getenv("PRIMOSCORE_RETENTION_DAYS", "365")))
sessions: dict[str, dict] = {}
rate_limits: dict[str, list[float]] = {}
lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_storage() -> str:
    DATA.mkdir(mode=0o700, exist_ok=True)
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
          id TEXT PRIMARY KEY,
          public_token_hash TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          name TEXT NOT NULL,
          phone TEXT NOT NULL,
          email TEXT,
          province TEXT NOT NULL,
          score INTEGER NOT NULL,
          score_band TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'new',
          consultation_requested INTEGER NOT NULL DEFAULT 0,
          consultation_requested_at TEXT,
          privacy_consent INTEGER NOT NULL,
          contact_consent INTEGER NOT NULL,
          consent_at TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'website',
          answers_json TEXT NOT NULL,
          dimensions_json TEXT NOT NULL,
          notes TEXT NOT NULL DEFAULT '',
          last_contact_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_leads_province ON leads(province);
        CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
        CREATE INDEX IF NOT EXISTS idx_leads_consultation ON leads(consultation_requested);
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL,
          lead_id TEXT, actor TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
        );
        """)
        for statement in (
            "ALTER TABLE leads ADD COLUMN brevo_synced INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE leads ADD COLUMN brevo_error TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE leads ADD COLUMN consent_version TEXT NOT NULL DEFAULT '2026-07-20'",
        ):
            try: conn.execute(statement)
            except sqlite3.OperationalError: pass
    configured = os.getenv("PRIMOSCORE_ADMIN_PASSWORD")
    if configured:
        return configured
    if PASSWORD_FILE.exists():
        return PASSWORD_FILE.read_text(encoding="utf-8").strip()
    generated = "PS-" + secrets.token_urlsafe(12)
    PASSWORD_FILE.write_text(generated, encoding="utf-8")
    os.chmod(PASSWORD_FILE, 0o600)
    return generated


ADMIN_PASSWORD = init_storage()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_text(value, limit=200) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip()[:limit]


def notify(subject: str, text: str) -> None:
    if os.getenv("BREVO_API_KEY") and os.getenv("LEAD_NOTIFICATION_TO") and os.getenv("BREVO_SENDER_EMAIL"):
        try:
            brevo_call("/smtp/email", {"sender":{"name":os.getenv("BREVO_SENDER_NAME","Primoscore"),"email":os.getenv("BREVO_SENDER_EMAIL")},"to":[{"email":os.getenv("LEAD_NOTIFICATION_TO")}],"subject":subject,"textContent":text})
            return
        except Exception as exc: print(f"[notifica Brevo non inviata] {exc}")
    host = os.getenv("SMTP_HOST")
    recipient = os.getenv("LEAD_NOTIFICATION_TO")
    if not host or not recipient:
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "primoscore@localhost"))
        msg["To"] = recipient
        msg.set_content(text)
        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=12) as smtp:
            if os.getenv("SMTP_TLS", "1") == "1": smtp.starttls()
            if os.getenv("SMTP_USER"): smtp.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except Exception as exc:
        print(f"[notifica non inviata] {exc}")


def brevo_call(path: str, payload: dict):
    key=os.getenv("BREVO_API_KEY")
    if not key: raise RuntimeError("Brevo non configurato")
    request=urllib.request.Request("https://api.brevo.com/v3"+path,data=json.dumps(payload).encode(),method="POST",headers={"api-key":key,"content-type":"application/json","accept":"application/json"})
    try:
        with urllib.request.urlopen(request,timeout=15) as response:
            raw=response.read(); return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail=exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Brevo HTTP {exc.code}: {detail}") from exc


def normalize_phone(phone: str) -> str:
    digits=re.sub(r"\D","",phone)
    if digits.startswith("00"): return "+"+digits[2:]
    if digits.startswith("39"): return "+"+digits
    return "+39"+digits


def sync_brevo_contact(lead_id: str) -> None:
    if not os.getenv("BREVO_API_KEY"): return
    with db() as conn: row=conn.execute("SELECT * FROM leads WHERE id=?",(lead_id,)).fetchone()
    if not row: return
    parts=row["name"].split(maxsplit=1); attrs={"FIRSTNAME":parts[0],"SMS":normalize_phone(row["phone"])}
    if len(parts)>1: attrs["LASTNAME"]=parts[1]
    if os.getenv("BREVO_CUSTOM_ATTRIBUTES","0")=="1": attrs.update({"PROVINCIA":row["province"],"PRIMOSCORE":row["score"],"CONSULENZA":bool(row["consultation_requested"])})
    payload={"ext_id":row["id"],"attributes":attrs,"updateEnabled":True,"getId":True,"emailBlacklisted":not bool(row["contact_consent"]),"smsBlacklisted":not bool(row["contact_consent"])}
    if row["email"]: payload["email"]=row["email"]
    list_id=os.getenv("BREVO_LIST_ID")
    if list_id: payload["listIds"]=[int(list_id)]
    try:
        brevo_call("/contacts",payload)
        with db() as conn: conn.execute("UPDATE leads SET brevo_synced=1,brevo_error='' WHERE id=?",(lead_id,))
    except Exception as exc:
        with db() as conn: conn.execute("UPDATE leads SET brevo_synced=0,brevo_error=? WHERE id=?",(clean_text(exc,500),lead_id))


def audit(action: str, lead_id: str | None = None, detail: str = "") -> None:
    with db() as conn: conn.execute("INSERT INTO audit_log(at,action,lead_id,actor,detail) VALUES(?,?,?,?,?)",(now_iso(),action,lead_id,ADMIN_USER,clean_text(detail,500)))


def cleanup_expired() -> int:
    cutoff=time.time()-RETENTION_DAYS*86400; removed=0
    with db() as conn:
        rows=conn.execute("SELECT id,created_at FROM leads").fetchall()
        for row in rows:
            try: expired=datetime.fromisoformat(row["created_at"]).timestamp()<cutoff
            except ValueError: expired=False
            if expired: conn.execute("DELETE FROM leads WHERE id=?",(row["id"],)); removed+=1
    return removed


class Handler(SimpleHTTPRequestHandler):
    server_version = "Primoscore/1.0"

    def unavailable(self, include_body=True):
        """Disable every public route while retaining the deployment intact."""
        body = b"Primoscore non e al momento disponibile."
        self.send_response(410)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_HEAD(self):
        if not PUBLIC_ENABLED:
            return self.unavailable(include_body=False)
        return super().do_HEAD()

    def translate_path(self, path):
        parsed = urlparse(path).path
        if parsed == "/": parsed = "/index.html"
        if parsed == "/admin" or parsed == "/admin/": parsed = "/admin/index.html"
        candidate = (SITE / parsed.lstrip("/")).resolve()
        return str(candidate if str(candidate).startswith(str(SITE.resolve())) else SITE / "404.html")

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        interactive_pages = ("/area-clienti.html", "/questionario-dettagliato.html")
        if urlparse(self.path).path in interactive_pages:
            csp = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        else:
            csp = "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        self.send_header("Content-Security-Policy", csp)
        if self.path.startswith("/admin") or self.path.startswith("/api/admin"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY: raise ValueError("Corpo richiesta non valido")
        return json.loads(self.rfile.read(length))

    def send_json(self, status, payload, extra_headers=None):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (extra_headers or {}).items(): self.send_header(key, value)
        self.end_headers(); self.wfile.write(raw)

    def rate_ok(self, key, maximum=20, window=60):
        now = time.time()
        with lock:
            values = [x for x in rate_limits.get(key, []) if now - x < window]
            if len(values) >= maximum: return False
            values.append(now); rate_limits[key] = values
        return True

    def session(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = jar.get("ps_admin")
        if not sid: return None
        item = sessions.get(sid.value)
        if not item or item["expires"] < time.time():
            sessions.pop(sid.value, None); return None
        item["expires"] = time.time() + SESSION_TTL
        return item

    def require_admin(self, csrf=False):
        session = self.session()
        if not session:
            self.send_json(401, {"error": "Autenticazione richiesta"}); return None
        if csrf and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
            self.send_json(403, {"error": "Protezione richiesta non valida"}); return None
        return session

    def do_GET(self):
        if not PUBLIC_ENABLED:
            return self.unavailable()
        parsed = urlparse(self.path)
        if parsed.path == "/api/health": return self.send_json(200, {"ok": True, "time": now_iso()})
        if parsed.path == "/api/public/config": return self.send_json(200,{"privacy_version":"2026-07-20","retention_days":RETENTION_DAYS,"brevo_enabled":bool(os.getenv("BREVO_API_KEY"))})
        if parsed.path == "/api/admin/session":
            session = self.session()
            return self.send_json(200, {"authenticated": bool(session), "csrf": session["csrf"] if session else None, "user": ADMIN_USER if session else None})
        if parsed.path == "/api/admin/leads": return self.admin_leads(parsed)
        if parsed.path == "/api/admin/export.csv": return self.admin_export(parsed)
        return super().do_GET()

    def do_POST(self):
        if not PUBLIC_ENABLED:
            return self.unavailable()
        path = urlparse(self.path).path
        try:
            if path == "/api/leads": return self.create_lead()
            if path == "/api/client-access": return self.create_client_access()
            if re.fullmatch(r"/api/client-access/[0-9a-f-]+/questionnaire", path): return self.save_client_questionnaire(path.split("/")[3])
            if re.fullmatch(r"/api/leads/[0-9a-f-]+/consultation", path): return self.request_consultation(path.split("/")[3])
            if path == "/api/admin/login": return self.admin_login()
            if path == "/api/admin/logout": return self.admin_logout()
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        return self.send_json(404, {"error": "Risorsa non trovata"})

    def do_PATCH(self):
        if not PUBLIC_ENABLED:
            return self.unavailable()
        path = urlparse(self.path).path
        if re.fullmatch(r"/api/admin/leads/[0-9a-f-]+", path):
            try: return self.update_lead(path.rsplit("/", 1)[1])
            except (ValueError, json.JSONDecodeError) as exc: return self.send_json(400, {"error": str(exc)})
        return self.send_json(404, {"error": "Risorsa non trovata"})

    def do_DELETE(self):
        if not PUBLIC_ENABLED:
            return self.unavailable()
        path=urlparse(self.path).path
        if re.fullmatch(r"/api/admin/leads/[0-9a-f-]+",path):
            if not self.require_admin(csrf=True): return
            lead_id=path.rsplit("/",1)[1]
            with db() as conn: cur=conn.execute("DELETE FROM leads WHERE id=?",(lead_id,))
            if not cur.rowcount: return self.send_json(404,{"error":"Lead non trovato"})
            audit("lead_deleted",lead_id)
            return self.send_json(200,{"ok":True})
        return self.send_json(404,{"error":"Risorsa non trovata"})

    def create_lead(self):
        if not self.rate_ok("lead:" + self.client_address[0], 8, 600): return self.send_json(429, {"error": "Troppe richieste. Riprova più tardi."})
        data = self.json_body(); answers = data.get("answers") or {}; dimensions = data.get("dimensions") or {}
        name, phone = clean_text(data.get("name"), 100), clean_text(data.get("phone"), 30)
        province, email = clean_text(data.get("province"), 80), clean_text(data.get("email"), 160)
        if len(name) < 3 or len(re.sub(r"\D", "", phone)) < 9 or not province: raise ValueError("Dati di contatto incompleti")
        if not data.get("privacy_consent"): raise ValueError("Presa visione dell'informativa mancante")
        score = max(0, min(100, int(data.get("score", 0))))
        lead_id, token = str(uuid.uuid4()), secrets.token_urlsafe(24); created = now_iso()
        with db() as conn:
            conn.execute("""INSERT INTO leads (id,public_token_hash,created_at,updated_at,name,phone,email,province,score,score_band,privacy_consent,contact_consent,consent_at,source,answers_json,dimensions_json)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (lead_id,token_hash(token),created,created,name,phone,email,province,score,clean_text(data.get("score_band"),80),1,1 if data.get("contact_consent") else 0,created,clean_text(data.get("source"),40) or "website",json.dumps(answers,ensure_ascii=False),json.dumps(dimensions,ensure_ascii=False)))
        audit("lead_created",lead_id,province)
        threading.Thread(target=sync_brevo_contact,args=(lead_id,),daemon=True).start()
        threading.Thread(target=notify,args=(f"Nuovo lead Primoscore — {province}",f"{name}\n{phone}\nProvincia: {province}\nScore: {score}/100\nArea privata: /admin/"),daemon=True).start()
        return self.send_json(201, {"id": lead_id, "token": token})

    def create_client_access(self):
        """Registra un consenso esplicito dall'area clienti e lo sincronizza con Brevo."""
        if not self.rate_ok("client-access:" + self.client_address[0], 8, 600):
            return self.send_json(429, {"error": "Troppe richieste. Riprova più tardi."})
        data = self.json_body()
        first_name = clean_text(data.get("first_name"), 50)
        last_name = clean_text(data.get("last_name"), 50)
        name = clean_text(f"{first_name} {last_name}", 100)
        phone = clean_text(data.get("phone"), 30)
        email = clean_text(data.get("email"), 160)
        city = clean_text(data.get("city"), 80)
        if len(first_name) < 2 or len(last_name) < 2 or len(re.sub(r"\D", "", phone)) < 9 or not email or not city:
            raise ValueError("Dati di contatto incompleti")
        if not data.get("privacy_consent"):
            raise ValueError("Presa visione dell'informativa mancante")
        if not data.get("contact_consent"):
            raise ValueError("Il consenso al contatto è necessario per proseguire")
        lead_id, token = str(uuid.uuid4()), secrets.token_urlsafe(24)
        created = now_iso()
        with db() as conn:
            conn.execute("""INSERT INTO leads (id,public_token_hash,created_at,updated_at,name,phone,email,province,score,score_band,status,consultation_requested,consultation_requested_at,privacy_consent,contact_consent,consent_at,source,answers_json,dimensions_json)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (lead_id,token_hash(token),created,created,name,phone,email,city,0,"area_clienti","to_contact",1,created,1,1,created,"area_clienti",json.dumps({"city":city},ensure_ascii=False),"{}"))
        audit("client_access_created", lead_id, city)
        threading.Thread(target=sync_brevo_contact,args=(lead_id,),daemon=True).start()
        threading.Thread(target=notify,args=("Nuova richiesta consulenza gratuita — area clienti",f"{name}\n{phone}\nEmail: {email}\nCittà: {city}"),daemon=True).start()
        return self.send_json(201, {"id": lead_id, "token": token})

    def save_client_questionnaire(self, lead_id: str):
        """Salva il questionario dettagliato del cliente autenticato dal token di accesso."""
        data = self.json_body(); token = str(data.get("token", ""))
        answers = data.get("answers") or {}; dimensions = data.get("dimensions") or {}
        if not isinstance(answers, dict) or not isinstance(dimensions, dict):
            raise ValueError("Formato del questionario non valido")
        with db() as conn:
            row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not row or not hmac.compare_digest(row["public_token_hash"], token_hash(token)):
                return self.send_json(404, {"error": "Cliente non trovato"})
            if row["source"] != "area_clienti":
                return self.send_json(403, {"error": "Accesso non autorizzato"})
            score = max(0, min(100, int(data.get("score", 0))))
            conn.execute("UPDATE leads SET answers_json=?, dimensions_json=?, score=?, score_band=?, updated_at=? WHERE id=?", (json.dumps(answers, ensure_ascii=False),json.dumps(dimensions, ensure_ascii=False),score,clean_text(data.get("score_band"),80),now_iso(),lead_id))
        audit("client_questionnaire_saved", lead_id)
        threading.Thread(target=sync_brevo_contact,args=(lead_id,),daemon=True).start()
        return self.send_json(200, {"ok": True})

    def request_consultation(self, lead_id):
        data = self.json_body(); token = str(data.get("token", ""))
        with db() as conn:
            row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not row or not hmac.compare_digest(row["public_token_hash"], token_hash(token)): return self.send_json(404, {"error": "Lead non trovato"})
            if row["province"].strip().lower() != "genova": return self.send_json(403, {"error": "Servizio non disponibile per questa provincia"})
            when = now_iso(); conn.execute("UPDATE leads SET consultation_requested=1,consultation_requested_at=?,status='to_contact',updated_at=? WHERE id=?",(when,when,lead_id))
        audit("consultation_requested",lead_id)
        threading.Thread(target=sync_brevo_contact,args=(lead_id,),daemon=True).start()
        threading.Thread(target=notify,args=("Richiesta consulenza gratuita — priorità alta",f"{row['name']} ha richiesto il contatto gratuito.\nTelefono: {row['phone']}\nScore: {row['score']}/100"),daemon=True).start()
        return self.send_json(200, {"ok": True})

    def admin_login(self):
        if not self.rate_ok("login:" + self.client_address[0], 6, 900): return self.send_json(429, {"error": "Troppi tentativi. Attendi prima di riprovare."})
        data = self.json_body()
        if not hmac.compare_digest(clean_text(data.get("username"),50), ADMIN_USER) or not hmac.compare_digest(str(data.get("password", "")), ADMIN_PASSWORD):
            return self.send_json(401, {"error": "Credenziali non valide"})
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        sessions[sid] = {"csrf": csrf, "expires": time.time() + SESSION_TTL}
        secure = "; Secure" if os.getenv("PRIMOSCORE_HTTPS", "0") == "1" else ""
        cookie = f"ps_admin={sid}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}{secure}"
        return self.send_json(200, {"authenticated": True, "csrf": csrf, "user": ADMIN_USER}, {"Set-Cookie": cookie})

    def admin_logout(self):
        session = self.require_admin(csrf=True)
        if not session: return
        jar = cookies.SimpleCookie(self.headers.get("Cookie", "")); sid = jar.get("ps_admin")
        if sid: sessions.pop(sid.value, None)
        return self.send_json(200, {"ok": True}, {"Set-Cookie": "ps_admin=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})

    def query_leads(self, parsed):
        q = parse_qs(parsed.query); clauses=[]; params=[]
        if q.get("status", [""])[0] in STATUSES: clauses.append("status=?"); params.append(q["status"][0])
        province=clean_text(q.get("province",[""])[0],80)
        if province: clauses.append("province=?"); params.append(province)
        if q.get("consultation",[""])[0] == "1": clauses.append("consultation_requested=1")
        search=clean_text(q.get("search",[""])[0],80)
        if search: clauses.append("(name LIKE ? OR phone LIKE ? OR email LIKE ?)"); params += [f"%{search}%"]*3
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with db() as conn: return conn.execute("SELECT * FROM leads" + where + " ORDER BY consultation_requested DESC, created_at DESC LIMIT 1000", params).fetchall()

    def admin_leads(self, parsed):
        if not self.require_admin(): return
        rows=self.query_leads(parsed); items=[]
        for row in rows:
            item=dict(row); item["answers"]=json.loads(item.pop("answers_json")); item["dimensions"]=json.loads(item.pop("dimensions_json")); item.pop("public_token_hash",None); items.append(item)
        return self.send_json(200,{"leads":items,"count":len(items)})

    def update_lead(self, lead_id):
        if not self.require_admin(csrf=True): return
        data=self.json_body(); sets=[]; params=[]
        if "status" in data:
            if data["status"] not in STATUSES: raise ValueError("Stato non valido")
            sets.append("status=?"); params.append(data["status"])
            if data["status"] in {"contacted","appointment","closed"}: sets.append("last_contact_at=?"); params.append(now_iso())
        if "notes" in data: sets.append("notes=?"); params.append(clean_text(data["notes"],4000))
        if not sets: raise ValueError("Nessuna modifica")
        sets.append("updated_at=?"); params.append(now_iso()); params.append(lead_id)
        with db() as conn: cur=conn.execute(f"UPDATE leads SET {','.join(sets)} WHERE id=?",params)
        if not cur.rowcount: return self.send_json(404,{"error":"Lead non trovato"})
        audit("lead_updated",lead_id,",".join(data.keys()))
        return self.send_json(200,{"ok":True})

    def admin_export(self, parsed):
        if not self.require_admin(): return
        rows=self.query_leads(parsed); out=io.StringIO(); writer=csv.writer(out)
        writer.writerow(["Data","Nome","Telefono","Email","Provincia","Score","Fascia","Consulenza","Stato","Note"])
        for r in rows: writer.writerow([r["created_at"],r["name"],r["phone"],r["email"],r["province"],r["score"],r["score_band"],"Sì" if r["consultation_requested"] else "No",r["status"],r["notes"]])
        raw=out.getvalue().encode("utf-8-sig"); self.send_response(200); self.send_header("Content-Type","text/csv; charset=utf-8"); self.send_header("Content-Disposition",'attachment; filename="primoscore-lead.csv"'); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)


if __name__ == "__main__":
    removed=cleanup_expired()
    if removed: print(f"Lead scaduti eliminati: {removed}")
    print(f"Primoscore: http://{HOST}:{PORT}")
    print(f"Area lead: http://{HOST}:{PORT}/admin/")
    print(f"Utente: {ADMIN_USER}")
    if not os.getenv("PRIMOSCORE_ADMIN_PASSWORD"): print(f"Password locale: {ADMIN_PASSWORD} (salvata in {PASSWORD_FILE})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
