"""Authorized lifecycle operations with tenant scope and audit records."""
import json
import sqlite3
import shutil
import time
from .auth import AuthError,consultant_profile,email_address,digest
from .repository import new_id,view
from .database import now
from .workspace import Workspace,string,integer

class Operations:
    def __init__(self,auth):self.auth=auth;self.db=auth.db;self.workspace=Workspace(auth)

    def status(self,token,d):
        if not isinstance(d,dict) or set(d)!={'tenant_id','status','reason'} or d['status'] not in ('active','suspended'):raise AuthError('Richiesta non valida.')
        reason=string(d['reason'],'motivo',300,True)
        with self.db.transaction() as c:
            actor,_=self.auth._resolve(c,token,full=True)
            if actor['role']!='master':raise AuthError('Operazione Master richiesta.',403)
            tenant=c.execute('SELECT * FROM tenants WHERE id=?',(d['tenant_id'],)).fetchone()
            if not tenant or tenant['status']=='draft':raise AuthError('Completa prima la normale attivazione dello studio.')
            if tenant['status']==d['status']:return {'ok':True}
            c.execute('UPDATE tenants SET status=? WHERE id=?',(d['status'],tenant['id']))
            # Account status is preserved: an individually suspended user must not be reactivated.
            ids=[r[0] for r in c.execute('SELECT id FROM accounts WHERE tenant_id=?',(tenant['id'],))]
            if d['status']=='suspended':
                for ident in ids:
                    for table in ('auth_sessions','auth_steps','customer_guest_sessions','customer_report_deliveries'):
                        c.execute('DELETE FROM '+table+' WHERE account_id=?',(ident,))
                    c.execute('UPDATE auth_tokens SET used_at=? WHERE account_id=? AND used_at IS NULL',(self.auth.timestamp(),ident))
            self.workspace._audit(c,actor,tenant['id'],reason,'studio_'+d['status'])
            return {'ok':True}

    def profile(self,token,d):
        if not isinstance(d,dict) or 'revision' not in d:raise AuthError('Profilo non valido.')
        d=dict(d);revision=integer(d.pop('revision'))
        with self.db.transaction() as c:
            actor,_=self.auth._resolve(c,token,full=True)
            if actor['role']!='consultant':raise AuthError('Operazione riservata al consulente.',403)
            old=c.execute('SELECT * FROM consultant_profiles WHERE tenant_id=?',(actor['tenant_id'],)).fetchone()
            if old['profile_revision']!=revision:raise AuthError('Profilo aggiornato in un’altra scheda. Ricarica.',409)
            # The controller belongs to the studio privacy settings.  A stale
            # profile form must never overwrite a newer privacy notice.
            d['controller_name']=old['controller_name'];d['controller_email']=old['controller_email'];d['controller_dpo']=old['controller_dpo']
            clean=consultant_profile(d)
            if clean['email']!=actor['email']:raise AuthError('Per cambiare email usa il comando con verifica del nuovo indirizzo.')
            c.execute('UPDATE consultant_profiles SET '+','.join(k+'=?' for k in clean)+',profile_revision=profile_revision+1 WHERE tenant_id=?',(*clean.values(),actor['tenant_id']))
            self.workspace._audit(c,actor,actor['tenant_id'],'','profile_updated')
            return {'ok':True}

    def email(self,token,d):
        if not isinstance(d,dict) or set(d)!={'email'}:raise AuthError('Richiesta non valida.')
        email=email_address(d['email'])
        user=self.auth.identity(token,full=True);self.auth.rate('change-email',user['id'],3,3600)
        with self.db.transaction() as c:
            actor,_=self.auth._resolve(c,token,full=True)
            if actor['role']!='consultant':raise AuthError('Operazione riservata al consulente.',403)
            if email==actor['email']:raise AuthError('Questo è già il tuo indirizzo verificato.')
            if c.execute("SELECT 1 FROM accounts WHERE role='consultant' AND email=?",(email,)).fetchone():raise AuthError('Il nuovo indirizzo non è disponibile.')
            token_value=self.auth._mail_token(c,actor,'verify')
            c.execute('INSERT INTO email_changes VALUES(?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET token_hash=excluded.token_hash,new_email=excluded.new_email,expires_at=excluded.expires_at',(actor['id'],digest(token_value),email,self.auth.timestamp()+86400))
            row=c.execute('SELECT id,payload_encrypted FROM auth_mail WHERE account_id=? ORDER BY rowid DESC LIMIT 1',(actor['id'],)).fetchone()
            payload=json.loads(self.auth.cipher.decrypt(row['payload_encrypted'].encode()));payload['to']=email
            c.execute('UPDATE auth_mail SET payload_encrypted=? WHERE id=?',(self.auth.cipher.encrypt(json.dumps(payload).encode()).decode(),row['id']))
            self.workspace._audit(c,actor,actor['tenant_id'],'','email_change_requested')
            return {'ok':True,'message':'Conferma il collegamento inviato al nuovo indirizzo. Fino ad allora resta valido quello attuale.'}

    def export(self,token,tenant=None,reason=''):
        with self.db.transaction() as c:
            actor,t,reason=self.workspace._scope(c,token,tenant,reason)
            tables=('consultant_profiles','cities','partners','voucher_lots','campaigns','deliveries','activities','print_runs','clients','questionnaires','assessments','customer_intakes','customer_events','booking_slots','appointments','customer_bookings','customer_settings','privacy_erasures','crm_leads','crm_events','crm_drafts','crm_rules')
            result={'format':'primoscore-studio-export-1','created_at':now(),'data':{table:[view(r) for r in c.execute('SELECT * FROM '+table+' WHERE tenant_id=?',(t,))] for table in tables}}
            self.workspace._audit(c,actor,t,reason,'export_complete_studio')
            return result

    def erase(self,token,d,tenant=None,reason=''):
        if not isinstance(d,dict) or set(d)!={'client_id','revision','confirmation'} or d['confirmation']!='ANONIMIZZA':raise AuthError('Conferma esplicitamente l’anonimizzazione dopo la verifica degli obblighi dello studio.')
        with self.db.transaction() as c:
            actor,t,reason=self.workspace._scope(c,token,tenant,reason)
            client=self.workspace._get(c,t,'clients',d['client_id']);self.workspace._rev(client,d)
            c.execute('INSERT OR IGNORE INTO privacy_erasures VALUES(?,?,?,?)',(t,client['id'],actor['id'],now()))
            anonymize(c,t,client['id'],self.auth.timestamp())
            self.workspace._audit(c,actor,t,'Richiesta verificata dallo studio; nessun dato personale nel motivo','privacy_anonymized',client['id'])
            return {'ok':True}

    def health(self,token):
        user=self.auth.identity(token,full=True)
        if user['role']!='master':raise AuthError('Operazione Master richiesta.',403)
        with self.db.transaction() as c:
            copies=list((self.db.path.parent/'backups').glob('primoscore-*.sqlite3'))
            backup_age=None if not copies else max(0,int(time.time()-max(p.stat().st_mtime for p in copies)))
            return {'backup_age_seconds':backup_age,'backup_count':len(copies),'disk_free_bytes':shutil.disk_usage(self.db.path.parent).free,'mail':{table:{r[0]:r[1] for r in c.execute('SELECT status,count(*) FROM '+table+' GROUP BY status')} for table in ('auth_mail','service_mail')}}

    def retry_mail(self,token):
        user=self.auth.identity(token,full=True)
        if user['role']!='master':raise AuthError('Operazione Master richiesta.',403)
        self.auth.rate('retry-mail',user['id'],3,3600)
        with self.db.transaction() as c:
            for table in ('auth_mail','service_mail'):c.execute('UPDATE '+table+" SET status='queued',attempts=0 WHERE status='failed'")
            self.auth._event(c,user['id'],'failed_mail_retried')
        return {'ok':True}

