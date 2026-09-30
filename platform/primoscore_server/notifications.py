"""Minimal operational notices: no customer identity, answers or score in email."""
import json
import smtplib
from .repository import new_id

def queue(auth,c,tenant,client,event,kind,*,customer=False):
    role='customer' if customer else 'consultant'
    rows=c.execute('SELECT a.id,a.email FROM accounts a JOIN auth_credentials x ON x.account_id=a.id WHERE a.tenant_id=? AND a.role=? AND a.status=? AND x.email_verified=1'+(' AND a.client_id=?' if customer else ''),[tenant,role,'active',*([client] if customer else [])]).fetchall()
    for row in rows:
        payload={'purpose':kind,'to':row['email'],'url':auth.origin+('/cliente/appuntamento' if customer else '/consulente/clienti')}
        c.execute('INSERT OR IGNORE INTO service_mail(id,tenant_id,client_id,event_key,payload_encrypted,created_at) VALUES(?,?,?,?,?,?)',(new_id(),tenant,client,event+':'+row['id'],auth.cipher.encrypt(json.dumps(payload).encode()).decode(),auth.timestamp()))

def send_operational(auth,mailer,limit=20):
    with auth.db.transaction() as c:
        ids=[r[0] for r in c.execute("SELECT id FROM service_mail WHERE status='queued' AND attempts<3 ORDER BY created_at LIMIT ?",(limit,))]
    sent=failed=cancelled=0
    for ident in ids:
        with auth.db.transaction() as c:
            row=c.execute("SELECT * FROM service_mail WHERE id=? AND status='queued'",(ident,)).fetchone()
            if not row:continue
            active=c.execute("SELECT 1 FROM tenants WHERE id=? AND status='active'",(row['tenant_id'],)).fetchone()
            erased=c.execute('SELECT 1 FROM privacy_erasures WHERE tenant_id=? AND client_id=?',(row['tenant_id'],row['client_id'])).fetchone()
            if not active or erased:
                c.execute("UPDATE service_mail SET status='cancelled' WHERE id=?",(ident,));cancelled+=1;continue
            payload=json.loads(auth.cipher.decrypt(row['payload_encrypted'].encode()))
            # A suspended account or changed address must not receive a queued notice.
            recipient=c.execute("SELECT 1 FROM accounts a JOIN auth_credentials x ON x.account_id=a.id WHERE a.tenant_id=? AND a.email=? AND a.status='active' AND x.email_verified=1",(row['tenant_id'],payload['to'])).fetchone()
            if not recipient:
                c.execute("UPDATE service_mail SET status='cancelled' WHERE id=?",(ident,));cancelled+=1;continue
            c.execute("UPDATE service_mail SET status='sending',attempts=attempts+1 WHERE id=?",(ident,))
        try:mailer.send(payload)
        except (OSError,smtplib.SMTPException):
            with auth.db.transaction() as c:c.execute("UPDATE service_mail SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END WHERE id=?",(ident,))
            failed+=1
        else:
            with auth.db.transaction() as c:c.execute("UPDATE service_mail SET status='sent' WHERE id=?",(ident,))
            sent+=1
    return dict(sent=sent,failed=failed,cancelled=cancelled)

LABELS={'contact':'Nuova richiesta nel tuo studio','booking':'Appuntamento confermato','appointment_changed':'Appuntamento aggiornato'}