def anonymize(c,t,ident,timestamp):
    """Idempotent purge, also used when reapplying the erasure ledger after restore."""
    for row in c.execute('SELECT id FROM accounts WHERE tenant_id=? AND client_id=?',(t,ident)).fetchall():
        account=row[0]
        for table in ('auth_sessions','auth_steps','auth_recovery_codes','auth_tokens','auth_mail','customer_guest_sessions','customer_report_deliveries'):
            c.execute('DELETE FROM '+table+' WHERE account_id=?',(account,))
        c.execute('UPDATE auth_credentials SET password_hash=NULL,email_verified=0,totp_encrypted=NULL WHERE account_id=?',(account,))
        c.execute("UPDATE accounts SET email=?,status='suspended' WHERE id=?",('erased-'+account+'@example.invalid',account))
    for table in ('crm_leads','crm_events','crm_drafts'):
        c.execute('DELETE FROM '+table+' WHERE tenant_id=? AND client_id=?',(t,ident))
    c.execute('DELETE FROM service_mail WHERE tenant_id=? AND client_id=?',(t,ident))
    c.execute("UPDATE clients SET first_name='Contatto anonimizzato',last_name='',email='',mobile='',residence_city='',stage='archived',revision=revision+1 WHERE tenant_id=? AND id=?",(t,ident))
    c.execute("UPDATE questionnaires SET answers_json='{}',current_step=0,revision=revision+1 WHERE tenant_id=? AND client_id=?",(t,ident))
    c.execute("UPDATE assessments SET answers_json='{}',result_json='{}' WHERE tenant_id=? AND client_id=?",(t,ident))
    c.execute("UPDATE customer_intakes SET partner_label='',fingerprint='' WHERE tenant_id=? AND client_id=?",(t,ident))
    c.execute("UPDATE customer_bookings SET note='' WHERE tenant_id=? AND appointment_id IN (SELECT id FROM appointments WHERE tenant_id=? AND client_id=?)",(t,t,ident))
    c.execute("UPDATE appointments SET status='cancelled',revision=revision+1 WHERE tenant_id=? AND client_id=? AND status='confirmed'",(t,ident))
    c.execute("UPDATE outbox SET payload_json='{}',status='cancelled' WHERE tenant_id=? AND client_id=?",(t,ident))
